# 启动与退出：装配顺序、延迟绑库与有序收尾

服务刚启动时最容易出隐蔽故障：数据库还没选定，路由已经放进来读写请求；旧版本残留的历史归档还没迁完，新对话已经把 checkpoint 祖先剪掉了；进程收到 `SIGTERM` 后直接强杀，定时任务刚好触发一半，通道租约没释放，另一台副本只能干等 90 秒超时。

Octop 把整套启动和退出拆成了两个清晰的装配入口：`src/octop/launch.py` 负责外部进程与 Web 服务器接线，`src/octop/infra/server.py` 里的 `OctopServer` 负责领域对象的依赖排序、延迟绑库、启动迁移和逆序关闭。

## 为什么把 launch.py 和 server.py 拆开

执行 `octop run` 时，CLI 并不直接构造 FastAPI，而是调用 `launch.run_foreground_blocking`。`launch.py` 先启动 `OctopServer`，再把构建好的领域服务树交给 `api/app.py` 的 `build_app()`，最后挂载到 uvicorn 事件循环。

把这两个文件分开有两个实在的好处。第一，像 `octop chats repl`、`octop cron run-now`、`octop acp` 这类嵌入式 CLI 命令，可以直接实例化 `OctopServer` 跑完就退，不用绑定 TCP 端口，也不用引入任何 FastAPI 路由代码。第二，HTTP 层与领域层的依赖方向被物理切断，哪怕未来换一套 Web 框架，核心启动树也不用动。

## 从空目录到可接请求的装配链

进入 `OctopServer.start()` 后，对象树严格按依赖顺序逐层拉起：

1. 解析 `PathLayout.from_env()` 和 `load_config(config.json)`，叠加环境变量覆盖，确定数据目录、密钥目录与数据库配置。
2. 判断是否需要延迟打开控制面数据库（`should_defer_control_plane_db`）。首次安装且尚未指定数据库时，服务先以未绑定状态启动，只放行安装向导与健康检查路由。
3. 控制面就绪后，调用 `open_database()` 打开 `SqlitePool` 或 `PostgresPool`，顺序执行 `infra/db/migrations/` 下的带编号迁移脚本（SQLite 与 PostgreSQL 成对维护，截至 `v1.0.3b7` 已到 `025_user_mfa`）。
4. 装配 `SharedServices`（所有仓储与公共服务）、`WizardTokenStore`（5 分钟有效的向导令牌）、`ExpertCatalog`、`SubagentCatalog`，以及 `PluginManager.seed_bundled()` 与 `load_installed()`（内置插件默认关闭）。
5. 构造进程级 `AgentManager`，并在任何专家写新对话之前执行一次性 `migrate_history_v2()`（见下文）。
6. 拉起 `Gateway`（包含按需建立的 `ChannelManager` 与 `WebSocketHub`）、`CronManager`（APScheduler）和 `UserManager`，把四个核心单例收进 `AppRuntime` 暴露给 API 路由。

这套顺序里藏着一条关键区分：进程存活不等于业务就绪。如果健康检查只看 TCP 端口通不通，首次安装进入向导锁定态，或者某个专家的远程后端失联时，排障很容易误判。区分宿主未绑库、用户未初始化、单个专家构建超时和单次模型调用失败，能省掉大量查日志的时间。

## 首次安装的延迟绑库与空库重绑

自托管用户第一次打开页面，往往是在浏览器向导里决定用 SQLite 还是填一套外部 PostgreSQL 地址。如果 `OctopServer.start()` 强行在启动瞬间建好默认 SQLite 并初始化所有服务，用户选 PostgreSQL 时就会出现两套库互相打架的局面。

`infra/db/factory.py` 中的 `should_defer_control_plane_db` 专门处理这个空白期：未完成初始选库时，服务先启动 HTTP 界面并由 `setup_lockdown` 中间件拦截非向导请求；用户提交数据库选择并测试连通后，服务端调用 `bind_control_plane()` 正式打开目标库、跑完迁移并启动 `_boot_runtime()`。

对于已经绑定了空库、还没有创建任何用户（`user_count == 0`）的向导场景，`infra/db/rebind.py` 还允许切换一次控制面，并调用各运行时组件的 `replace_repos()` 等重绑方法同步新连接池，避免直接改私有字段留下一半旧引用。一旦管理员账号建好、系统进入正常使用，这条热重绑通道就关上了，不存在运行中把存量 SQLite 数据在线切到 PostgreSQL 的魔法。

## v1.0.3b5 的启动关口：下线 history_v2 的一次性迁移

在 `v1.0.3b1` 时，Octop 曾试验过一套名为 `history_v2` 的本地分段归档库（`history_v2.sqlite`）。到了 `v1.0.3b5`，团队决定正式下线这套仅支持 SQLite 的平行历史库，统一回到控制面的 `thread_messages` 投影表。

处理这种历史包袱时，最危险的做法是在后台异步慢慢搬数据，或者允许新对话边写边迁。看 `src/octop/infra/server.py` 的 `_boot_runtime()`，团队把 `migrate_history_v2()` 放到了一个非常卡死的位置：**数据库迁移完成之后、任何 Agent 开始写新历史或裁剪 checkpoint 祖先之前**。

`src/octop/infra/history/retire_v2.py` 的实现很克制：

- 检测到 `~/.octop/history_v2.sqlite` 或 `history_v2.required` 存在时才触发；如果只有标记文件却缺归档文件，或者当前控制面已经不是 SQLite，直接抛错中止启动，绝不静默丢历史。
- 以只读模式（`mode=ro` + `PRAGMA query_only = ON`）打开旧归档，校验 `archive_meta.version == 1`。
- 按 `thread_id` 逐条开控制面事务迁回 `thread_messages`，并在同一事务里向 `history_v2_retirement` 表写入归档片段的 SHA-256 摘要。哪怕迁移到一半进程崩溃，重启后已迁完的线程靠摘要幂等跳过，未迁完的继续补齐；如果有人换了一份不同内容的旧归档顶包，摘要不匹配会立刻报错拦下。
- 全部线程落稳后，再把旧归档和标记文件归档移走，之后整套系统才正式接流。

这个启动关口示范了什么叫负责任的架构退场：不留双写尾巴，不靠运维手工倒库，把幂等和崩溃恢复做在启动链路里。

## 退出为什么必须按逆序来

收到退出信号后，`OctopServer.stop()` 不会直接让进程一死了之，而是反着依赖树拆掉：先停主动关怀调度器和 `CronManager`，再停 `Gateway` 和外部通道连接，随后停止 `AgentManager` 中的活跃专家与连接器会话，最后关闭历史资源和数据库连接池。

先停调度器和入口，再停执行器，道理很朴素：如果先关了数据库或 Agent 注册表，而 APScheduler 刚好唤醒一条定时任务，或者 IM 通道正好收进一条群消息，就会撞上一连串半销毁状态。在多副本 PostgreSQL 部署里，有序退出还多了一层价值：`CronManager` 和通道管理器退出时会主动释放自己持有的数据库租约行，另一台副本在下一轮 25 秒巡检里就能立刻接管，不用干等 90 秒租约自然过期。

## 源码入口

- [`src/octop/launch.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/launch.py)
- [`src/octop/infra/server.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/server.py)
- [`src/octop/infra/db/factory.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/factory.py)
- [`src/octop/infra/db/rebind.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/rebind.py)
- [`src/octop/infra/history/retire_v2.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/history/retire_v2.py)
