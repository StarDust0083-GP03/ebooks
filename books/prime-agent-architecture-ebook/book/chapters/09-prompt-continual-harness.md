# 分层系统提示词与 Continual Harness 自我改进

做过长程 Agent 成本优化的人，对 Prompt Caching（提示词前缀缓存）的敏感度都很高。一次 20 轮的会话，只要系统提示词第 10 行变了一个时间戳或者塞进一条动态记忆，后面几万 Token 的前缀缓存就会整段失效，输入费用瞬间翻好几倍，首字延迟跟着拉长。

可另一方面，Prime Agent 的卖点恰恰是“持续脚手架（Continual Harness）”：Agent 跑着跑着发现这个项目必须用某个特定测试命令，或者总结出一套更好用的子 Agent 分工策略，希望立刻沉淀下来，下一轮就生效。

一边要求前缀字节级纹丝不动，一边要求行为策略在运行中持续演化。Prime Agent 在 `crates/pa-core/src/prompts/` 和 `crates/pa-core/src/refinement/` 里给出的解法非常干净：**严格分层，动静分离，把自我改进完全移出基础系统提示词**。

![分层系统提示词与 Continual Harness 精炼流程](../../wiki/assets/diagrams/prompt-continual-harness.svg)

图里最关键的设计切口在中间那条琥珀色框：`[harness-digest]` 不拼进系统提示词，而是以一条独立用户消息的形式在冷边界或状态变更时追加进对话流。基础系统提示词永远不被 `/refine` 改写，前缀缓存和持续进化因此不再打架。

## 静态前缀与动态尾部：用 `cached_prefix_len` 钉死边界

打开 `crates/pa-core/src/prompts/system_prompt.rs`，整个系统提示词组装函数 `system_prompt_breakdown()` 返回的不只是最终字符串，而是一个带完整血缘信息的 `SystemPromptBreakdown` 结构，里面每个切片 `PromptSegment` 都明确标注了 `SegmentKind::Static` 或 `SegmentKind::Dynamic`，并计算出精确的字节边界 `cached_prefix_len`。

**静态前缀（`SegmentKind::Static`）**只由两类输入决定：用户是否通过 `--system-prompt` 传入了完整替换文本，以及当前选中模型匹配到的模型专属补丁。默认情况下，按固定顺序拼接四个文件：

1. `core`（`prompts/layers/core.md`）：定义唯一的 `python_repl` 工具和全部程序化工具、子 Agent、信箱、压缩、目标、心跳和 MCP 的完整 API 契约。
2. `usage`（`prompts/layers/usage.md`）：强制执行规约，例如禁止直接调 `subprocess`、必须用 `edit` 精确匹配、读文件要赋给命名变量、禁止空跑 `pass` 占位、禁止 `sleep` 轮询以及不要往内核里硬装目标项目的第三方依赖。
3. `opinionated`（`prompts/layers/opinionated.md`）：工作流偏好与沟通风格准则。
4. `model-prompts`（`prompts/layers/model_prompts.toml`）：按模型名通配规则挂载的定向纠偏文件。比如当前仓库里就有一条真实规则：`glm-5.3*` 自动追加 `bash-over-subprocess.md`，专门纠偏特定模型爱调 `subprocess` 的习惯；用户也可以在 `<agent_dir>/model-prompts.toml` 里维护自己的映射表（路径校验会拒绝绝对路径和 `..` 穿越）。

只要自定义提示和模型规则不变，`assembled[..cached_prefix_len]` 在不同会话之间严格字节级一致。

**动态尾部（`SegmentKind::Dynamic`）**则按固定顺序收集所有会话级上下文：

1. `packages`：内核预装 Python 包列表，并明确提示这是一个 `uv` 管理的虚拟环境，没有内置 `pip` 模块，装包要用 `uv pip install <pkg>`。
2. `project-context`：自动发现的各层级 `AGENTS.md` 项目指令。
3. `skills-inventory`：去掉标记为 `disable_model_invocation` 后的可见技能清单（标明哪些有 `python_import` 可直接调用，哪些是磁盘文档）。
4. `mcp-servers`：已启用的通用 MCP 服务指引。
5. `environment`：当前工作目录、对话日志路径、模型输入模态（如图像/视频支持）、是否无守护进程、是否启用信箱等环境事实。
6. `session-role`：当前会话的 RLM 递归深度、是否为工厂状态子节点以及派生权限。
7. `appended-prompt`：发现的 `APPEND_SYSTEM.md` 与 `--append-system-prompt` 追加内容。

想检查某个模型到底拿到了一份怎样的系统提示词，不用翻代码猜，直接跑一条 `prime-agent prompt --model <selector>` 就能把完整提示词和每一层的来源、字节分段打印出来（`.changes/sebastian-prompt-builder-facts.md`）。

## 五类持久条目与双作用域并发锁

Continual Harness 的底层存储由 Rust（`crates/pa-core/src/refinement/mod.rs`）和 Python（`prime-agent-runtime/src/rlm/harness.py`）双向读写同一份 JSON 文档 `harness_state.json`。

它刻意保持了很小的领域模型，只存五类条目（`REFINEMENT_KINDS`）：

- `prompt`：窄范围的行为补充说明（默认路径 `policy`），绝不取代静态基础提示词；
- `memory`：跨压缩保留的事实、项目约定与用户偏好（默认路径 `general`）；
- `skill`：可复用的 Python 技能描述，写入时强制校验 `reference` 必须满足 `{"type": "python"}` 且带合法导入名与调用模式（`_validate_python_skill_reference`）；
- `subagent`：可复用的子 Agent 角色规格；
- `factory`：声明式状态机或 DAG 工厂规格（受 `factory.enabled` 门控）。

每条 `HarnessEntry` 都带递增版本号 `version`、来源标记 `source`（`agent` 或 `refine`）以及起止时间戳，外加一份独立的 `refinements` 精炼事件列表与审计日志 `refinement_history.jsonl`。

作用域默认严格隔离：本地存储位于 `<session-artifact-dir>/harness/harness_state.json`，仅影响当前会话；只有显式传入 `global_=True` 时，才写入跨会话共享的 `~/.prime/agent/harness/harness_state.json`。如果在 `--no-session` 这类没有持久化目录的临时会话里访问 `rlm.harness`，Python 侧的 `_HarnessProxy` 不会把内核弄崩，而是降级为空读视图并在本地写入时抛出明确错误提示改用 `global_=True`。

因为 Rust 宿主和 Python 内核可能并发读写同一份文件，两边实现了一致的防踩踏细节：Rust 使用 `BTreeMap` 保序序列化防止文件无意义抖动；加锁和替换前两边都会沿符号链接解析真实目标文件（`resolved_state_file()`，最多跟踪 40 层防循环链接），避免锁住软链接别名却绕开了内核持有的真锁。

## 相关性打分、`[harness-digest]` 指纹与 `/refine` 触发链路

持久条目攒多了，全部倾倒给模型又会变成新的上下文负担。Prime Agent 在 `crates/pa-core/src/session_engine/harness_digest.rs` 里做了一套轻量但很考究的动态摘要机制：

1. **多语言词项提取与加权排序**。`digest_query_terms()` 从当前活跃目标（最高权重 `3.0`）和最近 4 条用户/助手消息（权重从 `2.0` 逐条递减至 `1.0`）里提取最多 48 个检索词项。Python 侧的 `rlm.harness.search()` 与 Rust 侧采用同一套跨语种分词规则：拉丁字母按至少 3 字符词干切分，非 ASCII 非 CJK 语种按 2 字符起步，而没有空格分隔的中日韩文字（CJK）自动按边界与相邻拉丁词切开，并展开成重叠二元组（例如 `修复登录` 展开为 `修复`、`复登`、`登录`），即使条目里写的是 `登录故障` 也能稳定召回。
2. **指纹防重复投递**。渲染出 `<harness_state>` 摘要的同时，宿主会计算一份不含查询词的纯状态指纹 `state_fingerprint`。`[harness-digest]` 作为一条 `display: false` 的自定义消息，在冷启动、恢复、压缩边界或状态指纹确实变化时追加进上下文。如果记忆内容根本没变，绝不重复追加冗余行；而且即使运行中途完成了精炼，也只是向后追加新行而不篡改历史消息前缀，最大限度保住已有的前缀缓存。
3. **后台非阻塞精炼与回滚**。用户运行 `/refine`、模型调用 `await refine.run(instructions=...)`，或者在上下文压缩之后触发自动精炼（`auto_refine_trigger.rs`），精炼都不会打断正在执行的 Python 单元格，而是排队到当前回合结束后的空闲边界执行。后台规划器调用辅助模型审阅最近轨迹，提出小步、有证据支撑的增量修改，写入 `harness_state.json` 和 `refinement_history.jsonl`，并在必要时支持按快照回滚。如果会话在规划中途被销毁或切换分支，后台任务立即取消；但一旦进入写盘提交阶段，会保证完成审计与落盘，不留下半成品状态。当然，用户也可以随时在全局 `/settings` 里把 `harnessRefinements` 设为 `false`，一键彻底关闭精炼写入。

有了前缀稳定、经验可沉淀的提示体系，剩下的问题就是让 Agent 在长达数小时的运行里既不把上下文撑爆，又能在遇到失败时按预算坚持推进。下一章我们拆解自动压缩、持久目标、心跳调度和自主质量门禁。
