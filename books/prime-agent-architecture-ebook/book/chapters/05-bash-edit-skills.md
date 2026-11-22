# 单工具收口下的程序化工具链：`bash`、`edit` 与可执行技能

如果只看模型工具列表里唯一的 `python_repl`，很容易误以为 Prime Agent 让模型天天在 Python 里自己调 `subprocess.run()` 和 `open().write()`。真这么干的话，两分钟就能遇到一堆麻烦：一个阻塞的 `subprocess.run(["cargo", "test"])` 会把整个 CPython 事件循环卡死，终端界面看不到任何实时进度，用户按中断也停不掉孤儿进程；整文件覆写则动不动把几百行无关代码改丢，连最小 diff 都展示不出来。

Prime Agent 在 `crates/pa-core/src/prompts/layers/usage.md` 里明令禁止模型直接用 `subprocess` 或 `os.system`，同时把日常高频操作做成了两层程序化工具：由运行时直接内建的 `bash()`，以及作为可执行 Python 技能预导入内核的 `edit` 与其余 13 个内置技能。

## `bash()`：默认异步、1 秒有界等待与跨重启接管

`prime-agent-runtime/src/rlm/bash.py`（1,843 行）是整个 Python 运行时里最贴近操作系统细节的模块。调用 `bash("cargo test")` 时，它是同步函数，立刻派生子进程并返回 `BashHandle`，不阻塞当前语句。每次 `bash()` 都是全新进程，壳层变量不会跨调用保留，但因为遵循 POSIX 进程继承，模型在 Python 里做的 `os.chdir(...)` 和 `os.environ[...]` 都会自然传给后续的 `bash()` 子进程（反之不行）。

这套封装在三个地方做得非常克制且扎实：

**1 秒有界等待自动降级**，很多模型习惯顺手写 `r = await bash("pytest")`。如果在有守护进程的会话里跑一个耗时 10 分钟的构建，一直 `await` 会直接撞上 60 秒单元格超时。`bash.py` 第 66 行的处理方式很巧：内核引导阶段会检查宿主是否注册了 `bash.completed` 处理器，只要注册了，就把 `_completion_notices_wired` 置为 `True`。此后 `await bash(...)` 走 `_bounded_wait(..., bound=1.0)`（`_YIELD_AFTER_SECONDS = 1.0`）。快命令 1 秒内跑完直接返回 `BashResult`；超过 1 秒还没结束时，它绝不取消底层命令，而是立刻返回一个实时快照对象（`r.running` 为 `True`、`r.output` 带着当前已收集输出、`r.handle` 指向活句柄），并在前 3 次降级时往 `stderr` 打一行教学提示（`_MAX_DEGRADE_HINTS = 3`），提醒模型可以结束本回合，等命令跑完后的 `[bash-done]` 通知再回来。如果运行在没有通知机制的无守护进程模式里，等待保持无界，防止模型结束回合后再也没人唤醒它。

**兼容保守 POSIX Shell 的底层信道**，`bash.py` 第 41 行把子进程输出和完成状态通道固定在单数字文件描述符 `_OUTPUT_FD = 8` 和 `_STATUS_FD = 9`，源码注释点明了原因：`dash` 等 POSIX 壳层只在语法上保证支持单数字 fd 重定向。同时单次命令输出有独立缓冲上限（头部 `_HEAD_CAP = 512 KiB`，尾部 `_TAIL_CAP = 1.5 MiB`），完成标记使用 `\x1eprime-agent-complete:` 与 `\x1f` 包裹；取消等待时先发 `SIGTERM` 给进程组，留 0.5 秒宽限再发 `SIGKILL`（Windows 侧借助 `_winjob.py` 的 Job Object 与 `taskkill /T` 清理进程树）。每次调用还会通过 `application/vnd.prime-agent.bash-command+json` MIME 帧向宿主上报命令摘要，供 TUI 工具卡片展示。

**`persistent=True` 跨重启持久进程**，对于开发服务器、长时间训练脚本这类必须活过内核、Worker 甚至守护进程重启的任务，`bash(cmd, persistent=True)`（仅限 POSIX 下的已保存会话）把进程挂到独立持久化目录里。即便会话重启甚至被归档删除，该进程依然继续运行，直到自己退出或显式被 `kill()`。重启后的新内核调用 `from rlm import persistent_shells; persistent_shells()` 就能把句柄重新认领回来；在离线期间已经跑完的持久进程会被列出一次已完成状态，而重新接管后的运行中进程通过轮询检测结束，因此结果通知约有 1 秒延迟。

## `edit`：精确唯一匹配与原生 Diff 回传

在 `skills/edit/SKILL.md` 和 `crates/pa-core/src/prompts/layers/usage.md` 里，修改已有文件的唯一推荐入口是 `await edit(path=..., old_str=..., new_str=...)`（`edit` 模块经由 `rlm/skill.py` 包装为可直接调用的异步对象，等价于 `edit.run(...)`）。

它的设计哲学和许多基于行号打补丁的编辑器截然不同，只抓两条铁律：

1. **精确唯一匹配**：`old_str` 在目标文件里必须不多不少刚好出现一次。匹配不到或者命中多处，直接抛出异常要求模型扩大上下文切片，绝不做模糊猜测，从根源上避免改错同名函数。
2. **结构化 Diff 事件直达界面**：改动落盘后，`edit` 技能不是只返回一句字符串，还会调用 `rlm.emit()` 发出 MIME 类型为 `application/vnd.prime-agent.diff+json` 的显示载荷（定义见 `crates/pa-core/src/kernel/shared.rs` 第 61 行）。Rust 宿主收集这些载荷，通过 `crates/pa-core/src/tools/ipython/diff/` 计算带 4 行上下文的统一差异，写进 `ToolResult` 的 `details.diffs`，TUI 和 HTML 导出器就能直接渲染彩色行级 Diff，而不是让用户在纯文本里人眼找改动。

因为模型身处 Python REPL，`edit` 还能和变量天然配合：先在 Python 里读入文件并定位起止切片赋给 `old`，用字符串操作构造 `new`，再调 `await edit(path=..., old_str=old, new_str=new)`。如果代码片段里本身包含三双引号，直接用三单引号变量规避转义灾难。

## 14 个内置技能与用户/仓库信任边界

在仓库的 `skills/` 目录下，v0.10.5 一共随行打包了 14 个技能包，覆盖文件编辑、媒体加载、多 Agent 协作、长程调度和自我改进：

| 技能目录 | 形态 | 核心用途 |
| --- | --- | --- |
| `edit` | 可执行 Python 技能 | 精确唯一字符串替换并推送 diff 载荷 |
| `attach-image` | 可执行 Python 技能 | 校验视觉模型能力并把 JPEG/PNG/GIF/WebP 载入上下文 |
| `attach-video` | 可执行 Python 技能 | 载入单文件上限 7.5 MB 的 MP4/WebM 视频片段（面向声明 `video` 输入的 OpenAI 兼容接口） |
| `agent-message` | 可执行 Python 技能 | 核家族内点对点发消息或广播（`agent_message.send`） |
| `agent-observe` | 可执行 Python 技能 | 查看家族名册、单节点状态与最近消息预览 |
| `compact` | 可执行 Python 技能 | 查询上下文占用并预约在回合边界执行压缩 |
| `goal` | 可执行 Python 技能 | 创建、查询和完结当前会话的持久目标 |
| `refine` | 可执行 Python 技能 | 预约在回合结束时执行 Continual Harness 精炼 |
| `rlm-heartbeat` | 可执行 Python 技能 | 创建、暂停、恢复和删除周期性自唤醒心跳 |
| `websearch` | 可执行 Python 技能 | 借助宿主管理的 API Key 发起网页检索 |
| `factory` | Markdown 技能 | 指导使用 `rlm.factory` 状态机编排 |
| `mcp` | Markdown 技能 | 指导发现与调用 MCP 工具服务 |
| `prime-intellect` | Markdown 技能 + 参考文档 | Prime Intellect 算力、环境、推理与沙箱文档索引 |
| `skill-creator` | Markdown 技能 + Python 技能规范 | 指导编写和打包新的自定义技能 |

这 14 个技能里藏着一条非常硬的安全边界。打开 `crates/pa-core/src/skills/mod.rs` 第 142 行的 `get_python_skill_runtime_info()`：

```rust
pub fn get_python_skill_runtime_info(skills: &[Skill]) -> Vec<PythonSkillRuntimeInfo> {
    skills
        .iter()
        .filter(|skill| skill.is_python() && skill.source_info.scope != SourceScope::Project)
        ...
}
```

这一行过滤器明确划出了一条红线：只有内置技能和用户主动安装进个人配置目录的 Python 技能，才会在内核引导时自动安装并预导入进 REPL；任何来自当前代码仓库（`SourceScope::Project`）的技能，哪怕目录里放了 `pyproject.toml` 和 Python 源码，宿主也**绝不会自动导入内核执行**，只把它的 `SKILL.md` 当成普通文档路径放进提示词清单。这样当你 `cd` 进一个陌生开源仓库启动 `prime-agent` 时，恶意仓库无法借技能发现机制在第一回合前偷偷注入启动代码。

另外，资源解析系统（`crates/pa-core/src/packages/`）还支持通过 `npm`、`git` 或本地路径安装外部技能包，并按明确优先级完成资源覆盖合并。至于模型怎么知道每个技能的接口签名？预导入的技能模块可以直接在 REPL 里用 `help(<skill>)`、`dir(<skill>)` 或 `inspect.signature(...)` 自省，不需要把几十页 API 文档全硬塞进系统提示词里占 Token。

单会话里的命令、编辑和技能都已齐备，但遇到大型调研或并行改造时，单个上下文窗口仍然太窄。下一章我们看 Prime Agent 怎样把一个会话裂变成一棵受控的递归子 Agent 树。
