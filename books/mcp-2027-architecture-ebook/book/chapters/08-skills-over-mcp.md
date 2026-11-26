# Skills 进入 MCP：`io.modelcontextprotocol/skills`

2026 年上半年，团队用 Agent Skills 配合外部服务时，普遍撞上了一个维护噩梦：研发一边维护远端的 SaaS MCP Server 或命令行工具，一边还得单独建一个 Git 仓库分发 `SKILL.md` 和配套参考文档，挨个催同事拉取更新。一旦后端工作流改了参数约定、公司新上线了一条工单分派规则，半数人的本机硬盘里跑的还是三个月前的过期 `SKILL.md`。把几百行工作流硬塞进 `server/discover` 的 `instructions` 字段也不行，比如 `SEP-2640` 里提到的 `mcpGraph` 技能光一份说明书就超过 875 行，全量常驻系统提示会把上下文吃光。

**Skills over MCP**（`io.modelcontextprotocol/skills`，`SEP-2640` 已达 Final，扩展目前处于 Beta 阶段）把 Agent Skills 正式搬进了 MCP 协议内部，直接复用核心的 Resources 原语作为传输层，技能目录格式与渐进披露规范则完整委托给上游 `agentskills.io` 规范，不在 MCP 里重复定义。Philipp Schmid 在原文中指出，Anthropic 已在 Claude 和 Claude Code 上向公众开放了 Skills over MCP 测试版，Shopify、HubSpot 和 Atlassian 等服务已经能够按当前登录用户的账号与组织，实时下发定制化的业务 Playbook。下面这张图展示了从轻量目录发现到 SHA-256 摘要校验与热更新的完整生命周期。

![Skills over MCP 渐进披露与 SHA-256 摘要更新流程](../../wiki/assets/diagrams/skills-sync-lifecycle.svg)

图里把整个流程切成了四个清晰阶段：连接期只注入极轻的元数据，按需加载前必须核对清单和用户审批，拉回来的每个文件都要校验字节数与 SHA-256 摘要，远端一旦改动技能内容，旧审批自动失效并触发刷新重确认，彻底结束了手工同步 `SKILL.md` 的割裂时代。

## `skill://` 寻址规则与三个新增协议方法

在 `SEP-2640` 中，一个 Skill 依然是一个包含根目录 `SKILL.md` 及可选支撑文件（`references/`、`scripts/`、`examples/` 等）的逻辑目录，每个文件都作为一条标准 MCP Resource 暴露。服务端**应当**使用 `skill://` URI 方案，结构为：

```text
skill://<skill-path>/<file-path>
```

其中 `<skill-path>` 可以是单段（如 `skill://git-workflow/SKILL.md`），也可以带任意层级的组织前缀（如 `skill://acme/billing/refunds/SKILL.md`），但最后一段**必须**与 `SKILL.md` 头部 YAML Frontmatter 里的 `name` 字段完全一致。规范特意强调两点：第一，`skill://` 的首段在 RFC 3986 语法里占了 authority 位置，但这里没有任何网络含义，客户端绝不能对它做 DNS 或网络解析；第二，服务端也可以用自有域名的方案（如 `github://owner/repo/skills/refunds/SKILL.md`），宿主**严禁**仅凭 URI 协议头就认定某个资源是技能，唯一可信的认定来源只有 `skills/list` 或 `skills/get` 的返回记录。

声明 `io.modelcontextprotocol/skills` 扩展的服务端必须同时声明核心 `resources` 能力，并实现以下方法（`docs/extensions/skills/overview.mdx`）：

- **`skills/list`（必填）**：枚举服务端公开的技能清单，支持 `cursor`/`nextCursor` 分页与 `SEP-2549` 的 `ttlMs`/`cacheScope` 缓存字段。每个条目必须是原子完整的（清单不得跨页拆分），包含 `uri`、原封不动解析出的 `frontmatter`（至少含 `name` 和 `description`），以及完整文件清单 `resources`（每项含 `uri`、`digest` 和字节 `size`），或者针对动态生成技能标注 `"resources": "dynamic"`。
- **`skills/get`（必填）**：按 URI 直接获取单个技能条目。规范允许目录极其庞大或按需生成的服务端在 `skills/list` 里只返回空列表或精选子集，但对它服务的每一个技能都**必须**响应 `skills/get`，未知 URI 返回 `-32602`（Invalid params）。这样，用户、另一个技能或 `server/discover` 的 `instructions` 可以直接指向一个未列举技能的 URI，由宿主按需拉取。
- **`resources/read`（复用核心原语）**：读取 `SKILL.md` 及支撑文件的实际字节内容。
- **`resources/directory/read`（可选）**：仅当服务端在扩展设置里显式声明 `directoryRead: true` 时客户端才可调用，用来分页列举目录的直接子项（子目录使用 `mimeType: "inode/directory"`，目录 URI 不带末尾斜杠）。

规范还给服务端划出了体量建议：单个 Skill 包含 `SKILL.md` 在内**不应超过 512 个文件或 16 MiB**，宿主则必须能完整支撑这一上限。

## 渐进披露怎么压住上下文开销

把 Skills 搬进 MCP 之所以不会重演 2025 年上下文膨胀的老路，靠的是严格的三段式渐进披露，规范甚至用了 **MUST NOT** 约束宿主不得偷懒预加载：

1. **连接期只取摘要**：宿主调用 `skills/list`，只把每条技能的 `name`、`description` 和来源服务器标识放进系统提示，不拉取任何文件内容。规范明文规定，宿主在连接、列举甚至请求用户审批期间，**都不得提前读取技能文件**，用户审批直接绑定到清单元数据即可。
2. **命中后再读正文**：只有当模型或用户明确选用某个技能，且通过所需审批后，宿主才调用 `resources/read` 拉取对应的 `SKILL.md`。注意，普通路径直接调 `resources/read` 读到一份 `SKILL.md` 并不会自动激活技能；必须经过宿主的技能加载通道完成校验后，才算真正载入上下文。
3. **支撑文件按需延迟加载**：`SKILL.md` 里提到的相对路径（如 `references/checklist.md`）由客户端相对于技能根目录解析成 `skill://code-review/references/checklist.md`，等执行流程真走到那一步，再向同一台来源服务器按需读取。

如果服务端连接的是登录态远程 SaaS（如 Shopify、Atlassian 或内部运维平台），`skills/list` 还可以根据当前 OAuth 令牌代表的账号或组织（配合 `cacheScope: "private"`），动态返回专属的团队工单模板、审批规范或店铺运营剧本。这是静态托管在 Git 仓库里的本地 `SKILL.md` 根本做不到的能力。

## SHA-256 清单、热更新与五条防投毒红线

把远程服务器下发的 Markdown 指令直接喂给 Agent，天然带着供应链篡改和提示注入风险。`SEP-2640` 在完整性和信任边界上写得相当严密，核心靠清单里的 SHA-256 摘要把热更新和安全审批绑成了一体：

1. **严格限定在保留清单内读取**：技能处于活跃期（至少直到 `SKILL.md` 移出模型上下文）内，宿主必须保留加载时的条目清单，禁止通过该技能读取任何不在清单里的 URI。即使调用了 `resources/directory/read` 看到了服务端新冒出来的文件，宿主也不得把它当作当前已批准技能的一部分，必须走刷新和重审批流程。
2. **三重落地校验**：每次 `resources/read` 拿到字节流后，宿主在交付模型前**必须**校验三项：原始字节长度是否等于清单 `size`、计算出的 SHA-256 是否等于清单 `digest`、解析出的 `SKILL.md` YAML Frontmatter 是否与条目里的 `frontmatter` 逐字段一致。任意一项不符，立刻丢弃内容，不得给模型看。
3. **变更即撤销旧审批**：持久化的用户审批必须绑定到**完整的（文件 URI，摘要）集合**。只要远端厂商修改、新增或删除了技能里的任意一个文件，哪怕改了一个标点，摘要就会立刻变化，本地以 `(serverId, uri, sha256)` 为键的缓存自动失效，旧审批随之作废；宿主调用 `skills/get` 拉取新清单并再次向用户确认后，才能加载新版本。对于没有固定摘要的 `"resources": "dynamic"` 技能，宿主有权直接拒载，若允许加载也绝不能把历史审批泛化覆盖到未来任意动态生成的内容上。
4. **来源标识与跨服务器隔离**：技能的唯一身份是 `(originatingServerId, skillURI)`，光靠 `name` 不唯一，宿主绝不能允许两个不同服务器的同名技能静默互相覆盖。所有加载进上下文的技能内容必须打上来源服务器标签，且所有支撑文件读取必须锁死在来源服务器上；如果一段技能指令试图诱导 Agent 去读另一台服务器上的资源，宿主必须拦截并弹出明确标明双方服务器名称的逐次确认框。
5. **不可信内容与防嵌套提权**：技能正文一律视为不可信外部输入。凡是涉及宿主侧代码执行或类似 `allowed-tools` 的权限扩张，都必须按技能逐一获得显式人工批准。如果一个技能目录的子目录里嵌套了另一份 `SKILL.md`，父技能读取它时只能把它当普通参考文本，宿主严禁静默解析并激活嵌套技能的 Frontmatter；想激活嵌套技能，必须走独立加载和全新同意流程。落盘缓存的远端技能文件还必须设为对模型、工具及其他用户只读不可篡改（或每次读取重验哈希），并且**必须排除出本地文件系统技能自动发现路径**，防止重启后失去 MCP 来源标记被宿主误当成受信任的本机技能。

这一整套机制在第 14 章的 `SkillsServer`、`SkillHostLoader` 以及 `test_13` 至 `test_15` 中都有零依赖代码验证。

当服务端不仅能交付强类型工具，还能交付带版本校验和账号定制的工作流剧本之后，同步拉取这一侧的短板基本补齐了。但现实工程里还有大量活根本不可能在几秒钟的 HTTP 请求里跑完，比如跑一次 15 分钟的 CI 流水线、做一轮全库迁移、或者等一位审核人半小时后点批准。下一章我们看面向长程异步作业的 **MCP Tasks**。
