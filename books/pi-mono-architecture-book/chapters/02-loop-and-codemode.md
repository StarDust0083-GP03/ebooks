# 第 4 章：一次请求的完整生命周期

在长会话里排查为什么模型突然忘了三轮前的结论，或者为什么切回旧分支后磁盘文件还在，根源往往出在同一件事上：把磁盘历史、界面展示和发给模型的上下文混成了一团。

把一条真实输入放进 v1.6.0 的主链里跟踪一遍，每一步的归属就清晰了：

1. 终端 TUI、SDK 或 RPC 收到用户输入。提示模板按需展开，扩展的输入钩子可以先拦截命令或改写内容。
2. `AgentSession` 接管本次运行准备：解析模型与思考等级、装配工具列表、检查自动压缩阈值、维护消息队列与扩展生命周期。
3. `SessionManager` 从当前叶子节点沿着 `parentId` 往上回溯，抽出活动分支，重建发给模型的投影上下文。
4. harness 把系统提示、活动分支消息、工具声明和模型参数打包发往 provider。如果命中 Anthropic 消息线程（`compat.supportsMessageThreads`，v1.6.0 起对任何 `api.anthropic.com` 端点默认开启），请求可以只发新增消息，由服务端续接上一轮回复；一旦发生压缩或工具集变化，则自动回退到完整上下文请求。[S29](#s29)
5. provider 推送流式事件。v1.5.0 起大型工具调用的 JSON 参数在这里经 `StreamingJsonParser` 增量解析，Agent 再把事件归一化为消息、工具与回合信号。
6. 若模型发出工具调用，进入 `pi-schema` 参数转换与校验、触发扩展 `tool_call` / `tool_result` 钩子、执行工具并落盘结果。
7. 只要还有未消费的工具结果、自动重试或排队消息，循环继续推进；全部清空后才进入最终收尾。

![一次 Code Mode 请求的跨层时序：嵌套工具结果先回到脚本，再由脚本筛选后交给模型](diagrams/request-sequence.svg)

时序图省去了逐块流式事件和重试分支，只留出最关键的一道墙：**脚本发起的嵌套工具调用，原始结果先回到 QuickJS，不会直接灌进下一轮模型上下文。**[S7](#s7)、[S9](#s9)、[S10](#s10)

### 同回合并行调用和 Code Mode 解决的问题不同

当前 `packages/agent` 的工具管线已经支持同回合并行：收到一个包含多次工具调用的 assistant 回复后，harness 先按顺序跑完调用前钩子检查，再并发执行获准的工具；完成事件按实际完成先后通知 UI，而写回会话历史的 `toolResult` 消息严格维持 assistant 原回复里的调用顺序。需要独占共享状态的工具也可以声明顺序执行。[S6](#s6)

也就是说，模型在同一轮里连续发出三次 `read`，不开 Code Mode 一样能并行。Code Mode 额外补上的能力，是在脚本里直接写依赖关系、条件分叉、异常处理和结构化过滤，把不需要模型阅读的原始载荷留在执行侧。

### 三份历史各干各的活

| 视图 | 记录或呈现的内容 | 主要用途 |
|---|---|---|
| 原始会话树 | 消息、分支链接、压缩摘要、上下文编辑、配置变更、自定义状态 | 审计追溯、恢复、分叉、导出 |
| UI 与执行事件 | 流式增量、折叠状态、嵌套工具进度、耗时与提示 | 实时交互观察 |
| 模型上下文投影 | 活动分支经压缩边界、上下文编辑、消息转换后的最终请求载荷 | 驱动下一轮模型推理 |

当前 JSONL 文件里的每条记录都靠 `id` 和 `parentId` 串成一棵树。`/tree` 切回老节点继续对话时，被放弃的分支留在原文件里不会丢。压缩只是往分支上追加一条摘要和保留边界，不会改写旧行；`context_edit` 可以在投影阶段隐藏或替换某条历史消息，磁盘原始记录照旧保留。[S11](#s11)

顺带提一个 v1.5.2 补上的日常恢复痛点：项目文件夹改名或删掉以后再打开旧会话，交互模式以前只能硬着头皮在当前目录临时续跑，每次重启都要反复弹窗询问。v1.5.2 起，遇到原目录不存在时，`/resume`、`/import`、启动参数和扩展的 `ctx.switchSession()` 会同时给出 `Continue`、`Fork` 和 `Cancel` 三个选项。选 `Fork` 会把会话带新 ID 复制一份到当前目录，以后再恢复就不会反复追问。[S39](#s39)

### 自动压缩、摘要预算与截断恢复

自动压缩的触发条件很直白：

```text
contextTokens > contextWindow - reserveTokens
```

Code Mode 负责在原始数据进入上下文之前先瘦身，压缩负责在对话拉长之后把早期历史压成摘要。两者上下游配合，互不替代。拿测试日志举例，工具自带的截断只认头尾行数，不知道你想看哪三条失败断言；脚本可以直接解析测试输出、只留失败堆栈。反过来，如果脚本一股脑把几万行原始日志全 `text()` 出去，上下文同样会迅速撑满。[S12](#s12)

v1.4.0 修正过一个很隐蔽的预算耦合：`compaction.reserveTokens` 只负责决定何时触发压缩，不再用来折算摘要请求的输出上限。早期主摘要曾用 `floor(0.8 * reserveTokens)` 和模型上限取较小值，结果推理型模型光思考就用光了大半额度，正文摘要写到一半直接截断。如今摘要与分支总结都改用模型本身的输出上限，并在接近窗口上限时协同调整输入与输出空间。导出的 `generateSummary()` 和 `generateSummaryWithUsage()` 为兼容旧签名保留了 `reserveTokens` 形参位置，但内部已经忽略它；`/compact <instructions>` 附带的自定义指令也会透传给被切开的回合前缀摘要。[S12](#s12)、[S28](#s28)

另一条容易弄坏整场会话的边界在输出截断恢复上。如果模型输出撞到 token 上限留下一个残缺工具调用，投影层在丢弃残缺调用的同时，必须一并清掉与之对应的孤立 `toolResult`；否则后续发往 provider 的每一轮请求都会因为工具请求与结果不成对而报 400。v1.4.0 修通了这条协议一致性保护；v1.6.0 又补上了另一处场景：按 Esc 中断一批工具调用时，尚未开始执行的调用现在会收到明确的 `Operation aborted` 结果，不再悬空。[S2](#s2)

### v1.5.1 的两处底盘改动：流式读写与空闲缓存预热

当项目里出现几十兆的日志或压缩包，早期 `read` 和 `grep` 很容易把进程内存打爆。v1.5.1 把这两把高频工具的 I/O 方式做了重构：`read` 改用流式读取，哪怕文件很大，指定 `offset` 和 `limit` 时也只把展示范围内的行留在内存里，自定义 `ReadOperations` 可通过新的可选 `createReadStream` 接入同一能力；`grep` 的上下文行改由 ripgrep 直接吐出，不再自己把每个命中文件整份读进内存，原来的 `GrepOperations.readFile` 回调由此废弃不再调用。[S2](#s2)

同一版本把 `cacheWarming` 的默认策略从 `"streaming"` 调成了 `"idle"`。代码写在 `packages/ai/src/cache-warming.ts` 里，算盘打得很克制：单靠直觉定时发请求保活很容易倒贴钱，所以 Pi 在每次准备刷新缓存前，都会拿模型单价和当前提示规模算一笔账。[S37](#s37)

```text
expectedSavings = continuationProbability * missCost - warmCost
```

处于活跃流式阶段时延续概率按 `1` 算，最长保活 60 分钟（`CACHE_WARMING_MAX_AGE_MS`）；会话空闲后延续概率按上游经验常数 `0.15` 算，最长保活 30 分钟（`CACHE_WARMING_MAX_IDLE_AGE_MS`）。只有当预期节省不低于 `0.05` 美元（`CACHE_WARMING_MINIMUM_EXPECTED_SAVINGS`）时，才会在缓存 TTL 到达 90%（且至少提前 10 秒）时发一次 `maxTokens: 1`、`maxRetries: 0`、`disableContinuation: true` 的静默刷新，并且不干扰 Anthropic 的消息线程状态。

### 区分 agent_end 与 agent_settled

写 SDK 或 RPC 集成时，最常见的状态机 bug 是监听到 `agent_end` 就把界面标成空闲或直接退出进程。`agent_end` 只说明底层 Agent 跑完了一轮，后面还可能紧接着自动压缩、错误重试或者排队中的 follow-up 消息。真正代表 Pi 停下来不再自动推进的事件是 **`agent_settled`**。另外，v1.6.0 给 `AgentSessionRuntime` 补上了 `takeUnreportedDiagnostics()`，方便宿主在启动以及 `/new`、`/resume`、`/fork` 切换会话后按条去重取出配置或扩展加载告警，不再静默吞掉问题，也不再把同一条告警在 TUI 里刷屏。[S9](#s9)、[S40](#s40)

顺手确认一条底线：用 `/tree` 切回十分钟前的节点，磁盘上已经修改过的文件不会自动倒回。会话树记录的是对话与状态演进，无法替代 Git 或文件系统快照。

# 第 5 章：Code Mode 到底改变了什么

很多时候，让 Agent 卡壳的障碍并不在模型智商，而在工具返回了太多噪音。读取十几个配置文件、跑完一套测试再挑出三条报错，如果每次都把几万字符原封不动送回模型，既慢又容易稀释注意力。

Code Mode 的做法很直接：**让模型写一小段调用工具的 JavaScript，把循环、判断、容错和字段筛选放在脚本里跑完，只把主动调用 `text()` 或 `image()` 吐出来的精选结果交还给模型。**[S10](#s10)

![普通工具调用与 Code Mode 的信息流对比：Code Mode 将原始数据留在执行侧筛选](diagrams/information-flow.svg)

从模型视角看，它只发起了一次外层 `codemode` 工具调用；脚本在执行侧可以按需触发十几次真实工具调用。底层的 `read`、`bash`、MCP 工具照常执行，没有被任何替身架空。

### 用一个具体任务看差异：抽查三个 package.json

需求很简单：读取仓库根目录、`packages/api` 和 `packages/web` 三个 `package.json`，只报告各自的包名以及 `scripts` 里有没有 `test`。

用普通工具，模型可以连发三次 `read`，把三个完整 JSON 全部吞进上下文再摘字段；也可以拼一条 shell 命令用 `jq` 或 `node -e` 抽取。Code Mode 提供了另一条更结构化的路：

```js
const paths = [
  "package.json",
  "packages/api/package.json",
  "packages/web/package.json",
];

const rows = await Promise.all(paths.map(async (path) => {
  const raw = await tools.read({ path });
  const pkg = JSON.parse(raw);
  return { path, name: pkg.name, hasTest: Boolean(pkg.scripts?.test) };
}));
text(rows);
```

这段脚本假设三个文件都没有触发工具截断且为合法 JSON。注意 `tools.read` 读文本时直接返回字符串，别错写成 `raw.output`。要是目标文件大到被 `read` 截断，半截 JSON 会让 `JSON.parse` 报错；遇到超大文件时，要么换专门的结构化查询工具，要么用受控 shell 抽出所需片段再交给脚本。

### 把容错和控制流写进同一段代码

上面用 `Promise.all()` 有个隐患：只要其中一个路径不存在，整个表达式立刻 reject，连另外两个成功读出来的结果也会丢掉。并行读取多个相互独立的来源时，改用 `Promise.allSettled()` 稳得多：

```js
const paths = ["package.json", "missing/package.json"];
const results = await Promise.allSettled(
  paths.map(async (path) => JSON.parse(await tools.read({ path })))
);
text(results.map((r, i) => r.status === "fulfilled"
  ? { path: paths[i], name: r.value.name }
  : { path: paths[i], error: String(r.reason) }
));
```

拿到每项的 `fulfilled` 或 `rejected` 状态后，脚本自己决定如何汇总回报。放进同一个并发数组的调用彼此之间不能有先后依赖；如果第二步要读取第一步刚写出的文件，就老老实实按顺序 `await`。

### 算一笔直观的字符账

做个示意算术（纯数据量推导，非线上性能测试）：假设有 N 次独立调用，单次原始结果 R 字符，经脚本挑完后每项只留 S 字符。

```text
原样跨回模型：约 N * R 字符
脚本筛选输出：约 N * S 字符 + 脚本与外层结果包装
```

代入 N = 12、R = 20,000、S = 300，数据载荷从约 240,000 字符降到约 3,600 字符。字符数不等同于 token 数，也不直接等于账单费用，实际开销还取决于分词器、工具定义长度和缓存命中率。但这笔账清楚标出了收益来源：省下来的恰恰是那些本就不需要模型逐字阅读的中间噪音。

### 什么时候反而别套 Code Mode

只读一个五十行的小文件，直接 `read` 干脆利落；跑一趟全量单元测试，直接 `bash` 更直观；做耗时很长的密集计算，把任务塞进 QuickJS 解释器反而拖慢节奏。Code Mode 是一层按需启用的可编程数据过滤层，别为了用它把一行能说完的调用强行包成脚本。

# 第 6 章：拆开沙箱，顺着源码追一次调用

脚本跑在沙箱里听起来很让人安心，可一旦线上出了权限越界或内存打爆，光靠这几个字根本没法排障。必须看清楚脚本到底运行在哪一层，工具校验和扩展钩子又卡在哪个节点。

普通 CLI 的 Code Mode 拆成两层：`packages/codemode` 是零 Pi 内部依赖的独立 QuickJS/WASM 沙箱库；`packages/coding-agent/src/extensions/codemode` 负责把它接到 Pi 的工具管线、会话存储、TUI 卡片和模型运行时上。[S13](#s13)

![主线程、worker、WebAssembly 与 QuickJS 的调用边界：嵌套工具调用经消息桥返回主线程走完整管线](diagrams/sandbox-boundary.svg)

### 从源码一路追通 tools.read()

在 `packages/coding-agent/src/extensions/codemode` 和 `packages/codemode` 里顺着代码走一遍，一次 `tools.read(...)` 经过九个站点：

1. `tool.ts` 定义 `codemode` 工具的参数结构、模型可见提示和 `reachesTools` 静态分析。
2. `execute.ts` 读取当前会话里允许被脚本调用的工具列表 `ctx.tools`，逐个包成沙箱能力适配器。
3. 通过 worker 准入池拿到名额后，创建 `CodemodeSandbox`，配置 QuickJS WASM、worker 入口、256 MiB 堆上限和剩余 deadline。
4. `sandbox.execute()` 拉起 worker thread，在独立 WASM 实例里初始化新的 QuickJS VM。
5. VM 启动时加载 prelude，把 `tools`、`text`、`image`、`console`、`store` / `load` 等受限接口挂到全局，底层通信桥藏进闭包，外部脚本摸不到。
6. 脚本执行 `await tools.read(...)`，请求序列化为 JSON，经 worker 消息桥发回 Pi 主线程。
7. 主线程调用 **`ctx.executeTool()`**，完整经过 `pi-schema` 参数转换校验和所有扩展注册的 `tool_call` / `tool_result` 钩子。
8. 工具执行完，结果按契约转换成脚本值传回 VM；脚本筛选并输出。
9. `execute.ts` 触发沙箱关闭、在脚本成功时提交 `codemode-store` 状态、按预算截断过长输出、落盘临时图片，返回外层工具结果。

打开 `packages/coding-agent/src/extensions/codemode/execute.ts`，决定安全策略能否统一落地的就是这一行核心调用：

```ts
const outcome = await ctx.executeTool(tool.name, args, { signal: callSignal });
```

正因为脚本里的工具调用必须绕回 `ctx.executeTool()`，扩展在工具边界上写的命令白名单、目录保护或审计日志才能照常拦住嵌套调用。不过有一点必须提前搞清楚：Pi 默认**没有内置逐次弹窗确认的权限审批系统**，这里保证的是自定义扩展钩子不会被脚本绕过，而非开箱自带强制审批。[S14](#s14)、[S15](#s15)、[S25](#s25)

### v1.5.0 换装 pi-schema：统一 JSON Schema 校验

在 v1.5.0 之前，TypeBox 工具和普通 JSON Schema 工具（尤其是 MCP 服务器导出的工具）在参数宽容度和报错文案上存在温差。v1.5.0 新增了独立包 **`@earendil-works/pi-schema`**（`packages/schema`），把 JSON Schema Draft 2020-12 与 Draft 7 编译成纯 JavaScript 闭包，全程不使用 `eval` 或 `new Function`，因此能在严格 CSP 与 Cloudflare Workers 环境下运行。[S35](#s35)

`packages/ai/src/utils/validation.ts` 现在统一走 `pi-schema` 做参数转换与校验。不管是内置工具、TypeBox 扩展工具还是 MCP 原始 JSON Schema 工具，校验前都会经过同一套宽松转换：把数字字符串 `"42"` 转成 `42`，把 `"true"` 转成 `true`，把单个标量包成单元素数组，并丢弃可选属性上的 `null`。校验失败回传给模型的报错也统一成精简的点号路径格式，比如 `- path: missing` 或 `- limit: expected number, got string`。[S15](#s15)

这套切换带来一个明确的迁移注意点：超出 JSON Schema 表达范围的 TypeBox 私有特性，例如 `Type.Refine()`、`Type.Codec()` 以及通过 `Format.Set()` 注册的自定义格式，不再自动作用于工具参数校验，需要移进工具的 `execute()` 里自行检查。v1.5.1 随后补齐了 `Type.BigInt()` 与 `Type.Undefined()` 的转换兼容，而 `Type.Void()` 依旧拒绝任何 JSON 参数。[S2](#s2)

### 为什么舍弃 Node 的 eval 而绕道 QuickJS/WASM

脚本在 QuickJS/WASM 里完全看不到 `process`、`require`、`fs`、`fetch` 和 `setTimeout`。就算模型在脚本里写 `eval` 或 `new Function`，也只能在同一个受限 VM 里生成代码，凭空变不出宿主进程的任何句柄。再加上独立的 worker thread，一段同步死循环最多卡死当前 worker，不会把 Pi 主线程的终端响应和取消快捷键拖死。

把每一层的限额对号入座，排查报错时就不会找错地方：

| 层次 | 负责的限制或约束 |
|---|---|
| 脚本首行指令 | 可选 `// @options:` 必须写在第一行，放错位置或含非法字段会直接报错 |
| QuickJS VM | CLI 默认单脚本 256 MiB 堆内存上限，超限即分配失败 |
| 宿主与 worker | 总 deadline、取消信号、worker 存活数量上限与 FIFO 排队等待 |
| 输出收集器 | 16 Mi 字符总量及 100,000 条输出项硬上限 |
| 模型结果格式化 | 外层默认约 10,000 输出 token 预算，超长保留头尾并把全文存到临时文件 |
| 底层真实工具 | 自身参数校验、外部进程生命周期、输出截断与命令超时 |
| 操作系统或容器 | 文件系统、进程、网络与凭据的真正隔离边界 |

这些数字各自守着不同环节，切忌混用。256 MiB 是 CLI 集成定的值，Cloudflare 远程沙箱的默认 VM 内存则是 32 MiB；外层 10,000 token 输出预算管的是发回模型的体积，跟 VM 里能建多大的对象无关。[S10](#s10)、[S13](#s13)

### 别把 JS 沙箱当成系统权限沙箱

```js
await tools.bash({ command: "rm -rf ./important-dir" });
```

只要 `bash` 被注入进了 `tools`，上面这行代码完全合法，真正执行删除的是宿主进程里的 shell。QuickJS 碰不到本地文件系统，不代表经它转发出去的 `bash` 调用也碰不到。需要强隔离的环境，必须把 Pi 或工具执行端放进挂载受限、网络可控、凭据隔离的容器或虚拟机里。顺便留意，v1.5.0 已经删掉了仓库里早年的原生 OS `sandbox/` 扩展例子，官方现在推荐参考 `gondolin/` 示例或 `docs/containerization.md` 做容器化隔离。[S2](#s2)、[S25](#s25)

想顺着源码亲手走一遍的读者，推荐按这条顺序打开文件：`tool.ts` → `execute.ts` → `codemode/src/runtime/host.ts` → `worker.ts` → `vm.ts` → `prelude-source.ts`。先跟通一次普通调用，再去看声明渲染和模型辅助接口会轻松很多。
