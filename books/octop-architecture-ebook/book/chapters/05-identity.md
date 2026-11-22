# 身份与会话：四种标识与共享专家的线程隔离

共享一个调教好的专家给团队同事，是自托管助手最高频的需求，也是最容易漏权的地方。假如后端只校验当前用户有没有登录、能不能访问这位专家，一旦有人把浏览器地址栏或接口里的 `thread_id` 换成同事的编号，整段私人聊天记录就会直接串台。

Octop 用四个彼此正交的标识把人和对话彻底拆开：`user_id`、`agent_id`、`session_key` 和 `thread_id`。混用其中任意两个，权限和会话生命周期都会缠在一起。

![四种标识与共享专家的线程隔离边界](../../wiki/assets/diagrams/identity-boundary.svg)

## 四种标识各自管什么

| 标识 | 回答的问题 | 生命周期特征 |
| --- | --- | --- |
| `user_id` | 当前操作主体是谁 | 来自 JWT / 认证中间件，绝不信任请求体里的明文声明 |
| `agent_id` | 调用哪位专家 | 资源维度，可私有、可按用户组可见、可公开分享 |
| `session_key` | 某个入口当前绑在哪条对话上 | 可随时改绑，如 `<aid>:dashboard:<uid>:dm` 或 IM 群键 |
| `thread_id` | 哪一段持续对话和 LangGraph 图状态 | 稳定身份，切新对话后旧线程保留在历史里 |

不拿 `session_key` 直接充当 `thread_id`，原因很实在：同一个浏览器窗口或飞书私聊入口，用户随时会点新建对话。如果入口键和对话 ID 合二为一，新建对话就只能覆盖或删掉旧上下文；把它们拆成一张绑定表（`ThreadRegistry`），入口可以自由指向新线程，旧线程依然能按 `thread_id` 回看、归档甚至被定时任务固定引用。

## 先查专家权限，再查线程可见性

请求进入聊天或工作区路由时，鉴权分成严格的两关。

第一关在 `src/octop/api/common/agent.py`。代码把读用权限和管理权限明确拆成两个辅助函数：`require_agent_row()` 允许专家所有者、管理员以及满足分享或用户组可见策略（`expert_visible_to`）的成员使用专家；修改模型、提示词、技能或删除专家则走 `require_agent_owner_row()`，把普通共享使用者挡在外面。

第二关在 `src/octop/api/routers/chat/turn.py` 的 `resolve_thread_id()`，以及 `src/octop/infra/gateway/threads.py` 的 `thread_visible_to()`。看 `v1.0.3b7` 的真实检查逻辑：

```python
row = thread_registry.get_thread(thread_id)
if row is None or row.agent_id != agent_id:
    raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"thread {thread_id!r} not found")
if not thread_visible_to(row, user_id):
    raise OctopError(ErrorCode.FORBIDDEN, "thread not owned by user")
```

而 `thread_visible_to(row, user_id)` 的判定只有一行：

```python
def thread_visible_to(row: ThreadRow, user_id: int) -> bool:
    return row.user_id == user_id or bool(getattr(row, "contributed", False))
```

这行代码藏着 `v1.0.3b5` 才加入的产品语义：**共享专家上的主动共享对话**。默认情况下，哪怕专家已经分享给全组，每个人在上面创建的对话 `contributed` 都是关闭的，只有 `row.user_id == user_id` 才能打开。只有当线程所有者在共享专家下主动把某一条对话分享出去（置位 `contributed`），同一专家的其他可见成员才能阅读这条对话。早于该字段的老数据行默认视为私有，不会因为升级突然泄密。

同时，第一行 `row.agent_id != agent_id` 挡住了另一种越权：禁止拿着 A 专家合法的 `thread_id` 去请求 B 专家的聊天接口，防止上下文和工具权限跨专家污染。

## session_key 是路由输入，不是凭证

不同入口生成的 `session_key` 格式各自独立：

- Web 工作台：`<agent_id>:dashboard:<user_id>:dm`
- CLI 终端：`<agent_id>:cli:<user_id>:dm`
- IM 通道：`<agent_id>:<channel_kind>:<platform_session>:<dm|group>`
- 定时任务：默认回落到任务所有者的 `dashboard` 会话键，`v1.0.3b6` 起还可以写成 `latest`（跟随最新对话）或 `thread:<thread_id>`（钉死某一条历史对话，见 `infra/cron/session_target.py`）。

`thread_id` 本身不编码渠道来源，来源类型作为独立列存在线程表里。任何从客户端传入的 `session_key` 都只能当路由参数，服务端必须拿 JWT 解析出来的 `user_id` 重新校验专家与线程归属，绝不能因为字符串里拼了某个数字就放行。`tests/integration/test_boundary_authz.py` 专门把所有者、共享使用者、无权用户和管理员四种主体排成矩阵，测试专家读取、配置写入、线程订阅与取消的边界。

## 预建 LDAP 账号与用户组可见性

企业内部署时，管理员往往希望在员工第一次登录前就把专家、知识库和技能包分好权限。`v1.0.3b1` 引入了按用户组限制技能包、知识库、连接器、模型、存储和专家的可见性；`v1.0.3b3` 又补上了预建 LDAP 账号能力，管理员可以提前建好账号并拉进对应用户组，员工首次用公司 LDAP 密码登录时直接继承预设分组和资源可见范围，不用等登录完再手工补权。

## 删除线程和删除专家，先清 checkpoint 再删表行

沿着标识往下看，删除操作比创建更考验顺序。对话正文和图状态存在 Agent 的 checkpoint 存储里（本地 `memory.sqlite` 或 PostgreSQL 共享表），跟控制面的 `threads` 和 `agents` 表没有数据库级外键级联。

看 `src/octop/infra/agents/memory/thread_cleanup.py` 的顶部注释和执行顺序，Octop 定下了一条反直觉但非常稳的规则：**先删 checkpoint，成功之后才删 `threads` 行**。原因很简单：如果先删了 `threads` 表行，接着删 checkpoint 失败，这条对话在界面上消失了，磁盘或数据库里却永远留下一堆查不到的孤儿状态，用户想重试删除都没入口；反过来先删 checkpoint，失败了直接报错，对话还留在列表里，用户或管理员随时可以重试。

删除整个专家也照此办理：先遍历清理该专家所有线程的 checkpoint，再清除它的长期记忆命名空间（SQLite 删前缀表，PostgreSQL 在 `octop_memory` 等共享 schema 中按 `namespace` 删行），最后才删 `agents` 行和本机数据目录。此外，从 `v1.0.3b1` 起，如果某条对话正在流式回复中，接口会直接拒绝删除该对话，避免前台删表、后台还在写历史和 checkpoint 撞成脏数据。

## 源码入口

- [`src/octop/api/common/agent.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/common/agent.py)
- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)
- [`src/octop/infra/gateway/threads.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/threads.py)
- [`src/octop/infra/agents/memory/thread_cleanup.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/thread_cleanup.py)
- [`tests/integration/test_boundary_authz.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/tests/integration/test_boundary_authz.py)
