# 本地守护进程、SSH 远程代理与跨主机热迁移

写代码时经常会遇到两种场景：第一种，在笔记本终端里让 Agent 跑一轮大重构，跑了一半想合上终端窗口切去干别的，甚至改用浏览器里的桌面 UI 查看 Diff，回来再接着聊，不能一关终端就把 Agent 和后台构建全杀掉；第二种，起初在笔记本本地开了个会话分析了半天需求，真到要编译庞大 Rust 工作区或者跑 GPU 测试时发现本机算力吃紧，想把整个活生生的会话连同对话历史树、子线程和沙箱里改到一半的代码，原封不动搬到云端那台 64 核开发机上继续跑。

`crates/infinity-daemon/`（9,215 行）就是为这两个场景设计的控制枢纽。CLI 终端、桌面 Web 界面、Zed 编辑器（ACP）和 Slack 机器人，本质上都是连向同一个守护进程的客户端。

## 常驻守护进程与 256 MiB 帧边界

守护进程在首次运行 `infinity` 时自动拉起，把全部本地状态收纳在 `~/.infinity/` 下：

| 路径 | 职责 |
| --- | --- |
| `~/.infinity/daemon.sock` | 客户端与守护进程通信的 Unix Domain Socket |
| `~/.infinity/daemon.pid` | 守护进程 PID 文件 |
| `~/.infinity/providers.json` | 已安装的外部模型供应器二进制注册表 |
| `~/.infinity/remotes.json` | 配置好的 SSH 远程守护进程列表 |
| `~/.infinity/sessions.json` | 会话索引元数据（工作目录、关闭与归档状态） |
| `~/.infinity/.../{thread_id}*.json` | 按线程拆分的懒加载历史、元数据与视图快照 |

这里有一个很容易被忽略的传输层细节（`crates/infinity-protocol/src/lib.rs` 第 175 至 190 行）：tokio 默认的 `LengthDelimitedCodec` 单帧上限只有 8 MiB。但在 Coding Agent 场景里，一次重放长对话历史或者回传大文件 Diff 很容易冲破 8 MiB，导致客户端莫名断连。`infinity-protocol` 统一提供了 `length_delimited_codec()` 工厂函数，把 `MAX_FRAME_LENGTH` 调到 **256 MiB**，客户端和守护进程两端强制复用，保证帧长上限不会漂移。

面向浏览器桌面端（`infinity-web` + `infinity-ui`），守护进程通过 `ws_handler.rs` 暴露 WebSocket 接口，默认监听 `127.0.0.1:8080`（可通过 `INFINITY_WS_BIND` 与 `INFINITY_WS_PORT` 调整）；如果编译 CLI 时启用了 `--features bundled-web`，`web_assets.rs` 会把静态构建产物直接打包进二进制，开箱即访问，和终端 TUI 共享同一批在线会话。

## 懒加载 JSON 持久化与历史格式兼容

`crates/infinity-daemon/src/memory_store.rs` 的 `PersistentConversationStore` 展示了非常克制的组合设计：它内部直接包着核心库的 `InMemoryConversationStore`，把所有复杂的祖先链、派生截断、压缩游标逻辑全部委托给核心实现，自己在外面只叠了一层**按线程懒加载的 JSON 文件持久化**。

每个线程在磁盘上拆成三份文件：`{thread_id}.json` 存消息列表与压缩摘要，`{thread_id}.meta.json` 存核心 `ThreadInfo` 与守护进程专用的 `ThreadExtras`（标题、子线程列表、累计 token、最后更新时间以及该线程独立选中的 `selected_model`），`{thread_id}.views.json` 存前端旁路视图（例如沙箱实时推送的 Diff 面板内容）。两张内存标记集合 `loaded` 和 `metadata_loaded` 保证打开会话列表时只读轻量的 `.meta.json`，真正点进某条会话才把完整消息读进内存。

源码里还保留了干净的向后兼容层：反序列化 `ThreadSnapshot` 时先尝试新格式（带展示元数据的 `InfinityMessage`），失败则回退解析旧版的 `OldThreadSnapshot`（裸 `Message` 加独立的 `display_as` 旁路表）并在内存里无损升级；如果老元数据里没有按线程记录的 `selected_model`，加载时自动用哨兵值识别并回填全局默认模型。

## SSH 远程代理：透明拼装 ThreadRef

开发者在 `~/.infinity/remotes.json` 里登记远程开发机（或者执行 `infinity remote add devbox -- ssh -J bastion devbox`）。守护进程启动时，`crates/infinity-daemon/src/remote.rs` 读取 `ssh_args`，为每台远端建立一条直连远端 `~/.infinity/daemon.sock` 的 SSH 转发隧道，并在断线时每隔 5 秒自动重连，同时把 `connecting` / `connected` / `disconnected` 状态实时广播给前端 UI。

多主机代理最巧妙的地方在于客户端几乎零感知：

- 远端会话在列表里通过 `ThreadRef::prefixed(&remote)` 染上前缀（如 `devbox/a1b2c3d4`），本地会话保持裸 UUID。
- 客户端发来针对 `devbox/a1b2c3d4` 的 `Connect`、`UserInput` 或 `SwitchModel` 时，本地守护进程识别出 `remote == Some("devbox")`，调用 `strip(&remote)` 剥掉前缀，原样沿 SSH 隧道转发给远端守护进程。
- 远端返回的事件流再经由本地守护进程重新打上 `devbox/` 前缀推回前端。整个过程不需要在远端开放任何公网 HTTP 端口，完全复用开发者已有的 `~/.ssh/config`、跳板机与密钥认证体系。

## 跨主机热迁移：把活会话与沙箱一起搬走

热迁移是 `infinity-daemon` 里最见分布式系统功底的一块代码，编排逻辑集中在 `crates/infinity-daemon/src/migrate.rs`（428 行），配合 `sandbox-core/src/server.rs`（第 1994 至 2250 行）的 `/migrate` 与 `/migrate/import` 两个接口完成。

无论用户是把本地会话搬到远端 `devbox`、把远端 `devbox` 搬回本地，还是把一台远端 `devbox` 直接搬到另一台远端 `gpu-server`，**永远由本地守护进程充当总编排器**。整个搬迁严格按六个阶段推进：

1. **停机定格源会话**，先在源主机调用 `cleanup_session` 停掉正在跑的 Agent 驱动和原会话附属的 RAP 服务，确保对话历史与沙箱文件在打包期间不再发生变化。
2. **拉起目标端迁移 RAP 服务并打通 SSH 隧道**，`boot_dest_and_tunnel` 在目标主机的 `dest_cwd` 下拉起那些在清单里声明了 `needsMigration: true` 的 RAP 服务（主要是 `sandbox-local`），拿到监听端口后按源与目标的拓扑动态建转发隧道：
   - 本地搬到远端：通过 `ssh_forward_port` 把远端目标端口正向转发到本机。
   - 远端搬到本地：通过 `ssh_reverse_forward_port` 把本机目标端口反向转发到源远端。
   - 远端 A 搬到远端 B：本地编排器充当中继，先把远端 B 的端口正向转到本机，再从本机反向转到远端 A，形成一条无需 A/B 互通直连的级联隧道。
3. **沙箱打包与 multipart 传输**，源端拉起临时迁移 RAP 服务，通过 `RapNotifier::request_migration` 通知源端 `sandbox-local` 向目标 URL 迁移。`sandbox-core` 的 `migrate_inner` 收集属于该 `root_thread_id` 的所有线程沙箱状态：如果是 Jujutsu 仓库，先确保所有工作区都已加载并跑一次 `jj git export` 把书签全部导出到 Git 引用里；接着调用 `git bundle create` 把该会话全部线程的 `sandbox-{thread_id}` 书签打成一个自包含的二进制 bundle 文件；最后连同序列化后的元数据 JSON 通过 HTTP multipart POST 发往目标端的 `/migrate/import`（目标端路由特意加了 `DefaultBodyLimit::disable()` 放开请求体限制，源端遇到连接拒绝最多间隔 5 秒重试 5 次）。目标端收到后解包 bundle、还原书签并改写 `remote_uri` 指向目标仓库路径。
4. **序列化迁移对话树**，沙箱搬迁成功后，源端调用 `serialize_session`（远端则通过点对点 `ClientMessage::Emigrate` 指令）把根线程及所有子线程的完整消息、压缩摘要和元数据打包传回编排器。
5. **目标端导入并清理临时服务**，销毁刚才拉起的临时目标 RAP 服务，在目标端调用 `import_session`（或发 `ClientMessage::ImportSession`）把整棵对话树导入存储，并在 `SessionStore` 中建好记录、标记为已停机等待下次唤醒。
6. **归档源会话并拆除隧道**，源端把旧会话标记为已归档（远端通过 `EmigrateDone` 通知），离开函数作用域时 `_tunnel_guards` 自动 `drop`，所有临时 SSH 端口转发全部干净回收，前端收到 `DaemonMessage::MigrateComplete { root_thread_id, new_root_thread_id }` 切到新坐标。

这套流程对失败边界也守得很严：`migrate_inner` 开头就会直接拒绝迁移 `SandboxMode::Direct`、`SandboxMode::Custom` 以及横跨多个不同 Git 仓库的会话，避免搬到一半留下残缺状态。

到这里，本地与跨机私有开发机的完整运行体系已经拆解完毕。下一章把视角切到公有云，看同一份 `infinity-agent-core` 在完全没有常驻进程、没有本地磁盘、也没有 SSH 隧道的 AWS 无服务器平台上怎么落地。
