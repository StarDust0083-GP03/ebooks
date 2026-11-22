# rutis 插件引擎与跨语言数据面

很多用 Rust 写核心的 Agent 运行时，做到一半都会卡在扩展层：Rust trait 在进程内很好用，可一旦想让用户用 TypeScript 或 Python 写插件，就不得不给每个核心接口手写一套 JSON-RPC 适配层，最后 Rust 版接口和跨语言接口越跑越远，文档里写一套、代码里两套。

Dim 在 `docs/design/rutis-usage.md` 和 `docs/design/service-faces.md` 里定了一条近乎苛刻的收敛原则：**公开服务只有一张合同，就是跨语言数据面（Data Face）；不写第二套适配层，也不留两套业务逻辑。**

## 为什么选 rutis 0.23.0，以及单行装配模型

![rutis 跨语言运行时与数据面边界](../../wiki/assets/diagrams/rutis-data-face.svg)

在工作区 `Cargo.toml` 和 `docs/adr/025-rutis-0-23.md` 里，Dim 把全栈（Rust 工作区 `rutis`/`rutis-loader`/`rutis-bridge`、`xtask` 类型生成器、内置 Node 运行时 `@arcships/rutis-runtime`、TypeScript 插件包以及 Python SDK）统一锁死在 `rutis 0.23.0`（会话协议版本 5）。没有任何双版本兼容垫片：只要握手对端还在说协议 4，连接直接以 `incompatible session` 拒绝。

在装载结构上，Dim 遵循 ADR 006 D3 定下的单行规则：**一个插件在进程配置里只占一个 loader row**，不管它横跨了 Process、Session 还是 Branch 几个作用域。

- 插件的入口模块在进程级 `apply` 里运行一次，从进程服务 `partRegistry` 登记自己要在每个 Session 或每条 Branch 里挂载的部件。
- Rust 插件注册的是生成器函数（`AgentBuilder::feature` 或 `AgentBuilder::plugin`）；TypeScript 和 Python 插件注册的是由 `definePlugin` 构造出的插件定义（`partRegistry.registerDefinition`）。
- 每当新的 Session 或 Branch 打开，作用域根挂载的 `required` 和 `optional` 两个 mounter 会把登记表里的部件实例化为自己的子插件；当原插件卸载或重载时，它登记在 `partRegistry` 里的定义自动撤回，所有活跃实例里的副本同步卸载。

对于外部语言插件，唯一需要在启动前静态声明给宿主 loader 目录的，是它打算在 Session 或 Branch 内部向别处提供的服务名（`instanceNames`），因为 loader 目录在运行时启动前就已经定型。至于工具、提示词源、Session 方法命名空间和宿主管理方法命名空间，在 R6 改造落地之后全部不再占独立服务名，而是由插件在 `apply` 里直接向各自的所有者注册（`toolRegistry.register`、`promptRegistry.register`、`sessionMethods.register`、`managementMethods.register`）。注册与调用方生命周期绑定，插件一卸载，注册项立刻自动撤回；如果此时有正在执行的调用，会明确返回可能已生效的状态，后续调用直接拒绝。

## 跨语言数据面的六条硬规定

要让 Python 插件直接调用 Rust 实现的存储、让 TypeScript 插件直接消费分支事件并注册工具，光有名字解析还不够，接口形状必须天然能过线。`docs/design/service-faces.md` 给数据面立了六条硬规则：

1. **输入输出全是 serde 数据**：普通结构体、标量、数组、枚举，绝不出现进程内句柄、`Arc<dyn Trait>`、`&mut`、迭代器、通道或 `PathBuf`/`OsString`。
2. **身份一律是值**：Session、Branch、Task、File、Root、Subscription 在接口上都只是字符串或整数 id；id 只是标识，不等于授权。
3. **操作代替对象**：把原来面向对象式的长生命周期句柄拆成“id + 一组无状态操作”。比如原来的 `FileStorage` 句柄拆成 `fsHost`（`stat`/`readBytes`/`openRead`/`readAt`/`closeRead`/`lock`/`replace` 等 12 个操作），`HostPlatform` 进程树拆成 `execHost`，`TimelineStore` 拆成 17 个操作的 `timelineStore` 数据面，活对象留在所有者内部托管。
4. **流靠事件或分页，回调不在锁内跑**：大输出走游标分页；回调函数可以经 bridge 按引用跨语言传递（`RemoteFunction`），但严禁在提交锁内部同步回调。
5. **错误是结构化数据**：统一为 `{ code, message }`，转发方原样透传错误码，绝不靠正则匹配错误文案（ADR 025 D5）。
6. **取消是协议动作**：调用方丢弃调用或触发取消回调即可经协议通知对端，不在业务参数里塞状态标志。

在 `rutis 0.23.0` 的传输层上，单帧默认上限是 16 MiB，但协议 5 新增了 `chunk` 分片帧：超过 16 MiB 的大帧自动按每片最大 8 MiB 切分并在派发前重组，总上限由 `RUTIS_MAX_TRANSFER` 控制（默认 256 MiB）。Dim 没有因为有了分片就放任大载荷横飞，而是在应用层主动补回了准入红线，例如 `pluginFiles` 的文本 `replace` 明确在写入前拒绝超过 16 MiB 的序列化记录，返回业务错误 `too_large`。

## 真实装配盘点：75 个数据面与 36 个内部面

纸面愿景和工程现状往往有距离，而 Dim 最好的一点是把迁移进度直接写进了 `xtask graph` 自动生成的 `docs/architecture/rust-agent/generated/catalog.md`，并用 `xtask check` 每天巡检。

按当前提交 `88a3df3669405730cbee6bfd113a7f75cf520677` 的真实 App Server 产品装配统计，全目录共有 **111 个服务**（Process 58、Session 31、Branch 22）：

| 分类 | 数量 | 判定规则（`service-faces.md` §2.3） | 代表服务 |
| --- | --- | --- | --- |
| `data`（跨语言数据面） | 75 | 在目录中声明为 `shared` 且在 `host_key(name)` 挂有 `HostDispatch` | `sessions`、`settings`、`store`、`sqliteStorage`、`fsHost`、`timelineStore`、`treeCommands`、`toolRegistry`、`timeline`、`dispatcher`、`approval`、`permission`、`branchLine` |
| `internal(hot-path)` | 16 | 每轮或每条输入上的高频热路径，尚未操作化 | `agentLoop`、`loopState`、`prompt`、`contextPreparation`、`toolExecutor`、`historyRenderer`、`managedWorktrees` |
| `internal(owner-api)` | 7 | 所有者原生 Rust 绑定，大多已有对应数据兄弟 | `agentIndex`（兄弟 `sessions`）、`sessionStore`（兄弟 `store`）、`settingsService`（兄弟 `settings`）、`modelHost`、`loader`、`timelineTree` |
| `internal(control)` | 5 | 守护进程或作用域控制句柄 | `serverControl`、`connectionHub`、`uiHost`、`uiExtensions`、`timelineFactory` |
| `internal(secret)` | 3 | 含 OAuth 或供应商凭据，凭据留在所有者内部 | `providerService`、`grokOAuthService`、`openAICodexOAuthService` |
| `internal(host-resource)` | 3 | 宿主本地资源索引或连接解析器 | `mentionIndex`、`providerConnectionResolver`（另注：`hooksService` 与 `mcpService` 已迁成 `data`） |
| `internal(object)` | 2 | 正在迁往操作面的原生对象包装 | `fileStorage`（数据面 `fsHost`）、`hostPlatform`（目标数据面 `execHost`） |

`xtask check` 对这 111 个条目执行六选一硬校：既共享又没 dispatcher 报错，有 dispatcher 却没共享报错，既不共享又不标明合法迁移原因报错，把共享和内部标记混用也报错。任何想跑在独立节点或跨语言运行时里的叶子部件（leaf-ready），要求它 `inject` 和 `provides` 的每一个名字都必须是 `data`。

## 真实跨语言插件长什么样

在 `runtime/plugins/` 下，仓库自带了两个用 TypeScript 写的生产级跨语言插件，正好展示了这套数据面不是空壳：

**`runtime/plugins/workflow`（多作用域工作流引擎）**，它的 `dim-plugin.json` 只占一个进程行，入口 `index.ts` 直接注入 7 个 Rust 提供的进程数据面：`sessions`、`settings`、`pluginFiles`、`workflowFiles`、`managementMethods`、`partRegistry` 和 `processActivity`，同时反向向全进程提供一个异步数据面 `workflowRuns { call: "async" }`。进程入口在 `apply` 里注册工作流设置源、注册管理 RPC 方法，再通过 `partRegistry.registerDefinition` 把 `session.ts` 里的 Session 部件挂进每一个会话，给模型提供 `workflow` 工具。整个引擎（定义解析、DAG 调度、持久化、状态机）全用 TypeScript 写成，却与 Rust 宿主无缝共享会话启动、停止、权限上限校验和空闲关机保护（`processActivity.hold`）。

**`runtime/plugins/light-ocr`（极简跨语言能力提供者）**，整个 `index.ts` 不到 35 行，不注入任何宿主服务，只在 `apply` 里提供一个单方法异步数据面 `ocrHelper { describe: "async" }`，告诉 Rust 侧的 `dim-ocr` 和 `dim-modality` 本地 `node` 可执行文件路径、`helper.cjs` 位置和只读目录列表，随后实际的受限子进程执行仍交回 Rust 的 `HostPlatform` 监管。

同时，`xtask types` 会根据所有标记了 `#[rutis::service]` 的 Rust 类型自动生成 `npm/branch-services.d.ts`（在 `rutis 0.23` 下全部输出为 `type` 别名而非 `interface`），并由 `xtask check` 检查新鲜度。只要 Rust 侧改了一个数据面字段，TypeScript 侧的类型定义就会立刻报过时。

有了这套单行装载、跨语言数据面和热卸载基础设施，Dim 才得以实现它最具辨识度的产品场景：让 Agent 在正在运行的会话里给自己写一个 TypeScript 插件，当场安装、立刻调用、改完热重载。
