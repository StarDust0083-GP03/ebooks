# 安全边界、成本账本与可运行教学模型

任何能执行任意 Shell 命令、运行生成代码并联网调用外部服务的编程 Agent，在生产团队落地前都必须回答两个最尖锐的问题：它到底能不能防住恶意代码和提示词注入？跑上一整夜的费用和失控风险怎么封顶？

拆完 Prime Agent 的全部源码后，最务实的一点在于它把**哪些防线是真的、哪些地方绝不是沙箱**写得清清楚楚。

## 真实安全边界：进程隔离不等于安全沙箱

先看上游在 `README.md` 首页挂出的醒目警告：

> Prime Agent executes model-generated Python and project commands with your user permissions. Its worker and kernel processes improve lifecycle isolation and recovery; they are **not** a security sandbox.

把整个代码库里的防御措施按真实强制力排开，界限一目了然：

**第一层，语言内存安全与输入防伪造（硬约束）**，全工作区通过 `Cargo.toml` 启用 `unsafe_code = "forbid"`，从编译期消除了 Rust 宿主的内存破坏面。线协议和队列层严格拦截客户端伪造守护进程内部保留类型（`inbox_notice`、`rlm_child_terminal_notice`、`rlm_child_failure` 以及 `inbox:arrival`/`inbox:reminder` 合并键）；`InboxReadFilter` 用 `deny_unknown_fields` 防止参数拼写错误导致误清空信箱；`agent_message` 的发送方身份由守护进程强制派生，模型无法冒名顶替。

**第二层，代码自动导入与凭据隔离（硬约束）**，`crates/pa-core/src/skills/mod.rs` 第 145 行直接阻断 `SourceScope::Project` 作用域的 Python 技能自动导入内核，防止打开一个不可信仓库就在对话开始前悄悄执行恶意代码。MCP 认证与 OAuth 令牌由 Rust 宿主保管，内核目录接口不返回原始凭据；stdio MCP 子进程仅继承 `_SAFE_ENV` 七个基础环境变量，错误输出经过正则脱敏。

**第三层，外部工具探测加固（尽力收敛，存在已知残余）**，自主模式的质量门禁在跑 Git 工作树快照时，通过命令行覆盖禁用了 `fsmonitor`、索引写钩子、promisor 懒拉取、外部 diff 与 textconv 转换器及子模块读取；但如 `crates/pa-core/src/autonomous/gates.rs` 注释所坦言，`.gitattributes` 中的 `clean` 与 `process` 过滤器无法通过 Git 标志禁用，处理完全不可信的仓库时仍是攻击面。

**第四层，用户态命令与代码执行（无沙箱）**，一旦你同意让 Agent 在本地目录干活，模型在 `python_repl` 里运行的 Python 代码和 `bash()` 启动的 Shell 命令，都直接持有你当前操作系统用户的全部读写、网络与凭据权限。再加上 `bash(..., persistent=True)` 创建的进程在会话删除后仍可作为孤儿继续运行，如果面对不信任的外部代码库、不可信提示词或第三方指令，唯一稳妥的办法是把整个 `prime-agent` 放进外部容器、独立虚拟机或云端沙箱里运行，并使用可随时丢弃还原的 Git 工作树。

## 模型路由、缓存定价与成本账本

长程任务里，钱怎么花出去往往比代码怎么写更隐蔽。Prime Agent 在费用可见性与控制上铺了四条线：

1. **多级模型目录与容灾兜底**：`pa-models` 拉取在线 Prime Inference 目录并落盘缓存到 `<agent-dir>/prime-inference-models-cache.json`，断网时回退到 `crates/pa-ai/src/models_generated.rs` 的手写编译期兜底表（包含真实上下文窗口、输出上限、思考等级支持和缓存读取单价）。如果用户通过 `--model` 选了一个目录里无法精确匹配的模型，系统会在标准错误或聊天区弹出明确告警行，而不会默默按编造的模型 ID 继续跑（`.changes/sebastian-surface-model-resolution-warning.md`）。
2. **供应商故障自动切换与防空转**：`crates/pa-core/src/session_engine/` 的 `provider_retry.rs`、`auto_retry.rs` 和 `provider_failover.rs` 把失败分类为永久错误与可重试错误，遵守 `Retry-After` 退避；当主供应商耗尽快速重试且目录中有其他已配置供应商提供同一模型时，自动按目录顺序切换备用供应商并发送 `reason: "backup"` 事件。
3. **前缀缓存保护与变量卸载**：静态系统提示词字节级稳定，`[harness-digest]` 按状态指纹按需追加，避免每轮重写前缀；大段日志和源码留在 CPython 变量里，减少重复进入注意力窗口的输入 Token。
4. **子 Agent 墓碑留账与硬预算封顶**：如第 6 章所见，即使子 Agent 已被删除清理，`rlm_ledger.rs` 依然在墓碑记录里保存 `deleted_usage` 与按模型拆分的消耗统计，保证整棵递归树的开销可完整汇总；而在 `/goal` 与 `/autonomous` 模式下，`token_budget`、`max_tokens`、`max_turns` 和 `timeout_ms` 提供了硬性止损线。

## 用 350 行零依赖 Python 跑通六大核心不变量

源码读到最后，最好的检验方式是剥掉网络、终端 UI 和多进程样板，把 Prime Agent 最值钱的六条核心不变量放进一个零第三方依赖的离线教学模型里亲手跑一遍。

本书在 `examples/mini_prime_agent.py` 中用纯 Python 3.10+ 标准库实现了这套精简模型，并在 `examples/test_mini_prime_agent.py` 中编写了 19 个单元测试，逐一对应前面章节拆解过的真实源码机制：

| 教学模型组件 | 对应上游源码 | 验证的核心不变量 |
| --- | --- | --- |
| `HeadTailBuffer` | `crates/pa-core/src/kernel/manager/requests.rs` | 3:1 头尾切分、滚动尾窗口线性内存裁剪、省略字符数精确统计 |
| `PersistentReplKernel` | `prime-agent-runtime/src/rlm/repl.py` | 跨单元格变量持久与尾表达式绑定 `_`、跳过保留名、`os.replace` 原子快照、仅对超单变量上限执行 `prune_oversized`（聚合超限只跳过不删内存） |
| `SessionInbox` | `crates/pa-core/src/session_engine/inbox.rs` | `inbox.jsonl` 断写容错重放与单调 ID、未知过滤字段严格报错防误清空、按条件局部消费、同一心跳任务仅保留最新未读、合并计数唤醒文本 |
| `SubagentRegistry` | `crates/pa-core/src/session_engine/rlm_host.rs` | `DEFAULT_RLM_MAX_DEPTH = 2` 深度拦截、兄弟唯一命名、UTF-16 码元计数的 512 长度上限与 10 秒 `progress_note` 节流、非阻塞 `collect` 快照 |
| `compaction_threshold` / `should_compact` | `crates/pa-core/src/session_engine/compaction.rs` | 联合输出上限截顶、防频繁重摘要的 `retention_floor = keep_recent + 2 * reserve + 1` 抬升 |
| `MiniAgentLoop` | `crates/pa-agent/src/agent.rs` & `agent_loop/run.rs` | Claim-First 排他占位防重叠运行、`rlm.end_turn()` 跑完同批其余调用后无额外回复停机、Steering → Follow-Up → Continuation 严格三级优先级 |

下面这段调用代码展示了教学模型如何把持久 REPL、严格信箱过滤和 `end_turn` 批终止串在一起（完整代码见 `examples/mini_prime_agent.py`）：

```python
from mini_prime_agent import (
    AssistantTurn,
    MiniAgentLoop,
    PersistentReplKernel,
    SessionInbox,
)

inbox = SessionInbox()
inbox.push("bash-done", "cargo test: 42 passed", arrived_at_ms=1_000, sender="cargo test")
inbox.push("heartbeat", "check gpu util", arrived_at_ms=2_000, sender="hb-1")
inbox.push("heartbeat", "check gpu util (latest)", arrived_at_ms=3_000, sender="hb-1")

kernel = PersistentReplKernel(max_output_chars=128)
kernel.install_binding("inbox", inbox)
loop = MiniAgentLoop(kernel)

def fake_model(_transcript):
    return AssistantTurn(
        text="reading bash completions and ending turn",
        tool_calls=[
            "done_msgs = inbox.read({'class': 'bash-done'})\nlen(done_msgs)",
            "end_turn()",
        ],
    )

transcript = loop.run(inbox.wake_notice_text(), fake_model)
assert kernel.namespace["done_msgs"][0].text == "cargo test: 42 passed"
assert inbox.count()["count"] == 1  # 只剩最新一条 heartbeat，未被误清空
```

在本书仓库根目录运行以下命令，即可离线复验全部 19 个单元测试：

```bash
python3 -m unittest discover -s examples -v
```

测试全部通过后，你不仅看懂了 Prime Agent 这 55 万行 Rust 与 Python 源码里的设计决策，手里也有了一套可以随时改参数、加断言、验证边界条件的最小实验台。
