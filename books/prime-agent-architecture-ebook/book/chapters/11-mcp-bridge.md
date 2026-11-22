# 宿主管认证、内核跑协议的 MCP 桥接层

接过 Model Context Protocol（MCP）的人都知道它有两个天然痛点：第一，很多 MCP 服务需要 OAuth 2.0 登录、动态客户端注册、PKCE 交换、本地回调端口监听和令牌自动刷新，把这些交互塞进 Python 子进程里既难和桌面浏览器配合，又容易让运行在 REPL 里的模型代码直接翻到磁盘凭据；第二，一个企业工作区动辄挂五六个 MCP 服务、几百个工具 Schema，如果像传统客户端那样在启动时把所有工具定义全塞进大模型的 `tools` 参数数组，还没开始干活上下文就已经被工具声明吃掉两万 Token。

Prime Agent 给出的架构解法非常干脆：**劈成两半**。Rust 宿主管配置、目录、权限和 OAuth 凭据，Python 内核管真正的 MCP 客户端连接和按需协议调用；而且所有 MCP 工具都不进大模型的 JSON 工具定义数组，全部变成 `python_repl` 里的程序化异步调用。

![MCP 桥接职责划分与安全信任边界](../../wiki/assets/diagrams/mcp-security-boundary.svg)

这张图同时标出了 MCP 的职责分界与整套系统的底层权限现实。先看上半部分的 MCP 桥接，再在第 13 章展开下半部分的安全边界。

## 宿主侧 `McpManager`：绝不启动 MCP 子进程，只管认证与目录

翻开 `crates/pa-core/README.md` 和 `crates/pa-core/src/mcp/`（18 个模块），顶部就写明了一条非目标边界：**会话引擎从不自己启动任何 MCP 服务进程**。`McpManager` 专注管好三件事：

**配置解析与目录视图**，汇总内置服务目录、用户 `settings.json` 里的 `mcpServers` 声明以及共享凭据库中的连接状态。操作者在终端打开 `/mcp` 面板查看服务卡片（`service_catalog_views`）和 API Key 凭据行（`api_key_credential_views`，例如网页搜索密钥）时，全部从宿主本地状态直接出图，不阻塞去往返查询 Python 内核。

**完整的 OAuth 2.0 认证链路**，`crates/pa-core/src/mcp/oauth*.rs` 实现了 RFC 9728 资源元数据发现、动态客户端注册、PKCE 挑战、本地 HTTP 回调监听（支持桌面应用固定注册回调端口，如 Slack 公共桌面客户端配置，以及动态端口候选区间）与令牌刷新。登录成功后，凭据按服务端点绑定写入共享认证存储，且通过 `pa-daemon` 的 `WorkerMcpLoginUi` 桥接把登录提示送达当前挂载的终端。

**三个受控宿主请求接口**，向 Python 内核暴露 `mcp.config`、`mcp.refresh` 和可选的 `mcp.begin_login` 三个 `host_request` 处理器。当内核里的模型代码想要调用某个服务时，内核按需向宿主索取该连接的解析配置或刷新后的访问信息；而面向模型探索的目录查询接口（`list_plugins`、`search_plugins`、`list_connections`）只返回白名单元数据，绝不把原始凭据吐给模型。

## 内核侧 `rlm.mcp`：按需发现、有界分页与进程卫生

真正的 MCP 传输协议跑在 `prime-agent-runtime/src/rlm/mcp.py`（1,075 行）和 `mcp_base.py`（337 行）里。模型在 REPL 中面向两个清晰的表面：

**按需发现面**，避免把几百个工具声明预先灌进系统提示词。模型可以用 `await mcp.list_connections()` 查已连接服务，用 `await mcp.search_tools(query, connection_id=..., limit=20)` 在最多 8 个活跃服务（`_MAX_TOOL_SEARCH_SERVERS = 8`）里搜索工具名和描述，或者用 `await mcp.describe_tool(connection_id, tool)` 单独拉取某一个工具的 `inputSchema`。

**实时调用面**，`await mcp.list_tools(server)` 和 `await mcp.call_tool(server, tool, arguments=...)`。调用时，`_Generation` 生命周期管理器按需懒加载对应的 stdio 子进程或 Streamable HTTP 连接，加锁串行化调用，原样透传工具 Schema 与实时返回值。

因为外部 MCP 服务质量参差不齐，`mcp.py` 里塞满了针对故障服务的防御性常量：

| 防御项 | 源码常量 | 保护目标 |
| --- | --- | --- |
| 启动握手超时 | `_DEFAULT_STARTUP_TIMEOUT = 20.0` 秒 | 防止卡死的 stdio 服务永久挂起首次连接 |
| 单次工具调用超时 | `_DEFAULT_CALL_TIMEOUT = 60.0` 秒 | 对齐默认单元格超时，避免失联服务拖垮调用 |
| 优雅关闭超时 | `_SHUTDOWN_TIMEOUT = 2.5` 秒 | 严格小于宿主的 `KERNEL_SHUTDOWN_TIMEOUT_MS`（5 秒），确保内核退出前能体面回收所有 MCP 子进程 |
| 分页列表上限 | `_MAX_TOOL_PAGES = 25` 页 | 遇到死循环游标或无限翻页的恶意/故障服务时直接抛出 `McpDiscoveryError`，绝不把残缺工具表当成完整列表发布 |
| 环境变量白名单 | `_SAFE_ENV = ("HOME", "PATH", "TMPDIR", "TEMP", "TMP", "SystemRoot", "WINDIR")` | 启动 stdio MCP 子进程时只透传极简基础环境变量，避免把宿主的全部敏感环境变量顺手泄露给第三方 MCP 工具 |
| 错误输出裁剪与脱敏 | `_STDERR_BYTE_LIMIT = 8 KiB`、`_STDERR_LINE_LIMIT = 40` 行 | `_StderrTail` 独立管道线程截断诊断日志，并用 `_SECRET_KEY_PATTERN` 和敏感值表清理可能渗入报错的 Token、密码或私钥，同时过滤 ANSI 转义与控制字符 |

此外，在 `crates/pa-daemon/src/mcp_connections.rs` 中，当 `/mcp` 界面确实需要展示每个活跃连接的实时工具清单时，守护进程会通过前面第 4 章提到的带外 `mcp_status` 通道（默认每服务 10 秒超时）并行拉取，既不阻塞用户正在跑的 Python 单元格，又把单个故障服务的报错隔离在对应连接行里。仓库里还专门配有端到端真机回归测试 `crates/pa-daemon/tests/mcp_product_path_e2e.rs`，用真实的 Worker、Python 内核和 `mcp_echo_server.py` 走完 `mcp.config` 到 `mcp.call_tool` 全链路。

后端引擎、多进程守护、信箱、提示词、压缩与 MCP 桥接全部就位后，还有一个最容易被低估的层面决定这套系统用起来到底顺不顺手：终端界面（TUI）如何做到毫秒级首帧、不卡顿地渲染流式代码和信箱卡片，以及后台怎么悄悄把可观测性做扎实。这就是下一章的主题。
