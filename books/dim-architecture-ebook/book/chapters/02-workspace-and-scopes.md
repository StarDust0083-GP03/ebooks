# 28 个 Crate 与三层作用域

打开一个中大型 Rust 工作区，最怕看到所有业务代码都堆在 `server` 里，或者底层 `types` 偷偷反向引用了上层实现。Dim 根目录 `Cargo.toml` 里一共列了 28 个工作区成员（`crates/` 下 23 个业务与协议包、`xtask` 与 `xtask/check` 共 2 个工程门禁包、`tests/` 下 3 个集成与扩展测试包，另有 `vendor/grok-build` 处于 `exclude`）。这套拆法由 `xtask/check/src/boundaries.rs` 在编译之外强制校验单向依赖红线，不靠口头约定。

## 28 个工作区成员的分层地图

![工作区四层依赖与三层作用域树](../../wiki/assets/diagrams/workspace-scopes.svg)

上面这张图把代码仓库的静态依赖和运行时的作用域树摆在了一起。上半部分是编译期可见的包分层，`xtask-check` 用四条硬检查卡死反向依赖：`dim-agent-types` 不允许依赖运行时或能力层，`dim-agent-runtime` 不能碰 `dim-agent-capabilities` 和 `dim-app-server`，`dim-agent-capabilities` 不能反向依赖 `dim-app-server`，而 `xtask-check` 自身甚至不允许依赖任何 `dim-*` 包或 `rutis`，保证纯静态检查不用等整个业务工作区编译完就能秒跑。

把 28 个成员按职责收拢，可以分成五组：

| 分组 | 包含成员 | 核心职责 |
| --- | --- | --- |
| 契约与运行内核（2） | `dim-agent-types`、`dim-agent-runtime` | 定义 `AgentEvent`、13 个保留域、冻结的 v1 线格式，以及 `Timeline`、`Dispatcher`、`AgentBuilder` 和 `PartRegistry` |
| 核心能力与模型宿主（6） | `dim-agent-capabilities`、`dim-agent-aimux`、`dim-agent-attachments`、`dim-agent-questions`、`dim-agent-scheduler`、`dim-event-hub` | 提供 20 个分支 Capability、Aimux 多协议模型宿主、附件、交互式提问、定时唤醒与 `/loop`、跨会话事件中心 |
| 可选业务插件（9） | `dim-codemode`、`dim-goal`、`dim-session-title`、`dim-oauth`、`dim-report`、`dim-modality`、`dim-image-understanding`、`dim-ocr`、`dim-modality-aimux` | rquickjs 代码沙箱、Pursuit 目标推进、自动会话标题、账户 OAuth、本地诊断报告、多模态任务与本地 OCR |
| 宿主、协议与客户端（6） | `dim-app-server`、`dim-client`、`dim-acp`、`dim-cli`、`dimcode`、`dim-migration` | SQLite 持久化与守护进程、共享 RPC 帧传输、ACP 协议网关、Ratatui 终端客户端、单二进制入口与旧版数据离线迁移 |
| 门禁与测试（5） | `xtask`、`xtask/check`、`tests/extension-a`、`tests/extension-b`、`tests/integration` | 生成真实装配图与服务目录、执行静态 AST 检查、验证跨包扩展和端到端行为流程 |

其中有一个很务实的细节：`dimcode` 单独作为一个二进制入口 crate，把 `dim-cli` 和 `dim-app-server` 打进同一个可执行文件里。终端启动守护进程时，并不是去装第二个独立安装包，而是通过 `<home>/rust/dimcode-server` 链接调起自身的 `app-server` 子命令，既方便用 `ps` 或 `pgrep dimcode-server` 区分客户端与守护进程，又避免了双二进制版本漂移。

## 为什么按状态所有者划三层作用域

把包拆清楚只解决了编译期问题，运行时部件该放哪儿更容易踩坑。`docs/adr/002-plugin-scope.md` 记了一段真实弯路：早期设计曾为了减少能力数量，把大部分新功能塞进 Process 进程级作用域。结果这些进程级插件既要给某个 Session 提供工具，又要监听某个 Session 的事件，还要往具体某条分支写输入，于是设计里被迫长出了一堆接线模块：扩展注册表、注册端口、Session 访问宿主、进程级订阅转发、聚合插件。每个接线模块存在的唯一理由，都是把放错位置的插件硬连回它原本该待的作用域。

ADR 002 拍板撤回了所有接线模块，定下一条铁律：作用域不按模块大小划分，而是按状态属于谁、生命周期跟谁走来划分。

**Process 根作用域**放跨会话共享或比单个会话活得更久的东西。真实装配目录里有 58 个进程级服务，包括 SQLite 存储 `sqliteStorage` 与 `store`、模型供应商目录 `providerService`、全局设置 `settings`、技能目录 `skillsCatalog`、MCP 配置管理 `mcpService`、宿主进程执行端口 `hostPlatform`、文件系统端口 `fsHost`，以及跨会话运行的定时调度器 `scheduleJobs` 和工作流引擎 `workflowRuns`。

**Session 作用域**代表一个主体，也就是与用户交互的同一个“我”。它持有该主体全部行为线共用的身份、工作区路径、工具注册表 `toolRegistry`、提示词源 `promptRegistry`、工作区文件工具 `toolHost`、文件访问事实 `FileAccessFacts`、分支目录 `timelineTree` 与 `treeCommands`。共 31 个 Session 级服务。只要任务需要换一个独立身份、独立系统提示或独立工作目录，就必须新建 Session（例如协作子代理或分叉会话），不能硬塞成同一 Session 里的分支。

**Branch 作用域**代表这个主体的一条并行行为线。它拥有自己的 `timeline` 与 `dispatcher`，以及围绕这条线折叠出的全部状态机：`Loop`、`InputQueue`、`Prompt`、`ContextPreparation`、`ContextUsage`、`ToolBinding`、`ToolExecutor`、`Permission`、`Approval`、`BranchLine`、`TreeTimeline`、`BackgroundExec`、`WaitBranch` 等，共 22 个分支级服务。

## 五种合法跨层关系与 ADR 015 的轻量化重构

在 `docs/architecture/rust-agent/principles.md` 第 2 节里，三层作用域之间的交互被收敛成五种明确关系，其余路径一律禁止：

1. **分支使用能力**：分支部件向上注入 Session 或 Process 服务，例如 `Permission` 注入 `S:CommandRules` 与 `S:SessionConfig`。
2. **能力在某条分支上被使用**：Session 级工具或方法入口在收到调用时，按调用方携带的 `branch_id` 路由到该分支的服务，比如 `BranchTool` 每次调用再查找调用线的 `BranchLine`。
3. **能力影响分支**：能力按自身契约向一条或多条分支写输入或事件，例如 `Goal` 向目标分支写入评估结果。
4. **分支影响分支**：严格限定在父子之间，通过向对方写入 `input/message` 或通过工具分页读取对方已提交历史完成，异步且不互相阻塞等待；兄弟分支之间不可见、不互调。
5. **Session 管理自己的分支**：分支目录、身份预留、创建、就绪发布与关闭结算由 Session 级的 `TimelineTree` 独占，但分支内由时间线推出来的业务状态绝不泄漏回 Session 层。

到了 `rutis 0.23.0`（ADR 015 与 ADR 025），这套作用域树又完成了一次关键瘦身。早期 Session 和 Branch 都是 `rutis-loader` 里的实例组（instanced loader group），每次打开会话或新建分支都要扫描全量 loader 条目表，连 MCP 连接代次和技能内容也成了嵌套 loader 实例。升级到 `rutis 0.23.0` 后，宿主可以用 `mark_scope` 把普通插件标记为作用域根，于是 Session 成为 Agent 挂载的普通子插件，Branch 成为 Session 挂载的普通子插件，Skills 内容与 MCP 连接代次则直接成为各自所属部件的子插件。`Loader::entries()` 里从此只剩进程行和 Agent 锚点行，创建与关闭会话直接走 `rutis` 原生的父子所有权树，不再需要扫描或维护第二套实例表。

理解了 28 个 Crate 的单向边界和三层作用域树，就能看清 Dim 最底层那根脉络：每条分支里真正负责把状态机串起来的时间总线。
