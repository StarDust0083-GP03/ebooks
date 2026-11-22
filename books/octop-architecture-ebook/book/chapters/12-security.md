# 安全边界：MFA、权限分离、审批串行与云凭证隔离

给 Agent 接上 shell、浏览器、外部连接器和云主机 API 之后，安全问题不再停留在登录页面。一个真实请求至少会撞上五道不同的门：身份认证、资源与用户组授权、工具审批（HITL）、执行与网络边界、以及静态凭证保护。这五道门互相补位，任何一道都不能替代另一道。

## 第一道门：JWT、Setup 锁定与 TOTP / Passkey MFA

所有 HTTP `/api/` 请求首先经过两层中间件：`setup_lockdown.py` 在系统尚未创建任何用户时锁死除安装向导和健康检查外的一切接口，返回 `setup_required`；系统初始化完成后，`jwt_auth.py` 对非豁免路径校验 JWT 并将已认证用户挂到 `request.state`（错误文案按 `Accept-Language` 本地化）。WebSocket 路由不会自动继承普通 HTTP 中间件的保护，因此在 `ws.py` 建连阶段会独立完成一次完整的 token 校验与资源鉴权。

从 `v1.0.3b2` 开始，Octop 在 `src/octop/api/routers/auth_mfa.py` 和迁移 `025_user_mfa` 中正式落地了多因素认证与会话加固：

- **TOTP 与 WebAuthn Passkey**：支持一次性动态口令、硬件或系统通行密钥二次验证以及一次性恢复码；同时支持无密码 Passkey 登录，但该开关默认关闭，需管理员在应用设置中显式开启。
- **高危配置二次认证（Reauth）**：增删 TOTP 或注册 Passkey 前，必须先调用 `/api/auth/mfa/reauth` 再次验证密码（兼容本地账号和 LDAP 绑定限流）换取短效 `reauth_token`。
- **会话续期与强制登出**：记住登录支持自动续期，而修改密码会立即让其他设备的旧会话失效；老数据库升级时若缺少 MFA 表会自动补齐，防止升级中断登录。

## 第二道门：可见、可用、可改三权分离

登录成功只说明系统认识你是谁，不代表能碰别人的资源。Octop 把资源权限细分成三层：

1. **页面与功能级权限**：`v1.0.3b1` 起权限按工作台 Tab 拆分并支持按组折叠，例如云主机页的路由 `api/routers/cloud.py` 直接挂载了 `Depends(require_permission("cloud"))`。
2. **用户组资源可见性**：技能包、知识库、连接器、模型、存储后端和专家都可以按用户组限定可见范围；共享连接器只有创建者能修改配置。
3. **专家与线程两级校验**：第 5 章拆解过，`require_agent_row` 放行所有者、管理员和分享对象，`require_agent_owner_row` 锁死配置写入权，`resolve_thread_id` 和 `thread_visible_to` 把私人线程与显式共享对话区分开。

## 第三道门：HITL 审批与重叠 ACP 权限串行化

危险工具需要人工确认时，运行图在中断点挂起。`src/octop/infra/gateway/hitl/coordinator.py` 和 `infra/agents/security/hitl_session.py` 把待审批动作绑定到具体的 `pending_id`、用户、专家和线程，协调网页弹窗（恢复走 SSE）、CLI 提示和 IM 文本回复三种入口，同时受线程级执行锁保护，避免同一个中断点被并发恢复。

审批设计里有一条铁律：批准必须绑定已保存的参数快照，而不是只绑工具名。假如界面展示给用户确认的是“发邮件给 A”，如果恢复接口允许重新塞入一个收件人 B，审批就成了摆设。同样，审批管的是放行未来的执行，取消管的是终止当前计算，已经发出去的外部写操作无法靠事后点取消自动撤回。

到了 `v1.0.3b6`，在对接托管编程 Agent 时冒出了一个非常隐蔽的并发审批 Bug（PR #2994）：Cursor CLI 可以在同一回合里连续抛出两个重叠的权限请求（比如同时发起两次 WebSearch）。底层的 `ACPHostedClient` 内部只保存单个 `future` 和单个待处理权限，第二个 `request_permission` 进来直接覆盖了第一个 `future`，等用户点同意后，只有第二个被唤醒，第一个永远挂死，整轮对话卡在开场白再也走不完。

`src/octop/infra/agents/acp/permissions.py` 用不到 30 行代码干净利落地修掉了这个问题：给每个 `ACPHostedClient` 实例动态挂一把 `asyncio.Lock`（`_octop_permission_lock`），包装 `request_permission`，强迫同一客户端上重叠到达的权限请求排队挨个等用户确认。

## 第四道门：容器挂载、SSRF 与静态加密

在执行和网络层面，Octop 叠了四块互不替代的防护板：

- **Docker 宿主机挂载策略（`v1.0.3b6`~`b7`）**：如第 7 章所析，裸机安装时 `omit_docker_volumes()` 强制剥离所有 `docker` 和 `composite` 配置里的 `volumes`，堵死普通用户把宿主机敏感目录挂进沙箱的路；只有当 Octop 自己跑在容器内、暴露目录已由运维在宿主机容器层收敛时，才允许向 Docker 专家透传挂载。
- **出站 SSRF 防护（`infra/utils/ssrf_guard.py`）**：自定义 MCP、OAuth 发现、元数据端点和回调全部校验公网 HTTPS 与 DNS 解析后的真实 IP，阻断私网与链路本地地址探测（仅显式允许本地/局域网自建 MCP 跳过私网禁令）。
- **静态密钥加密与日志脱敏**：从 `v1.0.3b1` 起，控制面保存的各类连接器与供应商密钥改为 Fernet 加密落盘，同时兼容平滑读取旧明文；`server.py` 在常规日志级别压低 `httpx`/`httpcore` 输出，避免带查询参数密钥的完整 URL 刷进日志文件。
- **工作区边界与插件信任**：记住 `host_jail_enforced()` 的结论，无 `bwrap` 的非 Linux 环境不具备内核级文件系统隔离；插件代码运行在宿主进程内，只应安装可信来源的插件。

## 第五道门：腾讯云 CVM 凭证按用户隔离与 Bridge 隧道白名单

`v1.0.3b4` 上线了工作台云主机页（`src/octop/api/routers/cloud.py`），让用户授权腾讯云账号后在界面里查看 CVM 实例监控并交给常驻运维专家巡检。在多用户和多副本部署下，云账号凭证如果混用或落在本地单机文件，会产生严重的越权与串号风险。

`v1.0.3b4` 和 `v1.0.3b6` 连续把这两条口子补齐：多副本部署时腾讯云授权改存共享控制面（`open_cloud_credential_store` 走 `secret_repo`），单机仍兼容本地文件；同时登录态与本地 `tccli` 镜像凭证严格按用户 ID 隔离（`tccli_credential_path_for_user`），绝不让 A 用户的云操作凭据被 B 用户或 B 用户的专家顺走。

而在跨实例协同场景下，`src/octop/infra/bridge/tunnel_policy.py` 对 Bridge HTTP 隧道实施**默认拒绝**策略：只放行白名单里明确定义的专家对话、只读 composer 和浏览器视图等路径与方法，所有未知管理路径一律拦下。并且隧道对端严格以对端连接账号的身份执行鉴权，本机的 `user_id=7` 绝不会直接当成远端的 `user_id=7`。

## 源码入口

- [`src/octop/api/middleware/jwt_auth.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/middleware/jwt_auth.py)
- [`src/octop/api/middleware/setup_lockdown.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/middleware/setup_lockdown.py)
- [`src/octop/api/routers/auth_mfa.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/auth_mfa.py)
- [`src/octop/api/routers/cloud.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/cloud.py)
- [`src/octop/api/common/agent.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/common/agent.py)
- [`src/octop/infra/gateway/hitl/coordinator.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/hitl/coordinator.py)
- [`src/octop/infra/agents/security/hitl_session.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/security/hitl_session.py)
- [`src/octop/infra/agents/acp/permissions.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/acp/permissions.py)
- [`src/octop/infra/backend/docker_spec.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/docker_spec.py)
- [`src/octop/infra/utils/ssrf_guard.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/utils/ssrf_guard.py)
- [`src/octop/infra/bridge/tunnel_policy.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/bridge/tunnel_policy.py)
