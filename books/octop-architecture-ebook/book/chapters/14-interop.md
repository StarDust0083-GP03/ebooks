# 跨端协作：托管 ACP 回环与 Bridge 实例隧道

单台 Octop 跑稳之后，自然会冒出两类跨边界诉求。第一类是开发者手头已经有顺手的本地编程 Agent（Claude Code、OpenCode、Codex、CodeBuddy 等），或者想在 Zed 这类 IDE 里反过来调用 Octop，这走的是 **ACP（Agent Client Protocol）**。第二类是两台独立部署的 Octop（比如办公室台式机和云服务器）想互相借用对方的专家和工作区，这走的是 **Bridge（云端协同桥接）**。

这两种能力都叫跨端协作，但执行位置、数据权威和身份边界截然不同，绝不能混成一回事。

![托管专家 ACP 回环与 Bridge 跨实例桥接对比](../../wiki/assets/diagrams/managed-acp-bridge.svg)

## ACP 的三种用法：入站、出站工具与托管专家

看 `docs/acp.md` 以及 `v1.0.3b6`~`b7` 新加的 `src/octop/api/routers/managed.py` 和 `infra/agents/managed.py`，Octop 对 ACP 的支持已经形成三条清晰路径：

| 形态 | 谁发起，谁执行 | 通信方式 | 核心注意点 |
| --- | --- | --- | --- |
| 入站 ACP（`octop acp`） | Zed 等外部 IDE 调 Octop | stdio JSON-RPC | 启动独立的嵌入式 `OctopServer`，与正在跑的 `octop run` 不共享进程内锁 |
| 出站 `acp_runner` 工具 | 原生专家把子任务派给外部 CLI | stdio JSON-RPC | 在运行 Octop 的宿主机找 `PATH` 与凭证，不是在浏览器机器执行 |
| 托管专家（`kind="managed"`） | 外部编程 Agent 直接作为 Octop 一等专家 | ACP + 6 个回环 HTTP MCP | 直接接管对话回合，可选会话模式，共享 Octop 连接器、知识库、专家回调与插件 |

`v1.0.3b6` 到 `v1.0.3b7` 把托管专家做成了非常顺手的工程抽象。创建托管专家时，`managed.py` 会往目标项目目录放入种子 `AGENTS.md`（仅当目录里原本没有时写入，避免覆盖项目自有规范），将选中的技能包按 Runner 习惯链接进 `.claude/skills`、`.opencode/skills` 等目录，并在每轮对话里通过 `acp/session_modes.py` 与 `acp/turn.py` 完成两件关键桥接：

1. **模式与审批映射**：解析本轮显式模式、线程保存模式或专家固定模式，自动过滤掉 Octop 自有的 `ask`/`craft` 却保留跨协议通用的 `plan`，经 `session/set_mode` 下发给底层 Runner；同时按 `approval_for_session()` 判定本轮是走逐次审批（`ask`）还是无人值守（`yolo`），并由第 12 章提到的 `acp/permissions.py` 给重叠权限请求加锁排队。
2. **反向能力回环**：通过 `octop-knowledge`、`octop-agents`、`octop-files`、`octop-cron`、`octop-memory`、`octop-plugins` 六个本地回环 MCP 端点，把原本只有原生专家才碰得到的知识库、其他专家、文件回传、全局定时任务和已启用插件，原封不动递给外部进程。这样一来，你在飞书里给 OpenCode 或 Claude Code 发一条消息，它既能改本机代码库，又能查 Octop 知识库、把改完的文件发回飞书，还能建一条真正的 Octop 定时任务。

## Bridge 云端协同：转发请求，绝不复制权威

再看另一种场景：台式机上的 Octop 挂着私有代码库和本地 GPU，云服务器上的 Octop 对外连着飞书和公网浏览器。Bridge 要做的不是把两台数据库做双向主从同步，而是通过一条机机长连接，让本机能够安全调用远端那位专家。

在本地视角里，远端专家的标识符形如 `bridge:{connection_id}:{remote_agent_id}`，并在头像上打上云端标记，列表卡片默认对对端地址和账号打码（`v1.0.3b1`）。浏览器依然只请求本机的 `/api` 与 WebSocket，由 `src/octop/infra/bridge/` 下的 `manager.py`、`transport.py`、`http_tunnel.py` 和 `peer_turn.py` 识别远端引用后转发到对端。

这里守着三条硬边界：

1. **权威永远留在专家所在侧**：远端专家的模型循环、线程历史、工作区文件和附件落盘全部发生在远端实例。本机只存桥接连接配置和临时视图，绝不在桥断开后悄悄拉起一个本地同名专家顶替续写，因为两边的工作区、凭证、记忆和执行环境根本不是同一个主体。
2. **两种 WebSocket 彻底分开**：浏览器连本机用的是 `WebSocketHub`，本机连远端用的是独立的机机长连接通道（`v1.0.3b5` 修复了心跳超时反复断开的问题），浏览器永远不需要也不能直接越过本机去直连远端内网地址。
3. **身份不按整数等价，路径默认拒绝**：本机的 `user_id = 7` 和远端的 `user_id = 7` 毫无关系，远端执行请求时严格按照对端绑定账号的身份做资源可见性判断；而且 `tunnel_policy.py` 把可穿透的 HTTP 方法和路径收得很窄，未在白名单内的管理接口一律拒绝，连接所有者与共享使用者的权限同样分开管控。

跨桥调用还必须正视网络不确定性：HTTP 隧道或流式推送在半路超时，并不等于远端没有执行某个外部工具，反过来传输返回成功也不等于远端工具具有天然幂等。在跨实例链路里，任何有外部副作用的动作依然需要依赖动作标识与对端查询来确认结果。

## 源码入口

- [`docs/acp.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/acp.md)
- [`docs/bridge.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/bridge.md)
- [`src/octop/api/routers/managed.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/managed.py)
- [`src/octop/infra/agents/managed.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/managed.py)
- [`src/octop/infra/agents/acp/session_modes.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/session_modes.py)
- [`src/octop/infra/agents/acp/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/turn.py)
- [`src/octop/infra/bridge/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/manager.py)
- [`src/octop/infra/bridge/transport.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/transport.py)
- [`src/octop/infra/bridge/http_tunnel.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/http_tunnel.py)
- [`src/octop/infra/bridge/tunnel_policy.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/tunnel_policy.py)
