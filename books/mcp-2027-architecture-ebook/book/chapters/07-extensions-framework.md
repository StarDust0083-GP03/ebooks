# 扩展机制与治理边界

任何通用协议在爆火后的第二年都会撞上同一堵墙：社区每周都在提新需求，有的想要异步长任务，有的想要嵌入式交互 UI，有的想要企业 IdP 策略接入，有的想要事件推送或文件上传。把这些特性全部塞进核心规范，结果一定是核心体积失控、SDK 维护者疲于奔命、每一次小改动都要拉动整个社区做破坏性升级；反过来，如果核心规范一概拒之门外，各家大厂和开源项目就会各自发明互不兼容的私有 `_meta` 字段和魔改方法，协议很快四分五裂。

MCP 在 `2026-07-28` 给出的工程解法，是把核心规范瘦身到最小公约数，再用 **`SEP-2133`（Extensions）** 与 **`SEP-3392`（Extension Lifecycle）** 搭出一套分层演进的扩展治理体系。今天我们看到的 Skills、Tasks、Events、Apps、Server Cards 和企业授权，全都是这套机制运转出来的产物。

## 命名空间、能力协商与默认关闭原则

扩展机制的设计非常克制，不引入额外握手，直接复用核心协议的能力声明结构（`docs/extensions/overview.mdx`）。

**反向域名标识符**，每个扩展用 `{vendor-prefix}/{extension-name}` 格式唯一命名，前缀强制必填。官方扩展统一使用 `io.modelcontextprotocol/` 前缀（如 `io.modelcontextprotocol/skills`、`io.modelcontextprotocol/ui`）；第三方或企业内部扩展则使用自己拥有的反向域名（如 `com.example/my-extension`），从命名源头杜绝冲突。

**双向按需协商**，服务端在 `server/discover` 的 `capabilities.extensions` 中声明自己支持哪些扩展及配置对象；客户端则在每条请求的 `_meta["io.modelcontextprotocol/clientCapabilities"].extensions` 中显式声明自己当前请求开启了哪些扩展。空对象 `{}` 表示开启该扩展且无额外参数。如果客户端连接的是 `2025-11-25` 旧版服务端，许多 SDK 也支持在旧 `initialize` 握手的 `capabilities.extensions` 里兼容传递相同结构。

**默认关闭与优雅降级**，规范定了一条硬规则：**无论扩展处于哪个成熟阶段，在 SDK 和客户端里都默认关闭，必须由开发者或宿主显式选择启用（opt-in）**。SDK 维护者拥有完全自主权决定支持哪些扩展，不受核心一致性测试的强制捆绑。当通信双方只有一侧支持某扩展时，支持方必须回退到核心协议行为（例如支持 MCP Apps 的服务端在面对纯文本客户端时，依然要返回可读的文本 `content`）；只有当该扩展对业务属于硬前提时（比如某工具必须异步创建 Task 或强制走企业托管授权），服务端才通过 `-32021`（`MissingRequiredClientCapability`）或授权错误明确拒掉请求。

## `SEP-3392`：四阶段成熟度生命周期

早期的 `SEP-2133` 只区分了实验仓库（`experimental-ext-*`）和官方仓库（`ext-*`），结果很快暴露出一个现实问题：实现者分不清哪个官方扩展已经定型可以放心上生产，哪个还在剧烈改动。为了把成熟度信号透明化，`SEP-3392` 在 2026 年 9 月落地，把扩展生命周期明确划成了四个阶段，并直接用仓库目录路径标明状态：

| 阶段 | 仓库与目录路径 | 允许的变更范围 | 审批权限 | 准入门槛 | 对实现者的承诺 |
| --- | --- | --- | --- | --- | --- |
| **Experimental** | `experimental-ext-<name>` 或 `ext-<area>/specification/experimental/` | 随时可做任意变更，无预先通知 | Extension Maintainer | 关联 WG/IG，维护者即可创建 | 无稳定承诺，建议锁死 tag 或 commit |
| **Beta** | `ext-<area>/specification/beta/<name>` | 优先增量变更；破坏性变更应罕见、打包公告并递增 `revision` | Extension Maintainer | 提交 Extensions Track SEP，且至少有 1 份官方 SDK 参考实现 | 欢迎真实业务试用，文档记录破坏性变更 |
| **Stable** | `ext-<area>/specification/stable/<name>/<revision>/` | 已发布修订版不可变，改动只在核心协议新版本发布时生效 | Extension Maintainer（升阶需 Core Maintainer 批） | 第二份 Extensions Track SEP，须具备客户端与服务端两份独立互操作实现及一致性测试场景 | 在同一核心协议版本内行为完全冻结 |
| **Core** | 原扩展移入 `specification/archived/` | 并入主规范，遵循核心发布周期 | Core Maintainers | Standards Track SEP | 成为核心协议默认能力，无需再单独声明扩展 |

这里有一个精巧的版本处理细节。按 `SEP-2133` 最初的写法，只要发生破坏性变更就必须换新标识符（如 `-v2`），容易造成标识符泛滥。`SEP-3392` 修正了这一点：阶段跃迁时标识符保持不变；Experimental 和 Beta 阶段如果确实需要破坏性变更，可以在扩展设置对象里增加或递增 `revision` 字符串，对端不认识该 `revision` 就自动不启用扩展；到了 Stable 阶段，扩展行为直接锚定在双方协商的核心协议版本号上（如 `2026-07-28`）。只有当新旧两种不兼容行为必须在同一个核心协议版本里并存时，才需要启用 `-v2` 新标识符。

## 截至 2026-11-24 的官方扩展全景与客户端矩阵

把 `docs/extensions/overview.mdx` 和 `docs/extensions/client-matrix.mdx` 的最新记录整理成一张状态表，就能看清 2027 年 Agent 运行时栈的真实成熟度，既不把 Beta 当成已经凝固的铁律，也不低估已经大规模落地的能力：

| 官方扩展 | 标识符 | 仓库 | 当前阶段（2026-11-24） | 社区矩阵记录的代表支持客户端 |
| --- | --- | --- | --- | --- |
| OAuth Client Credentials | `io.modelcontextprotocol/oauth-client-credentials` | `ext-auth` | **Stable** | `mcpc` MCP CLI 及官方 TS/Python SDK |
| Enterprise-Managed Auth | `io.modelcontextprotocol/enterprise-managed-authorization` | `ext-auth` | **Stable** | Archestra.AI、`mcpc`、Glean |
| MCP Apps | `io.modelcontextprotocol/ui` | `ext-apps` | **Stable** | Claude Web/Desktop、ChatGPT、Cursor、VS Code Copilot、M365 Copilot、Goose、Postman、MCPJam、Glean |
| MCP Tasks | `io.modelcontextprotocol/tasks` | `ext-tasks` | **Beta（目标并入 Core）** | Claude Web、Claude Code、MCPJam、Glean |
| Skills over MCP | `io.modelcontextprotocol/skills` | `ext-skills` | **Beta** | Claude Web、Claude Code、VS Code Copilot、MCPJam、Glean、Archestra.AI；ChatGPT/fast-agent/Inspector 部分支持 |
| MCP Events | `io.modelcontextprotocol/events` | `ext-events` | **Beta** | MCPJam（全模式）；ChatGPT（Webhook 部分支持） |
| MCP Server Cards | `ext-server-card` | `ext-server-card` | **Beta** | Python/Go SDK 与 Goose 实验性实现 |

除了这七项已毕业进入 Beta/Stable 的官方扩展，GitHub `modelcontextprotocol` 组织下还有五个处于 **Experimental** 孵化期的前沿方向，很值得做平台架构的团队提前留意：

- `experimental-ext-interceptors`：拦截、校验和变换工具调用与资源读取的中间件协议。
- `experimental-ext-variants`：让同一台服务器暴露多个变体供客户端按场景挑选。
- `experimental-ext-tool-annotations`：更细粒度的动作元数据与信任注解草案。
- `experimental-ext-file-uploads`：客户端与工具之间的带外文件传输机制。
- `experimental-ext-filesystems`：超越单次 `resources/read` 的资源目录列举、搜索与写入操作。

看清这张全景图，团队在做 2027 技术选型时就能掌握节奏：凡是已经进入 **Stable** 的能力（`2026-07-28` 无状态核心、`ext-auth`、`ext-apps`），可以放心按规范长期投入；处于 **Beta** 的高价值扩展（`tasks`、`skills`、`events`、`server-card`），架构上应当尽早按其接口契约预留抽象层，同时通过特性开关和适配器隔离协议小幅演进带来的变动成本。

接下来四章，我们逐一深入拆解决定 2027 年 Agent 形态的四项核心扩展：先从终结 CLI 配合 `SKILL.md` 与 MCP 对立之争的 **Skills over MCP** 开始。
