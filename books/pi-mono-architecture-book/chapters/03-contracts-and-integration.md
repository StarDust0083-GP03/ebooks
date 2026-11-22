# 第 7 章：工具太多时，如何按需发现而不塞满提示

接入三五个 MCP 服务以后，注册表里很容易冒出一两百个工具。如果照传统做法把全部工具名、说明和参数 schema 一次性塞进每次模型请求，提示词体积会立刻膨胀，模型选工具的准确率下降，工具列表只要微调还会连带击穿 prompt 缓存。

Pi 把工具注册、向模型声明和允许脚本调用拆成了三个独立开关。一个工具可以已经注册、可以供脚本调用，却完全不写进模型的工具声明列表；也可以等模型主动搜索后再按需声明。**工具在提示词里不可见，不等于它不可用，也不等于权限被收回。**[S16](#s16)、[S17](#s17)

![直接声明、脚本发现与按需加载三条工具路径：exposure 控制模型可见面与脚本调用面](diagrams/discovery.svg)

### MCP 的四种 exposure 策略

| MCP exposure | 默认向模型声明？ | 脚本可调用？ | 典型适用场景 |
|---|---|---|---|
| `direct` | 是，受当前活动工具集约束 | 是 | 极少数高频核心工具 |
| `codemode` | 否，默认也不展开进 Code Mode 说明 | 是 | 大多数 MCP 服务，先发现再组合调用 |
| `deferred` | 否，由 `tool_search` 加载后声明 | 是 | 希望发现后按普通工具直接调用 |
| `hidden` | 否 | 否 | 保留配置条目但使其完全不可达 |

MCP 工具默认走 `codemode`。普通扩展工具还多一种 `model-only` 策略，适合像 `codemode` 自身或交互问答这类只给模型直接调用、禁止其他工具嵌套调用的编排器。别把 MCP 的 exposure 配置和底层扩展工具属性硬揉成一张表：MCP 扩展还叠加了命名空间前缀、延迟连接和全局过滤，最终可达性要看禁用清单、工具筛选与活动集合的交集。[S15](#s15)、[S16](#s16)

### 脚本里的四个发现入口

```js
const hits = await searchTools("search documentation", { limit: 3 });
text(hits);
```

脚本侧提供四个分工明确的查询入口：`ALL_TOOLS` 给出当前可调用目录的名称与简述；`searchTools(query, options)` 做相关性排序召回候选；`describeTool(name)` 返回单个工具的完整说明与 TypeScript 声明；`describeNamespace(name)` 读取某个命名空间的服务指令和工具列表。

三个查询 helper 都是异步函数，调用时必须带 `await`。拿到候选后要先调用 `describeTool()` 核对参数字段，别凭名字盲猜参数直接调用。[S10](#s10)

```js
const hits = await searchTools("read documentation page", { limit: 3 });
if (hits.length === 0) return "没有找到候选工具";
text(await describeTool(hits[0].name));
```

发现结果交回给模型后，下一轮模型再据此写出精确的参数调用。把探索接口和执行操作分成两轮，比硬塞进一段赌运气的长脚本稳得多。

v1.5.0 到 v1.6.0 连续修通了工具发现里的几处常见暗礁：第一，旧版分词器只认 ASCII 字母和数字，中文、日文、泰文查询几乎搜不到结果；现在 `tool_search` 和 `searchTools()` 支持多语种与无空格中文切词（例如直接搜 `搜索记录`），也做了 Unicode 规范化匹配。第二，排序规则改成了工具名命中权重远高于描述词出现次数，且命中更多查询词的条目排在前面，避免搜 `peekaboo app` 时真正叫 `mcp__peekaboo__app` 的工具排到十几名开外。第三，v1.5.1 起索引器会沿着本地 `$ref` 展开 `$defs` 里的字段名和描述（带循环引用防护，遇到非法百分号转义降级为 `unknown` 而不让整个脚本崩掉）。第四，v1.6.0 修复了某个扩展在 `session_start` 里关闭自家工具时，顺带把 `tool_search` 之前动态加载进来的 MCP 工具在恢复或 `/reload` 后弄丢的问题。[S2](#s2)

### codemode.mode 的 on 与 only

默认配置是 `on`：已声明的工具保持直接可见，同时附带脚本编排提示。切到 `only` 后，底层工具从模型直接声明里隐藏，调用入口收拢到 `codemode`；但脚本依然能调用它们，隐藏声明只影响提示词呈现与编排方式，不会带来额外的操作系统隔离。

```json
{
  "defaultTools": ["+codemode"],
  "codemode": { "mode": "on", "inlineBudget": 3000 }
}
```

`inlineBudget` 按大约每四个字符折算一个 token 的方式估算内联声明预算，超出预算的工具交由发现接口按需查找。默认暴露为 `codemode` 的 MCP 工具本就不会逐个铺进说明里。v1.5.0 还缩减了复杂输出类型的内联声明：对象联合类型改由字段与鉴别器摘要展示，更长的多行类型则引导脚本调用 `describeTool()` 查看完整声明。[S2](#s2)、[S17](#s17)

### MCP 为什么不拖慢每次启动

自 v0.99.2 起，非 `direct` 的 MCP 服务器在后台异步建连，不卡住首个 prompt。具体等到什么时候才等待连接就绪，靠编排工具声明的 `reachesTools(input)` 契约决定：脚本里点名调用了 `tools.mcp__docs__search`，MCP 扩展只等 `docs` 这一个服务连好；如果脚本调用了 `searchTools()` 或访问 `ALL_TOOLS`，说明可能覆盖全目录，才等待全部延迟服务器就绪。v1.2.0 借由 `reachesTools` 把 MCP 和内置 Code Mode 彻底解耦，替换自定义编排器时只需实现同一契约即可联动延迟连接。[S2](#s2)、[S15](#s15)、[S16](#s16)

在连接与传输稳定性上，v1.5.0 与 v1.5.1 也有几项务实加固：MCP OAuth 登录默认改用 `oauth.clientRegistration: "auto"`，优先走 Client ID Metadata Document，服务端不支持再回退到动态客户端注册，并拒绝非 `http:`/`https:` 协议的授权地址；stdio 传输修复了大消息分片到达时反复拷贝扫描的卡顿，POSIX 退出时能清理掉孤儿守护子进程；Streamable HTTP 与 SSE 则补齐了消息体积上限，超过上限或错误响应超过 8 KiB 会立即取消传输，不再把整份异常响应读进内存。[S2](#s2)

### 一条容易误判权限的 CLI 参数

```sh
pi --tools read,codemode
```

在当前版本里，上面这条命令**不会顺手删掉 MCP 工具**，脚本里依然能调到已配置的 MCP 服务。如果要彻底屏蔽 MCP，必须显式加上 `--no-mcp`；要按名称筛选 MCP 用 `mcp__` 模式，排除特定工具用 `--exclude-tools`。千万别单看 `--tools` 白名单就以为做完了权限收口。[S16](#s16)、[S19](#s19)

# 第 8 章：返回值、失败处理、并发与状态边界

写 Code Mode 脚本时，语法错误很少见，真正让人半夜翻车的几乎全出在契约误判上：以为每个工具都返回字符串，以为命令失败一定会 `throw`，或者以为脚本报错会自动把前面写坏的文件回滚回去。

### 三类高频工具的返回形态

| 工具类别 | 脚本实际拿到的返回值 | 必须主动核对的字段 |
|---|---|---|
| `read` 读文本 | 字符串（读图片时为图片块对象） | 内容是否被截断、能否安全按预期格式解析 |
| `bash` | `{ output, exit_code, truncated, full_output_path?, ... }` | `exit_code`，Promise 正常 resolve 也可能命令执行失败 |
| MCP 工具 | 完整 `CallToolResult`（含 `content`、`structuredContent`、`isError`） | 先看 `isError`，再读结构化字段，别只取第一条文本就判成功 |

声明了 output schema 的工具会直接把结构化对象交给脚本；参数校验失败、扩展钩子拦截或内部异常才会 reject Promise。MCP 工具保留了协议原样对象，因此必须显式检查 `isError`。一切以 `describeTool()` 返回的声明为准。[S10](#s10)、[S14](#s14)、[S16](#s16)

```js
const result = await tools.bash({ command: "npm test" });
if (result.exit_code !== 0) {
  text({ passed: false, tail: result.output.slice(-4000) });
} else {
  text({ passed: true });
}
```

调用 MCP 工具时的判错套路如下（工具名与参数仅作契约示意，使用前需先经发现接口确认）：

```js
// 伪代码：工具名需先通过 searchTools / describeTool 核实
const result = await tools.mcp__docs__search({ query: "session" });
if (result.isError) throw new Error(JSON.stringify(result.content));
text(result.structuredContent ?? result.content);
```

顺便记住扩展侧调用外部命令的 `pi.exec()` 在 v1.5.0 和 v1.6.0 的变化：它新增了 `maxBuffer` 选项（默认上限为 256 MiB 与运行时字符串上限中的较小者，超限会终止进程并把 `truncated` 标为 `true`），超时或取消发 `SIGTERM` 后若 5 秒内进程仍不退出会补发 `SIGKILL`，而且从 v1.6.0 起，被信号杀死的命令不再误报退出码 `0`，而是照 shell 惯例返回 `128 + 信号编号`（例如 `SIGTERM` 为 `143`，`SIGKILL` 为 `137`）。[S2](#s2)

### 两层成功状态要分开判

`Promise.allSettled()` 里某一项拿到 `status === "fulfilled"`，只代表跨桥调用正常返回，既不代表 shell 退出码为零，也不代表远端业务执行成功。独立沙箱的 `sandbox.execute()` 遇到脚本内部抛错时，同样是正常返回 `{ ok: false, error }` 而不是直接 reject。把传输完成、命令退出码和业务成功拆成三道检查，脚本才不容易静默吞错。

### await 直接决定在途调用的生死

```js
// 错误写法：脚本立刻返回，尚未完成的 read 会被取消
tools.read({ path: "package.json" });
return "完成";
```

```js
// 正确写法：等依赖调用落袋再输出
const source = await tools.read({ path: "package.json" });
text(JSON.parse(source).name);
```

脚本一旦结束，所有还没跑完的在途工具调用都会收到取消信号；而已经执行完的外部写操作无法撤回。`Promise.all()` 只要有一项提前 reject，同样会带着外层脚本提前终止并取消同批其他调用。互不依赖的只读查询优先用 `Promise.allSettled()`，有读写因果的操作按顺序串行执行。

### store 只管分支内小状态，不管文件回滚

`store(key, value)` 和 `load(key)` 用来在同一会话分支内跨脚本保存小型 JSON 数据。CLI 只有在整段脚本成功结束后，才会把变更追加成一条 `codemode-store` 会话条目；单值 JSON 长度上限 262,144 字符，总量上限 1,048,576 字符。重新打开会话或用 `/tree` 切换分支时，Pi 只沿着当前路径重放状态。[S10](#s10)、[S14](#s14)

![分支状态重放与外部文件副作用的分离：切回旧分支会恢复 store，但磁盘文件保持不变](diagrams/branch-store.svg)

```js
const count = (load("inspected") ?? 0) + 1;
store("inspected", count);
text({ count });
```

假如脚本先调用 `tools.write` 改了磁盘文件，接着调用 `store()`，最后几行抛了异常：结果是 `store` 变更不会提交，但磁盘上那个文件已经被改掉了。分叉会话也一样，分支 A 写进 `store` 的游标不会漏进分支 B，可两边面对的是同一份文件系统。因此 `store` 只适合放分页游标、已筛选 ID 或短摘要，大体积日志、源码全文和图片 base64 应当留给文件或外部服务。

### 取消、真实退出与 worker FIFO 排队

同一个 Pi 进程里，Code Mode 默认最多同时存活两个 worker，跨会话共享；通过环境变量 `PI_CODEMODE_MAX_WORKERS` 可以设成更大的正整数，非法值自动回退到 2。由于 Bun 下某些不轮询中断的同步内建操作在取消后还会拖一小会儿才退出，v1.3.0 把立即向调用方返回取消结果，和等 worker 真正退出后再归还并发名额拆成了两件事，防止反复取消把僵尸 worker 越攒越多。[S2](#s2)、[S13](#s13)、[S14](#s14)

v1.4.0 在此基础上补上了排队机制：名额占满时，`reserveCodemodeWorker()` 不再直接报错拒绝，而是把等待者排进 FIFO 队列，等已有 worker 真正释放后由 `admitCodemodeWorkers()` 按顺序放行。排队最长等 60 秒，并且**等待耗时直接算在脚本自己的总 `timeout_ms` 里**。如果脚本总时限 10 秒、排队用掉 8 秒，实际能跑的时间就只剩 2 秒，拿到名额不会重算计时。[S26](#s26)

还要分清两组并行额度：进程级默认的两个 worker 管的是同时运行的脚本实例数量；单个脚本里并发发起十个互不依赖的普通工具调用，只占这一个 worker。而脚本内调用 `models.classify()` 和 `models.generateImages()` 另受每脚本最多 4 项在途请求的限制。脚本自身禁止嵌套启动另一个 `codemode`，调高 `PI_CODEMODE_MAX_WORKERS` 会同步推高内存与外部请求峰值，需要根据宿主资源评估。

### 四类高频故障的排查顺序

| 报错现象 | 优先检查的点 | 稳妥的恢复动作 |
|---|---|---|
| `tools.X` 不存在或报错 | 拼写、命名空间前缀、工具是否被过滤或处于 `hidden` | 走 `searchTools` / `describeTool` 核实，别反复瞎猜 |
| `JSON.parse` 失败 | 返回值是否被工具截断、混入日志或拿到错误页 | 换结构化工具，或先缩小读取范围 |
| 等待空闲 worker 超时 | 全进程在途脚本、残留未退出 worker 与总 deadline | 检查谁占着名额，别写死循环高频重试 |
| 超时或部分失败 | 已经落盘或发往外部系统的副作用 | 先核查实际外部状态再决定是否重试，写操作需幂等 |

# 第 9 章：扩展、渲染缓存、模型与集成界面

把一个长期工程接进 Pi 时，最容易犯的架构错误是把 Code Mode 当成万能胶。其实五种定制入口各守一块地盘：临时多步工具编排归 Code Mode，挂进宿主进程的可执行能力归扩展，按需加载的领域指令归 skill，可复用输入归提示模板，终端配色归主题。

### 扩展的打包、生命周期与诊断

自 v0.35.0 把 hooks 和自定义工具合为统一的 extensions 后，扩展就是一个接收 `ExtensionAPI` 的 TypeScript 工厂函数，直接运行在 Pi 宿主进程里，可以访问本地文件、凭据和会话状态，不受 QuickJS 那套沙箱限制。加载来源不明的扩展等同于在本地跑第三方代码。[S2](#s2)、[S15](#s15)

```ts
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.registerCommand("hello", {
    description: "显示问候",
    handler: async (name, ctx) => {
      ctx.ui.notify(`你好，${name || "世界"}`, "info");
    },
  });
}
```

写扩展要盯牢四条生命周期和打包规矩：

第一，工厂函数里只做注册，别一上来就起文件 watcher、socket 或定时器，因为有些命令只加载扩展并不会开启会话。长寿命资源放进 `session_start` 启动，并在 `session_shutdown` 里幂等回收。顺带一提，v1.6.0 修掉了一个很折磨 SDK 宿主的 bug：以前 `AgentSession.reload()` 发出 `session_shutdown` 后漏发了 `session_start`，导致像 MCP 这样靠 `session_start` 建连的扩展在重载后一直处于断开状态；现在重载流程会完整触发重新启动。[S2](#s2)、[S15](#s15)

第二，状态按可见性分开放。分支相关的工具细节放结果 `details`，不给模型看的持久元数据用 `appendEntry()`，需要模型参与的自定义消息用 `sendMessage()`，跨会话数据走外部存储；重建分支状态始终读 `getBranch()`，别扫全文件把别的分支混进来。

第三，打包发布扩展包时，宿主提供的五个共享包（`@earendil-works/pi-ai`、`pi-agent-core`、`pi-coding-agent`、`pi-tui`、`typebox`）必须声明在 `peerDependencies`（范围 `"*"`）而非 `dependencies`，避免在 ESM 环境里装出双份类实例和注册表。v1.5.0 新增了扩展包更新日志展示：只要包里包含至少一个扩展，升级版本号后下次冷启动就会自动读取根目录 `CHANGELOG.md`（或 `package.json` 里的 `pi.changelogPath`，设成 `""` 可关闭），在 Extension Updates 区域列出新条目，用户可以通过 `collapseChangelog` 设置把每个包折叠成单行。同期，工具与消息渲染上下文里开始直接注入 `keyText()` 和 `keyHint()`，避免扩展自己 import 一份副本却读不到用户改过的快捷键；v1.6.0 还修通了单文件独立二进制解析扩展 npm 依赖 `package.json` 里 `main` 和 `exports` 入口的问题。[S2](#s2)、[S36](#s36)

第四，v1.6.0 收拢了扩展和配置告警的输出通道。过去 `/reload`、`/new`、`/resume` 期间一旦触发 `git`/`npm` 刷新输出、`pi.events` 处理器异常或 `enabledModels` 告警，文字会直接冲刷覆盖全屏 TUI 画面。现在交互模式统一收进聊天区展示，print、JSON 和 RPC 模式写往 stderr，且通过 `AgentSessionRuntime.takeUnreportedDiagnostics()` 去重，防止每切换一次会话把同一条警告打印一遍。[S40](#s40)

### v1.5.0 到 v1.6.0 的性能主线：增量流式解析与 TUI 只读渲染缓存

聊长了、工具参数动辄几十 KB 时，很多终端 Agent 都会突然卡成幻灯片：一边是每个流式字符块都在重头解析整段 JSON，一边是每来一个增量或滚一下滚轮都在把整屏上千行历史重新高亮和拷贝一遍。v1.5.0 和 v1.6.0 连续对这条热路径动了两次大手术。

![v1.5.0 至 v1.6.0 的流式工具参数解析与 TUI 只读渲染缓存链路](diagrams/streaming-and-render-cache.svg)

先看上半条链（v1.5.0，`packages/ai/src/utils/streaming-json.ts`）。过去 provider 每收到一小片工具参数 delta，就会把迄今累计的全部文本拿去做一次容错 JSON 解析，流越长 CPU 和内存就按 O(N²) 飙升。现在每个流式工具调用由 `ToolCallArgumentsStream` 持有原始文本和一个 `StreamingJsonParser` 实例：`append(delta)` 逐字符线性推进状态机，每个字符只扫一次；`snapshot()` 返回供 UI 预览的不可变纯 JSON 对象，只浅拷贝当前尚未闭合的开路径容器，已经闭合的子容器和字符串直接共享引用。[S30](#s30)

更有意思的是它防极端输入的拷贝信用机制：开路径上有多少成员和元素，拷贝一次就记多少 `#pathCost`。每次 `append(text)` 按 `CREDIT_PER_APPEND (256) + CREDIT_PER_CHARACTER (8) * text.length` 往账户里充信用；只有当信用够支付当前 `#pathCost` 时，`snapshot()` 才生成新快照，否则直接复用上一份快照。这样就算模型一口气往一个未闭合数组里吐几千个元素，预览最多略微滞后几帧，整体时间复杂度绝不会退化成平方级。等到调用结束，`finish()` 再拿完整原始文本做一次严格 `parseToolCallArguments()` 产出最终参数与错误诊断。[S30](#s30)

流出来的部分参数传到界面层后，`ToolExecutionComponent.updateArgs()`（`packages/coding-agent/src/modes/interactive/components/tool-execution.ts`）也不再收到一次 delta 就立刻重跑语法高亮和排版，而是只把 `argsPending` 置为 `true` 并调用 `ui.requestRender()`，把两帧之间涌来的几十个 delta 合并成**每帧最多渲染一次**。同一时期，`AssistantMessageComponent` 引入了 `MarkdownSlot` 槽位复用，流式回答过程中已经渲染完的 Markdown 块与代码高亮不再每帧重算。[S33](#s33)、[S34](#s34)

再看下半条链（v1.6.0，`packages/tui/src/tui.ts`，也是本版最重要的破坏性变更）。长会话在全屏模式下用鼠标滚轮滚动时，过去每滚一格都要把整段对话树逐层拼接拷贝，很容易把单核 CPU 打满。v1.6.0 把组件契约改成了：

```ts
render(width: number): readonly string[];
```

`Container.render(width)` 内部调用 `renderChildLines(this.children, width, this.childLines)`。只要宽度没变、子组件数组引用没变、每个子组件返回的渲染行通过 `sameLines()` 对比也没变，就直接把上一次缓存的 `ChildLines` 连同 `lines` 数组引用原样返回。未变化的子树保持数组引用不变，所有祖先容器都能跳过拼接拷贝。[S31](#s31)、[S32](#s32)

这项优化直接带来一条必须遵守的扩展开发铁律：**绝不能原地修改 `render()` 或 `super.render()` 返回的数组。** 直接往上面 `push` 或改下标，会当场污染容器缓存并破坏后续帧画面。标准做法看内置的 `AssistantMessageComponent`（`assistant-message.ts`）就行：先拿引用做身份比对，只在缓存源变了时拷贝一份再加首尾标记：

```ts
const lines = super.render(width);
if (this.zoneLines?.source !== lines) {
  const zoned = [...lines];
  zoned[0] = OSC133_ZONE_START + zoned[0];
  zoned[zoned.length - 1] = OSC133_ZONE_END + OSC133_ZONE_FINAL + zoned[zoned.length - 1];
  this.zoneLines = { source: lines, lines: zoned };
}
return this.zoneLines.lines;
```

除此之外，v1.5.1 还给 `ctx.ui.custom()` 覆盖层补上了 `blocked` 选项支持（通过 OSC 7501 通知终端当前正在等待用户回答或授权），并给 `pi.registerMarkdownTransformer()` 增加了 `{ messageTypes }` 选项，可以按需作用于 `custom`、`summary` 和 `changelog` 表面，内置 Mermaid 渲染也支持了饼图、自定义样式和展开摘要里的图表绘制。[S2](#s2)、[S32](#s32)

### 模型提供商、思考约束与图片分类

Code Mode 不会抹平各家模型提供商的协议差异，流式归一化、缓存保活和推理参数适配仍由 `packages/ai` 承担。从 v1.3.0 起，切换模型时会自动丢弃上一个模型产生的历史 thinking 块，避免它们被当成普通文本喂给新模型。[S2](#s2)

v1.5.0 到 v1.6.0 在模型矩阵和协议细节上有多处值得留意的工程约束：

- **GPT-6.1 Terra 与 GPT-6.2 Astra / Sol**：v1.5.0 先在 `openai`、`azure` 和 `openai-codex` 上加入 `gpt-6.1-terra`（Codex 下 500K 上下文，支持工具搜索、会话中途切换思考等级，且允许把思考关到 `off`）。v1.6.0 上线 `gpt-6.2-astra` 与 `gpt-6.2-sol`，并把 `gpt-6.2-sol` 设为三家提供商的默认模型（Azure 提供 1.05M 上下文，Codex 为 Sol 提供 500K、为 Astra 提供 272K）。注意 `packages/ai/catalog/families/openai.ts` 中的 `GPT_6_NO_NONE_EFFORT_MODEL_IDS` 集合明确包含 `gpt-6.2-astra` 和 `gpt-6.2-sol`：这两款模型拒绝 `none` 和 `minimal` 推理强度，因此无法开启 thinking `off`。同时，当 OpenAI 在压缩后丢弃旧推理条目（如 `prefix_mismatch`）且开启了 `showCacheMissNotices` 时，对话记录里会给出带原因的提示。[S38](#s38)
- **Qwen Token Plan 四套提供商**：v1.5.1 与 v1.6.0 补齐了 Team 与 Individual 两条线的国际站和国内站提供商（`qwen-token-plan`、`qwen-token-plan-cn`、`qwen-token-plan-individual`、`qwen-token-plan-individual-cn`），切换到新网关 `token-plan.maas.qwencloudapi.com` 与 `token-plan.maas.qianwenaiapi.com`（旧 `aliyuncs.com` 仍兼容），支持按套餐拆分 `QWEN_TOKEN_PLAN_TEAM_API_KEY` 等环境变量（保留共用变量回退），新增 `qwen4-max-preview` 和 `deepseek-v4.1-flash`，同时移除了下线的 `deepseek-v3.2` 与 `deepseek-v4-flash`。[S2](#s2)
- **分类器与图片输入**：脚本里的 `models.*` 可以查目录、调分类器和生图，但不允许把聊天模型当作任意子 Agent 直接递归调用。`models.classify()` 自 v1.4.0 起支持传入 `images`（适用于具备视觉能力的 Jev、Clef 分类器，v1.5.1 又接入了通过 GitHub Copilot 调用的 Microsoft-Decision-1 分类器）。模型类型是 `classifier` 不代表一定支持图片，调用前必须检查 `m.input?.includes("image")`，并按 `models-configuration.md` 核对代理的 `compat.imageInput` 与 `sendImageOptInHeader` 字段。图片展示走 `image(block)`，绝不用 `text()` 打印 base64。[S10](#s10)、[S27](#s27)、[S29](#s29)

下面这段脚本和 `examples/scripts/06-classify-image.js` 保持逐字一致，用来演示图片分类器的完整契约检查（真实运行需要可用图片分类器凭据与本地 `screenshot.png`，可能产生费用）：

```js
const candidates = await models.getAvailableOfType("classifier");
const vision = candidates.find(m => m.input?.includes("image"));
if (!vision) throw new Error("没有可用的图片分类器");
const photo = await tools.read({ path: "screenshot.png" });
if (photo.type !== "image") throw new Error("需要图片块");
const result = await models.classify(vision, {
  state: { task: "检查页面布局" },
  images: [photo],
  questions: {
    broken: {
      type: "bool",
      instructions: "图片中是否有重叠或被截断的内容？",
      criteria: { true: "存在布局缺陷", false: "未发现布局缺陷" },
    },
  },
});
if (result.stopReason !== "stop") {
  throw new Error(result.errorMessage ?? result.stopReason);
}
const answer = result.answers.broken;
if (answer?.type !== "bool") throw new Error("分类回答类型不符");
text({ probability: answer.probability });
```

### 命令行运维新入口：凭据管理与自定义自更新

在自动化或 Nix、mise 托管环境里，过去常遇到两个麻烦：一是为了给某个 provider 登录还得先启一个交互会话，二是 `pi update` 碰到非官方包管理器安装会直接报错放弃。v1.5.0 和 v1.5.1 把这两块补齐了：

- **自定义自更新命令（v1.5.0）**：设置环境变量 `PI_SELF_UPDATE_COMMAND`（按空白拆分参数，优先级最高）或在全局 `~/.pi/agent/settings.json` 里写 argv 数组 `selfUpdateCommand`（支持含空格的参数）。当 pi.dev 报告新版本时，`pi update` 会把 `{packageName}`、`{version}`、`{installSpec}` 替换成新版本信息并执行该命令，例如 `mise use -g npm:{packageName}@{version}` 或 `["nix", "profile", "upgrade", "pi"]`。[S19](#s19)
- **脱离会话的命令行登录（v1.5.1）**：在 shell 里直接运行 `pi auth login <provider>`（支持 `--api-key` 静默录入密钥、`--no-browser` 走设备码或复制码登录）与 `pi auth logout <provider>`，登录 Radius 时还会顺带配好对应的 MCP 服务器。配合 `pi auth check --json` 与 `pi update --extensions` 逐包版本差异报告，CI 和无头机器上的运维顺畅很多。[S19](#s19)

### 选哪一种集成接口

| 使用场景 | 推荐入口 | 需要提前注意的边界 |
|---|---|---|
| 人在终端里交互编码 | TUI | 默认全屏模式，可切回 regular；自定义组件遵守只读 `render()` |
| 单次脚本或管道任务 | `--print` | 跑完一轮后退出，无交互确认框 |
| 外部程序消费事件流 | `--mode json` | stdout 输出 JSONL 事件流，v1.6.0 修复启动期 `session_start` 事件丢失 |
| 非 TypeScript 宿主长连控制 | RPC | stdin 发指令，stdout 收响应与事件；关停期间等待清理完成 |
| Node / Bun 应用内嵌 | SDK | 宿主自管生命周期、资源加载、诊断读取与内置扩展绑定 |
| 只需要受限工具脚本引擎 | 独立 `pi-codemode` | 零 Pi 内部依赖，不必拖入整个编码代理 |
| 需要跨重启持久任务恢复 | 实验性 `pi-durable` | 独立存储、journal 与 replay 策略，属于实验 API |

TUI 只是事件消费者与终端绘制层，RPC 和 SDK 驱动的都是同一套 `AgentSession` 和 Agent 循环。写自定义交互组件时要检查 `ctx.mode` / `ctx.hasUI`，不能假定 print 模式下也有弹窗确认。另外，CLI 默认装配了内置 Code Mode、`tool_search` 和 MCP 三个扩展，而 **SDK 默认不会自动装好它们**，必须在 `DefaultResourceLoader.extensionFactories` 里显式传入，并在创建会话后调用 `session.bindExtensions({})` 触发 `session_start`（完整代码见 `examples/sdk-codemode-mcp.ts`）。[S9](#s9)、[S20](#s20)

如果企业要求每次执行 shell 前必须过合规审批，切忌把审批逻辑塞进模型生成的 Code Mode 脚本里。可审计的拦截规则应当落在扩展的 `tool_call` 钩子边界，而文件、进程和网络的硬隔离则交给容器和操作系统权限去守。
