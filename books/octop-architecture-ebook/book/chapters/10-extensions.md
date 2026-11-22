# 工具与扩展：Skill、Plugin、Connector 与 MCP OAuth

想让专家按公司模板写周报、想给对话界面加一个富交互卡片、想连上公司内部文档或自建 MCP 服务器，这三件事如果全都笼统叫成装插件，权限边界和故障面很快就会失控。Octop 把扩展能力分成了四层，每一层的执行位置、信任级别和授权粒度都不一样。

| 扩展机制 | 核心作用 | 典型载体 | 风险边界 |
| --- | --- | --- | --- |
| Skill（技能与技能包） | 给 Agent 提供按需读取的操作规程与脚本文档 | `SKILL.md` 与配套文件树 | 属于提示词和工作区文件，不能代替服务端鉴权 |
| Plugin（插件） | 往宿主或运行库注入 Python 代码扩展 | 工具、中间件、IM 通道、富 UI 载荷 | 运行在宿主进程权限下，内置插件默认关闭 |
| Connector（连接器） | 把某用户的外部服务账户封装为受控能力 | 加密凭证、实例配置、工具白名单 | 按用户和共享范围隔离，共享连接器仅创建者可改 |
| MCP | 标准化的工具发现与调用协议 | `streamable_http` / `stdio` server 配置 | 只管传输协议，不自带最小权限模型 |

## 技能包与插件：分发、隔离与大载荷卸载

技能包（Skill Package）在 `v1.0.3b1` 到 `v1.0.3b6` 之间补齐了完整链路：支持按用户组共享、在工作台直接用文件树编辑、打包导出 ZIP 跨机迁移（还可由创建者关闭打包下载权限）。按 ADR 003，PostgreSQL 模式下技能包源文件存入 `skill_package_files` 表，绑定给原生专家时复制到专家的 `BackendWorkspace`；绑定给托管专家时，则通过 `managed.py` 的 `link_managed_skill_packages()` 在 Runner 自己的目录（如 `.claude/skills` 或 `.opencode/skills`）下建符号链接或 Windows Junction，并用清单追踪归属。

插件（`src/octop/infra/agents/plugins/manager.py`）的信任级别完全不同，代码直接跑在 Octop 进程里。`PluginManager` 启动时播种内置插件（默认关闭，需要显式启用），从 `v1.0.3b3` 起专家与插件市场目录彻底从安装包剥离、改为按需从远程市场加载，上传和 ZIP 解压有严格的体积上限防炸弹攻击。在 PostgreSQL 多副本模式下，已安装的插件和头像也同步进共享存储，避免各副本能力不一致。

插件常会返回体积庞大的 `octop_ui` 富界面数据（如表格、图表或交互面板）。如果原样塞进模型上下文，几轮对话就会把 token 预算吃干；如果直接丢掉，前端又画不出来。`OctopUiOffloadMiddleware`（设计说明见 `docs/octop-ui-payload-offload.md`）专门拦截这类特定信封，把大载荷卸到 UI 侧消费，只留精简摘要给模型和外层中间件。它服务于明确的插件信封协议，不是对任意工具结果做无差别压缩。

## 连接器的四种运行模式与 RAGFlow 接入

内置连接器目录定义在 `src/octop/infra/connectors/catalog.json`（包含腾讯文档、企查查、语雀、Obsidian、Agent Mail、钉钉 CLI 以及 `v1.0.3b2` 新增的 RAGFlow 知识库连接器），按执行位置分成四种模式：

- `remote`：运行库直接连向供应商托管的远程 MCP 地址。
- `gateway`：使用 Octop 进程内的 Python 适配器执行，无需拉起外部进程。
- `internal`：由 Octop 自己在 `/api/internal/mcp` 托管一个本地 HTTP MCP 入口，供运行库接入。
- `stdio`：在 Octop 所在宿主机上拉起子进程，通过标准输入输出通信。

同样叫连接器，网络超时、凭据传递、代理配置和子进程回收完全不同。装配时还要严格区分三层集合：系统安装了哪些连接器、当前用户授权了哪些实例、这位专家在本轮允许调用哪些工具。`v1.0.3b3` 和 `v1.0.3b4` 连续修了两个高频边界：共享连接器只允许创建者编辑，防止使用者改坏公共凭证；共享专家使用查看者自己的自定义 MCP 时，会通过 `shared_mcp_server_name()` 正确映射到 harness，不串名也不越权。

## 自定义 MCP 的 OAuth 授权与 RFC 8414 路径发现

越来越多远程 MCP 服务不再只收静态 API Key，而是走 OAuth 2.0 授权。Octop 在 `src/octop/infra/connectors/custom_mcp.py` 和 `oauth/` 下实现了完整的自定义 MCP 接入链，并在 `v1.0.3b5` 与 `v1.0.3b7` 补齐了两个企业内网和 SaaS 常见的痛点。

第一，**面向不支持动态注册的服务商提供手动 OAuth 应用凭证（`v1.0.3b5`）**。标准 MCP 授权依赖动态客户端注册（DCR），但不少 OAuth 服务商要求提前在开发者后台建好应用拿到 `client_id` 和 `client_secret`。`oauth/mcp.py` 的 `manual_client_metadata()` 允许用户填写手工授权地址与令牌地址，同时用 `validate_https_url_resolved()` 做 DNS 解析后的 SSRF 拦截；公网远程 MCP 强制 HTTPS，本地回环和内网局域网 MCP 则放宽允许 `http://` 方便内网自建工具联调。

第二，**带路径的 MCP OAuth 按 RFC 8414 规范发现元数据（`v1.0.3b7`，PR #3004）**。当一个 OAuth Issuer 带了子路径（例如 `https://auth.example.com/realms/tenant`），旧写法简单在末尾拼 `/.well-known/oauth-authorization-server` 会直接吃 404。`src/octop/infra/connectors/oauth/mcp.py` 的 `fetch_authorization_metadata()` 按 RFC 8414 第 3.1 节把 `.well-known` 插在域名与路径之间：

```python
parsed = urlparse(issuer)
issuer_path = parsed.path.rstrip("/")
metadata_url = (
    f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server{issuer_path}"
)
```

配合 `oauth/discovery.py` 里按 RFC 9728 顺序探测 `/.well-known/oauth-protected-resource{path}`、根路径以及 `401 WWW-Authenticate` 响应头里的 `resource_metadata`，无论是 Keycloak/Auth0 这类带租户路径的身份提供商，还是标准根域名 MCP 服务，都能自动发现授权端点，而且所有元数据里声明的 `authorization_endpoint`、`token_endpoint` 和 `registration_endpoint` 都必须通过 `host_allowed_for_issuer()` 与 SSRF 检查，防止恶意服务器借元数据重定向打向内网地址。

## v1.0.3b7：托管专家如何通过 6 个回环 MCP 消费宿主能力

原生专家跑在 Python 进程内，可以直接调用知识库、定时任务和插件函数；可托管专家（OpenCode、Claude Code、Codex 等）跑在独立的外部进程里，怎么才能共享同一套 Octop 能力？

`v1.0.3b7` 在 `src/octop/infra/agents/acp/turn.py` 和 `mcp_tools.py` 给出的方案非常巧妙：**不改外部 Runner 源码，而是把 Octop 内部工具包装成最多 6 个按回合授权的本地 HTTP 回环 MCP 服务**，在 `session/new` 和 `session/load` 时作为 `mcp_servers` 下发给外部 Runner：

1. `octop-knowledge`：携带一次性或按作用域复用的随机 token（`KnowledgeScope` 绑定 `user_id`、`is_admin`、勾选的 `knowledge_base_ids` 和 `locale`），让外部 Runner 按同样权限检索知识库。
2. `octop-agents`：把 `ask_agent` 等专家回调工具暴露出去，让托管专家也能呼叫 Octop 里的其他专家协作。
3. `octop-files`：暴露 `send_file_to_user`，让外部 Runner 把工作区里生成的文件直接推回给网页或 IM 用户。
4. `octop-cron`：暴露 `cronjob_create/list/update/delete`，并在回合提示词里明确叮嘱 Runner 别用它自带的会话内临时定时工具（那些任务不会出现在 Octop 的任务列表里）。
5. `octop-memory`：仅在该托管专家显式打开记忆开关时挂载。
6. `octop-plugins`：把当前专家已启用的插件工具自动转成标准 MCP JSON Schema，通过 `var_child_runnable_config` 注入上下文后供外部 Runner 调用。

此外，在 `ask` 或 `plan` 只读对话模式下，`_connectors_allowed()` 会自动跳过外部连接器，并针对 ACP 协议自身不支持按工具名过滤的限制，用 `_tool_limit_note()` 在回合提示中明确告知 Runner 每个连接器仅允许调用的白名单工具。

## 源码入口

- [`src/octop/infra/agents/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/manager.py)
- [`src/octop/infra/agents/plugins/manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/plugins/manager.py)
- [`src/octop/infra/connectors/builder.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/builder.py)
- [`src/octop/infra/connectors/catalog.json`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/catalog.json)
- [`src/octop/infra/connectors/custom_mcp.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/custom_mcp.py)
- [`src/octop/infra/connectors/oauth/discovery.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/oauth/discovery.py)
- [`src/octop/infra/connectors/oauth/mcp.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/connectors/oauth/mcp.py)
- [`src/octop/infra/agents/acp/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/turn.py)
- [`src/octop/infra/agents/acp/mcp_tools.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/mcp_tools.py)
- [`docs/octop-ui-payload-offload.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/octop-ui-payload-offload.md)
- [`plugins/README_CN.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/plugins/README_CN.md)
