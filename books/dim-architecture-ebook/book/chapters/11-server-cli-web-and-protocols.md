# App Server、CLI、Web 与 ACP / MCP 网关

一个设计得再干净的 Agent 内核，如果每个前端（终端 TUI、浏览器 Web 界面、Zed / Lody 这类外部 IDE）都各自内嵌一份运行时、各自读写同一个 SQLite 文件，很快就会撞上文件锁冲突、多端状态打架和后台任务随窗口关闭而夭折的问题。

Dim 的解法是把所有执行、审批、存储和插件装配收拢到唯一的后台守护进程 `dim-app-server`，再让 CLI、Web 工作区和 ACP 网关都作为讲同一套协议的薄客户端接入。

## 守护进程 App Server：受保护传输、优雅关机与单向升级

App Server 住在 `crates/dim-app-server`，同时作为子命令 `dimcode app-server` 被编译进单一二进制 `dimcode` 里。它只管装配、持久化、传输安全与对外入口，不把具体业务插件逻辑塞回主干（`principles.md` §1）。

**传输安全边界非常分明。** 产品模式和自定义模型配置模式下，执行与管理类 RPC v3 必须走受保护的本地传输：Unix 上是权限 `0700` 目录里的 `0600` 私有套接字（清理时只删除本监听器持有的 inode），Windows 上是带本地身份校验的命名管道。默认的 WebSocket 端点在非 mock 模式下只响应 `hello` 和 `ping`，防止局域网或浏览器任意页面直接调起本地执行。为了防止客户端连错守护进程，`hello.daemonProfile` 会显式声明当前守护进程属于 `product`、`configured`（附带模型配置文件原始字节的 64 位小写 SHA-256 摘要，不泄露环境变量值）还是 `mock`，客户端遇到配置或模式不匹配时直接拒绝连接，绝不擅自杀掉别人的守护进程。

**精细的关机与活动持有协议。** 当客户端发来 `daemon.shutdown { ifIdle: true }`（协议 v3 特性标记 `daemon.shutdown.ifIdle`）准备空闲退出或原子升级时，App Server 不会粗暴地只看当前有没有 TCP 连接，而是同时核对四件事：没有其他客户端连接、没有已附着的会话、没有任何分支上的在途工作，也没有任何通过 `processActivity.hold()` 拿走的进程级活动持有（例如后台脱离工作、正在跑的 Workflow、或者尚未排空的定时投递）。只要其中一项为忙，就返回 `daemon_busy` 并附带计数详情；一旦判定全部空闲，才会关闭新准入并开始优雅清理。如果请求的是默认优雅关闭（`force: false`），发起方连接会把最终回复的写通道转交给进程协调器，等整个 `rutis` 根树彻底卸载、SQLite 刷盘并显式释放 `flock` 锁之后才回发成功（避免并发派生的子进程继承文件描述符导致下一个守护进程误报数据库被占用）。

**提交确认的不确定性诚实告知。** 在 `session.submit` 的协议边界上，App Server 明确区分了“保证未落盘的拒绝错误”（`invalid_params`、`session_invalid_input`、`session_closed`、`session_build_failed`、`service_reloading`、`unknown_method`、`input_blocked`）与“提交后结果不确定的错误”（`submission_unknown`、存储/发布错误、连接断开）。客户端拿到不确定错误时绝不盲目重试，而是保留 Session id 提示用户恢复检查，避免把同一条指令提交两遍。同时，`UserPromptSubmit` 这类 Hook 失败但设为 `onFailure: continue` 时，不把提交判失败，而是通过加法字段 `warnings: [{ check, message }]` 告知客户端原样展示，绝不混入模型上下文。

**单向原生存储升级与离线运维。** 原生 SQLite 数据库在当前版本采用明确的版本号标记，启动时若检测到支持的旧格式，会在进入任何可写 PRAGMA 之前，在单一准入事务内原地顺序升级，失败则完整回滚，不支持的版本（包括曾移除的分支专用格式）直接拒绝打开并原样保留文件。导出整条会话时间线（`dimcode export SESSION_ID`）直接以只读快照打开数据库，不争抢守护进程锁，不触发升级；回收空间（`dimcode daemon vacuum`）则要求守护进程已停机，先校验不存在悬空内容引用、清理不可达的内容寻址节点，再执行 `VACUUM`。

## 终端 CLI 与 Web 工作区：薄客户端的不同接入姿态

终端客户端位于 `crates/dim-cli`（内嵌终端控件源自 xAI `grok-build` 的 Ratatui 衍生实现，遵循 Apache-2.0 归属），它自己不跑模型也不决定权限，只管输入编辑、终端回滚区渲染和交互式审批。

有意思的是，交互式 TUI 并不是只开一条普通 RPC 连接那么简单。在 `crates/dim-app-server/README.md` 描述的 UI 叶子（UI Leaf）协议里，交互式 `dimcode` 进程本身是一个监听在私有地址上的轻量 `rutis` 内核，通过 `ui.open` 让守护进程的 `UiLeaf` 反向拨号握手并挂载会话，再把普通的协议 v3 业务连接通过 `ui.attach` 登记上去，支持按代次原子替换（`ui.open { replace }`）且 20 秒内未附着自动回收。除了交互式 `tui`，CLI 还提供面向脚本与流水线的 `dimcode exec -p` / `exec --json`（审批或提问无法交互时主动停止当前 turn 并以退出码 3 退出，Ctrl+C 等待结算后以 130 退出），以及本地图像缩放校验（发送前统一把图片缩放到 2048 px 与 5,000,000 字节 base64 以内，单次最多 20 张，总请求体不超过 16 MiB）、超 16 KiB 粘贴自动走 `attachment.put`、跨平台剪贴板和 `@` 本地文件补全。

Web 工作区则完全由守护进程按需拉起：通过 `web.open`（持有者模式）或 `web.toggle`（守护进程开关）只绑定在 loopback 回环地址，前端静态资源既可读取外部 `dimcode-web` 目录（每次打开校验 `dimcode-web.json` 里的协议版本，不匹配返回 `web_assets_incompatible`），也可通过 `embedded-web` Cargo 特性编译进二进制。浏览器拿着返回 URL 里的一次性引导令牌换取 Cookie 后，走协议 v3 建立连接，只开放 Session 执行方法和只读模型发现接口，把 `daemon.shutdown` 等宿主管理方法全部隐藏成 `unknown_method`。在 `v0.1.0-alpha.27` 中，Web 端还引入了共享的 `chat-ui` 工作区包，将 Web 时间线渲染与交互控制（包括运行中 `steer`、暂存草稿和中断发送）与 TUI 逐步对齐。

## ACP 网关：零侵入的协议翻译层

为了接入 Zed、Lody、acpx 等支持 Agent Client Protocol（ACP）的外部编辑器和工具，Dim 在 `crates/dim-acp` 实现了 `AcpGateway`，通过 `dimcode acp` 在标准输入输出上暴露 ACP v1 协议（固定 `agent-client-protocol` SDK 3.5.0）。

| 架构边界 | `AcpGateway` 的具体做法 |
| --- | --- |
| 进程定位 | 运行在客户端侧的独立 `rutis` 进程插件，不是守护进程的 loader row，也不链接 App Server 内部类型 |
| 共享传输 | 与 `dim-cli` 共同依赖提取出来的 `crates/dim-client`，共用 16 MiB 帧封装、套接字安全校验与 OAuth 环回回调捕获 |
| 协议投影 | 只调用公开的 RPC v3 白名单方法，将 `session/new`、`load`、`resume`、`prompt`、`cancel`、`set_mode`、`set_config_option` 映射到原生方法；`approval.request` 映射为 `session/request_permission`（只给 `allow_once`/`reject_once`，不提供 `always`）；`ask_user` 条件映射为 `elicitation/create` 表单 |
| 会话级临时 MCP | ACP 客户端在 `session/new` 时传入的 `mcpServers` 通过独立的 `TemporaryMcp` Session 插件挂载，仅作用于该会话，会话结束立刻丢弃，绝不污染全局持久化 MCP 配置 |
| 严禁补假语义 | 现有原生接口无法保证的能力直接声明不支持（如 `logout`、客户端侧 `fs/*` 与 `terminal/*` 反向执行）；`session/load` 或 `resume` 要求守护进程明确声明 `session.attach.expectedCwd` 与 `session.history.latestTurn`，绝不退回到全量枚举会话或扫描全部历史 |

## MCP 管理与 OAuth：代次平滑切换与变量防护

对于持久化的 MCP 服务器，App Server 的 `McpPlugin` 把配置管理、连接代次和 OAuth 拆得很细：

- **三源分层与逐条容错**：支持 `user`、工作区 `.mcp.json` 的 `project`（按 ADR 003 默认禁用）以及插件贡献的 `plugin:<id>` 三类来源，支持从 Claude Code、Codex、OMP 预览和导入配置。解析配置文件时采用逐条容错，单条格式损坏会进入 `invalid` 列表并保持原样，不拖垮其余正常条目。
- **连接代次无缝交接**：每个 Session 通过子插件托管自己的 MCP 连接代次。修改或刷新配置时，正在执行中的旧工具调用继续跑完旧代次连接，新调用切换到新代次；只有最后一份旧租约释放后才关闭旧连接，而显式禁用则会主动排空并关闭所有会话里的活跃代次。失败重连采用 1 秒起跳、上限 5 分钟的指数退避；远端返回 HTTP 404 会重建会话并重放一次，单次工具调用超时默认 10 分钟（可在条目 `timeout` 里配 1 秒到 24 小时）。
- **环境变量展开防护**：远程 URL、请求头和 Bearer Token 支持 `${NAME}` 和 `${NAME:-default}` 展开，但对于工作区 `.mcp.json` 和插件来源，所有受保护的敏感变量名（模型 Provider 密钥、云凭据、代理、`DIM_*`/`DIMCODE_*` 等）在远端字段里会被强制展开为空字符串且忽略默认值，防止恶意仓库借 `.mcp.json` 把用户的 API Key 偷发到外部服务器。
- **按需懒加载的 MCP OAuth**：遇到远端返回 `401` 时，`mcp.auth.start` 自动发现受保护资源元数据并完成动态客户端注册，凭据按 `0600` 原子写入 `mcp-oauth.json` 并在过期前 30 秒跨会话单飞刷新；只要本地没有任何需要 OAuth 的 MCP 条目，守护进程甚至不会初始化 OAuth HTTP 客户端。

把整个系统的内内外外走完一遍之后，我们有必要冷静停下来，用工程师的眼光审视这套高度严整的架构究竟付出了哪些工程代价、靠什么守住边界，以及哪些问题在当前 Alpha 里仍然有意留白。
