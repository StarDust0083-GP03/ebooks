# Agent 装配：原生专家、托管专家与中间件顺序

用户在界面里改了一位专家的默认模型，或者关掉了一个技能，数据库里那行 `agents` 记录马上就变了。但此时内存里可能正跑着一个三分钟的回合，或者连接池里还挂着旧的 MCP 客户端。数据库里的配置行，和真正执行推理的运行时对象，在 Octop 里是两回事。负责把静态配置行装配成活的运行时、又管住并发重载边界的，是 `src/octop/infra/agents/manager.py` 里的 `AgentManager`。

从 `v1.0.3b6` 和 `v1.0.3b7` 开始，这套装配体系还多了一类一等公民：**托管专家（`kind="managed"`）**。它把本地的 OpenCode、Claude Code、Codex、CodeBuddy 等外部 ACP 编程 Agent 接进同一张专家列表，同时把 Octop 的工具、知识库和插件递给这些外部进程。

## 从 agents 表行到 HarnessAgentConfig

对原生专家来说，`AgentManager._build_harness_config()` 是能力拼装的总开关。它把六份分散的来源合成一份 `HarnessAgentConfig`，再交给 `octop-harness` 构造 LangGraph 运行时：

| 信息来源 | 装配进运行时的产物 | 工程上容易踩的坑 |
| --- | --- | --- |
| `agents` 行与 `config_json` | 身份、提示词、默认模型、运行时限制开关 | 保存进库不等于已生效，要区分热同步与重建 |
| 工作区后端解析 | `workspace_dir`、`BackendWorkspace` spec | 文件内容后端与 shell 执行环境不是一回事 |
| Provider 解析 | 模型引用、候选故障转移列表、本地 Magpie/Ollama | AUTO 路由在原模型失效后需自动换可用兜底 |
| 技能、插件与连接器 | 工具集合、MCP server 列表、技能目录 | 已安装但未启用的插件绝不能默认注入 |
| 安全策略 | `SecurityPolicy.merge(global, agent)`、`interrupt_on` | 专家级覆盖不得绕过全局底线 |
| Memory 配置 | 记忆后端 DSN/路径、独立辅助抽取模型 | 聊天模型正常不代表记忆提炼模型也可用 |

`manager.py` 体积很大，既管生命周期锁，又管各类能力组装。读它时别从第 1 行硬啃，沿着 `_build_harness_config()` 和 `stream()` 两条主干看，先抓输入来源和输出配置，再按需下钻到 `infra/agents/settings/` 下的子模块。

## 中间件顺序为什么不能随便调

原生专家装配时，宿主会往运行图里挂一串有序中间件。看 `manager.py` 里的装配片段，能直观感受到它们各自卡在什么位置：

- `TokenQuotaMiddleware` 和 `ReasoningRequestMiddleware` 在外层管配额与推理强度参数透传。
- `KnowledgeSearchHintMiddleware` 与 `BrowserProfileMiddleware` 负责把知识库目录和浏览器配置提示带入上下文。
- `BinaryReadGuardMiddleware` 挡住模型直接把二进制大文件当文本读爆上下文。
- `WorkspaceImageMaterializeMiddleware` 把工作区图片路径转换成模型可见的多模态输入；`ThreadArtifactsMiddleware` 则在输出侧把新生成的工作区文件登记到当前线程。
- `OctopUiOffloadMiddleware` 必须留在特定内层，把插件返回的超大 `octop_ui` 富界面载荷卸出模型上下文，同时让外层其他中间件看到一致的瘦身结果。
- 最外层再根据配置挂上 `SafeModelFailoverMiddleware` 以及 `runtime_limits.py` 组装的调用护栏中间件。

这里最值得留意的是 `v1.0.3b7` 新增的 `ConsecutiveToolCallMiddleware`（位于 `src/octop/infra/agents/middleware/consecutive_tool_call.py`）。

## 连续相同工具调用熔断：既杀死循环，又不误伤长任务

早期版本如果简单限制一轮对话的总工具调用次数，很快就会撞墙：一个认真的代码审计或批量文档处理任务，合法调用五六十次不同的 `read_file` 或 `grep` 再正常不过，总次数一刀切会把正常长任务直接掐死；可如果不设防，模型卡在某个报错参数上原地空转几十轮，既烧光 token 又卡死页面。

`v1.0.3b7` 给出的解法非常精准：`runtime_limits.py` 默认开启 `ConsecutiveToolCallMiddleware(limit=40)`，只统计**连续相同的 `(tool_name, sorted_json_args)` 调用**。看它的核心判定逻辑：

```python
class ConsecutiveToolCallState(AgentState[Any]):
    # UntrackedValue 确保计数器只在单次 run 内存活，下一轮用户发言自动清零
    tool_call_streak: NotRequired[Annotated[dict[str, Any], UntrackedValue, PrivateStateAttr]]
```

每当模型产出一条带 `tool_calls` 的 `AIMessage`，`after_model` 钩子逐个计算 `tool_call_signature(call)`（工具名加排序后的 JSON 参数）：

- 签名和上一条完全一致，`count` 加 1；只要换了工具名、改了参数，或者模型吐出了一条不带工具调用的纯文本回复，`count` 立刻归零。
- 一旦 `count > limit`，中间件不会再放行这次执行，而是给被拦截的调用以及同一批次里尚未执行的后续调用都补上 `status="error"` 的 `ToolMessage`，再追加一条带 `[tool_call_repeat]` 标记的 `AIMessage`，直接返回 `"jump_to": "end"` 结束本轮图执行。

把状态类型声明成 LangGraph 的 `UntrackedValue` 很讲究：它不污染长期 checkpoint，也不需要手动写清理代码，用户下一轮发言自然重新起算。配套的 `ToolRetryMiddleware`（默认重试 5 次）和模型重试预算（默认 2 次）也一并收在 `runtime_limits.py` 里统一解析，参数配置收口得很干净。

## 托管专家：把外部 ACP Runner 装进同一套专家模型

`v1.0.3b6` 引入、`v1.0.3b7` 补全的托管专家（`src/octop/infra/agents/managed.py`），解决的是另一类常见诉求：开发者本机已经装好了 OpenCode、Claude Code、Codex、CodeBuddy、Cursor CLI 或 Kimi Code，想直接在 Octop 的 Web 工作台、IM 通道或定时任务里驱动它们，而不是再套一层原生 LangGraph 循环。

托管专家在控制面依然是一行 `agents` 记录，区别在于 `kind == "managed"`，配置里记录 `managed_runner`、`workspace_dir`、可选的 `managed_session_mode` 和 `managed_permission_mode`。宿主在装配它时做了四件很务实的事：

1. **剥离不匹配的原生开关**，`pin_managed_runtime()` 去掉 Octop 原生的 `conversation_mode`，默认关闭记忆提取与注入（除非显式开启），并将存储后端钉死在本地根目录以便用户自选项目目录。
2. **按 Runner 习惯映射技能目录**，`link_managed_skill_packages()` 根据不同工具的原生约定目录（如 `.claude/skills`、`.opencode/skills`、`.codex/skills`、`.codebuddy/skills`、`.cursor/skills`），把用户选中的技能包以符号链接或 Windows Junction 的方式挂进去，并在 `.octop/managed-skill-links.json` 里记录自己创建的链接清单。切换 Runner 时，只清理清单里由 Octop 创建的链接，绝不误删项目原本的真实目录。
3. **会话模式与审批门联动**，`src/octop/infra/agents/acp/session_modes.py` 维护了各 Runner 支持的 `session/set_mode` 枚举表（例如 CodeBuddy/Claude Code 的 `default`、`acceptEdits`、`plan`、`auto`、`bypassPermissions`，Codex 的 `auto`、`read-only`、`full-access`，OpenCode 的 `build`、`plan`）。解析时故意忽略 Octop 自己的 `ask` 和 `craft`，保留通用的 `plan`；只要命中的模式 ID 含 `yolo`、`bypass`、`full-access` 等无人值守标记，`approval_for_session()` 就返回 `yolo` 跳过审批，反之如果该 Runner 发布过无人值守模式但当前没选它，就强制走 `ask`。
4. **通过 6 个回环 MCP 把宿主能力递给外部进程**，外部 Runner 跑在独立子进程里，看不见 Octop 进程内的 Python 工具。`src/octop/infra/agents/acp/turn.py` 在回合启动时，把用户授权的外部连接器加上 `octop-knowledge`、`octop-agents`、`octop-files`、`octop-cron`、`octop-memory`、`octop-plugins` 最多 6 个本地 HTTP 回环 MCP 服务拼成 `mcp_servers` 传给 `session/new` 或 `session/load`。我们在第 10 章和第 14 章会展开这套回环的细节。

## 进程内锁与重载策略

`AgentManager` 内部同时维护了几种不同粒度的 `asyncio.Lock`：按 `agent_id` 的生命周期锁保护启动、停止和重建不撞车；按 `(agent_id, thread_id)` 的执行锁保证同一条线程的普通流式对话和 HITL 恢复不并发写 checkpoint；此外还有在途调用计数和记忆维护时的回收保护。

并非所有配置改动都要推倒重建运行时。像技能启用过滤集合这类轻量变更支持热同步，而更换 Provider、改底层后端或增减插件则会触发运行时重建，并在并发重载时加上有界限制避免瞬间打满内存。需要注意的是，这些锁全部存在于当前 Python 进程内；到了多副本部署场景，跨实例的并发保护不再靠它们，而是交给数据库事务和租约来处理。

## 源码入口

- [`src/octop/infra/agents/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/manager.py)
- [`src/octop/infra/agents/managed.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/managed.py)
- [`src/octop/infra/agents/settings/runtime_limits.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/settings/runtime_limits.py)
- [`src/octop/infra/agents/middleware/consecutive_tool_call.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/consecutive_tool_call.py)
- [`src/octop/infra/agents/middleware/model_failover.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/model_failover.py)
- [`src/octop/infra/agents/middleware/octop_ui_offload.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/octop_ui_offload.py)
- [`src/octop/infra/agents/acp/session_modes.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/session_modes.py)
