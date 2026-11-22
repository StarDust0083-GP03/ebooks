# 第 10 章：从 v0.99.2 到 v1.6.0，到底演进了什么

如果团队线上跑的早就是 `v0.99.2`，Code Mode、独立 `pi-codemode` 包、工具按需发现、MCP、树形会话和统一扩展这些骨架其实早就已经在车上了。真正需要逐条核对的，是从 `v0.99.2` 一路走到 `v1.6.0` 这段时间里落下的契约调整、性能重构和破坏性变更。[S2](#s2)、[S21](#s21)

| 版本 | 关键架构与运行时变化 | 对开发者与使用者的直接影响 |
|---|---|---|
| v1.0.0 | 缩短 Code Mode 提示词、新增 `models.generateImages()`、默认全屏 TUI、从 `pi-agent-core` 移除旧实验 `AgentHarness` | 提示开销下降；探测工具改用 `in`；依赖旧实验 harness 的 SDK 需迁移 |
| v1.0.1 至 v1.0.4 | 16 Mi 字符与 10 万条输出硬上限、`image()` 落盘临时文件、冻结内建原型、`read` 读图返回图片块 | 防止无限输出拖垮宿主；不能再篡改 `Array.prototype` 等内建对象 |
| v1.1.0 | CLI `--tools +name/-name` 增量语法、输出项分隔 | 增减单个工具更顺手，多段输出边界更清晰 |
| v1.2.0 | `reachesTools` 解耦 MCP 与 Code Mode；独立包新增 `RemoteCodemodeSandbox`、`InlineCodemodeSandbox`、确定性 `sandbox.start()`；Durable 接入可重放 Code Mode；清理未发布实验服务栈 | 编排器可替换；沙箱可部署到 Cloudflare；旧实验目录不可继续沿用 |
| v1.3.0 | 取消结果与真实 worker 退出解耦；恢复会话保留活动工具集；内部包精确版本锁定 | 重复超时不再无限堆积残留 worker；恢复后的工具集更稳定 |
| v1.4.0 | worker 满额 FIFO 排队（上限 60 秒，计入总 deadline）、`PI_CODEMODE_MAX_WORKERS`；摘要改用模型输出上限；`models.classify()` 支持图片输入与 `models-configuration.md`；修补截断工具消息配对与并发弹窗 | 并行脚本不再立刻因满额失败；`reserveTokens` 不再错当摘要长度上限 |
| v1.5.0 | 新增零 `eval` 闭包校验库 `@earendil-works/pi-schema`；`StreamingJsonParser` 增量解析大工具调用与 TUI 按帧合并及高亮复用；扩展包更新日志与 `collapseChangelog`；`PI_SELF_UPDATE_COMMAND` / `selfUpdateCommand`；GPT-6.1 Terra 与多语种 CJK 工具搜索；移除原生 OS `sandbox/` 示例 | JSON Schema 与 MCP 工具统一宽松转换；大参数流式不再卡顿；TypeBox 非 JSON Schema 特性不再用于参数校验 |
| v1.5.1 | `pi auth login/logout`；默认开启空闲 prompt 缓存预热；Anthropic 消息线程；流式 `read` 与 ripgrep `grep` 上下文（废弃 `GrepOperations.readFile`）；OpenAI 兼容端点省略空 `"tools": []`；MCP `$ref` 搜索与流大小限制；Copilot 分类器与更丰富的 Mermaid 图表 | 降低长会话和重复提示费用；大文件读取不再打爆内存；自定义 `GrepOperations` 需改写 `execute()` |
| v1.5.2 | 原工作目录消失时，恢复/导入会话提供 `Continue`、`Fork`、`Cancel` | 移动或重命名项目目录后，一次 Fork 即可绑定新路径不再反复弹窗 |
| v1.6.0 | TUI `Component.render()` 改为返回 `readonly string[]`，`Container` 复用未变子树的缓存数组引用；GPT-6.2 Astra/Sol 成为默认模型（不支持 thinking `off`）；Qwen Token Plan 四套提供商与新网关；`AgentSessionRuntime.takeUnreportedDiagnostics()`；修复 SDK `reload()` 漏发 `session_start` 与 `find` 相对路径模式 | 长会话全屏滚动不再打满单核 CPU；原地修改 `super.render()` 的扩展必须先拷贝；切换与重载告警不再刷花终端 |

### 从 v1.4.0 升到 v1.6.0，优先核对六处破坏性或高影响变更

如果已经跑在 v1.4.0，往 v1.6.0 升级时先把下面六件事逐项查完，能省掉绝大部分线上踩坑：

1. **检查所有自定义 TUI 组件与 `Container` 子类（v1.6.0）**：搜索代码里对 `render()` 或 `super.render(width)` 返回值调用 `.push()`、`.splice()`、`.unshift()` 或下标赋值的地方，一律改成先拷贝再改（`const lines = [...super.render(width)]`），否则会直接破坏 `Container` 的帧缓存。[S31](#s31)、[S32](#s32)
2. **检查工具参数里的 TypeBox 私有用法（v1.5.0）**：`pi-schema` 只执行标准 JSON Schema 约束与内置格式，不再运行 `Type.Refine()`、`Type.Codec()` 或 `Format.Set()`。依赖这些校验的工具要把额外检查搬进 `execute()`；如果上游提示词或测试硬匹配了 TypeBox 旧英文报错文案，也要同步更新成 `pi-schema` 的 `- field: reason` 格式。[S15](#s15)
3. **检查自定义远程 `grep` 扩展（v1.5.1）**：`GrepOperations.readFile` 不再被上下文行逻辑调用，面向远端文件系统的扩展需要直接覆写 `grep` 工具的 `execute()`。[S2](#s2)
4. **检查 OpenAI 兼容网关与思考等级假设（v1.5.1 / v1.6.0）**：禁用全部工具时 Pi 不再发送 `"tools": []`；若老版本代理在历史含工具调用时强制要求带上该字段，需在 `models.json` 里设置 `compat.requiresToolsWithToolHistory: true`。切换到默认 `gpt-6.2-sol` 后，不能再假定 thinking `off` 始终可用。[S2](#s2)、[S38](#s38)
5. **检查 `find` 工具的路径 glob 写法（v1.6.0）**：含 `/` 的模式现在相对搜索目录匹配（`src/*.ts` 只命中根目录 `src/a.ts`，不再误扫 `pkg/src/a.ts`），跨层级匹配需写 `**/src/*.ts`。[S2](#s2)
6. **检查扩展重启与诊断上报（v1.6.0）**：SDK 宿主可在启动和每次会话切换后调用 `runtime.takeUnreportedDiagnostics()` 读取去重后的加载告警，并确认依赖 `session_start` 的扩展在 `reload()` 后能正常重连。[S40](#s40)

### 顺手澄清三个老版本迁移断点与一句流行数字

如果你是从 v0.99.2 跨大版本直奔 v1.6.0，下面三个早期断点同样躲不过：探测工具是否存在别写 `typeof tools.foo`（访问未知成员可能直接抛带近似建议的错），统一改用 `"foo" in tools`；独立 MCP 包早在 v1.2.0 就删掉了 `MCP_TYPESCRIPT_PREAMBLE` 与 `mcpStructuredContentSchema()`，输出 schema 直接描述完整返回对象；独立沙箱 `close()` 和执行结果返回只代表发出了关闭请求，自己做资源池必须挂 `onWorkerStarted(exited)` 或 `acquireWorker()` 释放回调追踪真实退出。[S13](#s13)、[S21](#s21)

另外，社区里常转述的一句“Code Mode 少用约 40% token”，出处是 v1.0.0 变更日志里的一个特定样本：开启默认工具和 Code Mode 时，一次 GPT-5.6 请求的初始提示词从约 5,300 降到了约 3,300 token，省下来的是精简后的工具说明与按需加载。那是上游记录的单一场景数字，既不是本书实测，也不能当成所有模型和工具规模下的通用折扣。[S2](#s2)

# 第 11 章：独立沙箱、云端隔离与 Durable 重放

工程评审里常听到一句模糊的话：“Pi 的 Code Mode 已经支持崩溃恢复了。”这句话缺了主语，很容易把三种保障级别完全不同的运行时混成一锅粥。

| 运行形态 | 工具与状态归谁管 | 进程中途崩溃后会发生什么 |
|---|---|---|
| 普通 CLI Code Mode | `AgentSession` 与内置扩展 | 此前已成功提交的小型 `store` 仍在分支里；中途死掉的脚本没有指令级续跑 |
| 独立 `CodemodeSandbox.execute()` | 你的宿主程序 | 宿主自管持久化；单次执行不会自动变成持久任务 |
| 独立 `sandbox.start()` | 宿主驱动的确定性 VM | 提供可重放的确定性执行引擎，但 journal 与调度归宿主自己写 |
| 实验性 `pi-durable/codemode` | Durable Harness、存储、任务与 journal | 重启后按日志顺序重放已交付结果再继续推进；未完成调用按各自 `replay` 策略处理 |

### 11.1 不引入整个编码代理，单独使用沙箱

`packages/codemode` 没有任何 Pi 内部包依赖，非常适合嵌进你自己的服务或 Agent 产品里，只借用它的受限脚本执行和工具桥接能力：[S13](#s13)

```ts
import { CodemodeSandbox } from "@earendil-works/pi-codemode";

const sandbox = new CodemodeSandbox({
  timeoutMs: 10_000,
  tools: [{
    name: "lookup",
    execute: async ({ id }) => ({ id, title: `条目 ${id}`, privateNote: "不输出" }),
  }],
});

try {
  const result = await sandbox.execute(`
    const row = await tools.lookup({ id: 7 });
    text({ id: row.id, title: row.title });
  `);
  if (!result.ok) throw new Error(result.error.message);
  console.log(result.output);
} finally {
  await sandbox.close();
}
```

这是独立注入自有可信函数的写法。如果打算把它接进已有 Agent 框架，工具桥接必须回落到框架自己的完整工具调用管线（像官方 CLI 走 `ctx.executeTool()` 一样），切忌直接调用裸 `tool.execute()` 绕开校验与安全钩子。[S13](#s13)

### 11.2 为什么 Cloudflare 要把 VM 放进单独部署的 Worker

Node 和 Bun 下可以用 worker thread 把 QuickJS 计算挪出主事件循环。Cloudflare Workers 没有同样的线程模型，QuickJS 的同步计算会直接堵住承载它的 isolate。

v1.2.0 引入的 `RemoteCodemodeSandbox` 把位置拆开了：工具和调用账本留在会话侧，QuickJS VM 放到**单独部署的 Worker 里的 Durable Object** 中，每次工具调用走 Workers RPC 回调宿主。注意这里的 Durable Object 是 Cloudflare 的平台原语，跟 Pi 仓库里的 `pi-durable` 包没有任何从属关系。[S13](#s13)、[S21](#s21)

![三种沙箱部署模式：Node/Bun worker thread、Cloudflare 独立 Worker RPC 与单线程 inline 模式](diagrams/deployment.svg)

上游 `packages/codemode/README.md` 记录过一组 Cloudflare 实测对比：让脚本持续做十秒密集计算，如果 VM 跑在会话对象内部或其动态加载的 Worker 里，同一会话对象的其他请求会被卡住约十秒；拆到单独部署的 Worker 后，其他请求的响应延迟大约在 50 ms。这个数字出自上游测试环境，说明的是隔离部署的架构理由，不代表你在任意账号区域都能复现一模一样的延迟。

在 Cloudflare 上跑还要盯紧三条额外约束：脚本如果不主动 yield，墙钟 timeout 挡不住纯 CPU 死循环，必须配 interrupt budget；远端 abort 先解除调用侧等待，VM 要等到下一次工具调用或耗尽中断预算才会停；远端单个 VM 默认内存上限只有 32 MiB，远小于普通 CLI 的 256 MiB。至于 `InlineCodemodeSandbox`，它直接在调用线程里跑 VM，适合明确接受短暂阻塞的轻量宿主，配合零 Node 依赖的 portable 入口共用同一套脚本 API。[S13](#s13)

### 11.3 Durable 重放的是调用日志，不存 VM 内存快照

`packages/durable` 是一套仍在快速演进的实验性持久执行运行时。它的 Harness 把会话条目、文档状态、任务检查点和提交统一写进底层存储；结合确定性 `sandbox.start()` 后，能让长时间运行的 Code Mode 脚本跨进程重启恢复。[S23](#s23)

![Durable Code Mode 的确定性重放流程：记录顺序日志、重启后重跑脚本喂回旧结果再续跑](diagrams/durable-replay.svg)

它的恢复原理跟时间旅行调试很像，不依赖给 QuickJS 堆拍内存快照：

1. 启动脚本时锁定全部不确定因素：由宿主提供虚拟 `Date.now()`、固定随机种子、锁定 UTC 时区、隐藏 `WeakRef` 与 `FinalizationRegistry`，并校验代码和 VM 指纹。
2. 脚本发出的嵌套工具调用会注册成 Durable 子任务。
3. 工具完成后，宿主先按交付顺序把结果写进 journal，再把值交回 VM。
4. 进程崩溃重启后，Harness 从头重跑同一段脚本，沿着 journal 把已经记录过的结果按原序瞬间喂回去。
5. 脚本在确定性条件下推演出完全一致的调用序列，自动接上已完成或仍在跑的子任务，然后继续推进尚未执行的新步骤。

### 一个检验边界的案例：扣款成功瞬间宕机

假设脚本调用了外部支付工具，远端银行已经扣款成功，但本地进程还没来得及把结果写进 journal 就断电了。重启后框架能盲目再调一次吗？绝对不能。在 Durable 里，只有工具明确声明了 `replay: "safe"`，中断后的未决调用才允许自动重跑；默认情况下框架会报 interrupted 错误，把善后权交回业务层。就算标成了 `safe`，真正避免重复扣款靠的还是外部服务端的幂等 key 或查询对账。[S23](#s23)

确定性重放提供的是可追溯、可检查、可恢复的编排调度，变不出跨任意外部系统的恰好一次副作用魔法。只要脚本代码改了路径、VM 指纹变了或者总 deadline 过期，重放都会主动报错停下，避免在错位状态下继续发外部请求。

### 11.4 远程执行环境 pi-env 解决另一半问题

`packages/env` 决定命令和文件操作在哪台机器上执行：本地留着 Harness、存储和模型凭据，通过 SSH 在目标机器上拉起一个小型 Rust daemon，用协议化接口跑文件和进程操作，让 `RemoteExecutionEnv` 尽量贴近 `NodeExecutionEnv` 的语义。[S24](#s24)

把 QuickJS VM 放到远端（`RemoteCodemodeSandbox`）和把文件与命令放到远端（`pi-env`）是两个正交的架构选择：前者管脚本计算位置，后者管工具副作用落点。远程执行同样不等于自动沙箱，仍然必须校验 SSH host key，并收紧远端账户权限、网络出口和凭据暴露面。

![Durable、env、chord 与共享底层包的实际依赖关系：独立于普通 CLI 运行链](diagrams/durable-package-dependencies.svg)

把这张图和第 3 章的 CLI 依赖图放在一起对照最直观：两边共享 `ai`、`codemode`、`pi-schema` 和 `telemetry` 四个底层包，但在上层分道扬镳。普通 CLI 不会自动继承 Durable 的持久重放契约。

# 第 12 章：迁移路线、动手实验与核对工具

读完架构图之后，最怕关上页面只剩满脑子术语。这一章把迁移核对拆成可执行清单，再给四组零付费模型成本就能在本地跑通的动手实验。

### 12.1 从 v0.9.4 跨版本迁移，按所在层逐项拆解

如果只用终端 CLI，先备份现有会话和配置目录，把包名从 `@mariozechner/*` 切到 `@earendil-works/*`，并确认运行环境满足 Node.js 22.19 或更新版本（别继续沿用早期仓库的 Node 20 门槛），再逐步启用 Code Mode 和 MCP。

如果在业务代码里嵌入了旧版 `Agent`，重点核对旧 transport 抽象到当前 `streamFn`、消息转换器、事件流以及默认并行工具语义的变化。曾经引用过旧 `AgentHarness` 的项目要注意，`pi-agent-core` 自 v1.0.0 起已经移除该导出，需要明确决定迁向标准 `AgentSession`，还是进入实验性 `pi-durable`。[S6](#s6)、[S22](#s22)

如果维护着旧版自定义 hooks 和工具，按当前 `ExtensionAPI` 统一合并注册入口，逐条核对拦截、改写事件的返回值契约，并把宿主提供的核心包放进 `peerDependencies`。

### 12.2 升级至 v1.6.0 的工程检查表

- [ ] 自定义 TUI 组件与 `Container` 子类把 `render()` 输出视为只读，改动前先浅拷贝数组（`[...super.render(width)]`）。
- [ ] 工具参数校验不再依赖 `Type.Refine()`、`Type.Codec()`、`Format.Set()` 等非 JSON Schema 特性，必要检查移入 `execute()`。
- [ ] 远端文件检索扩展不再依赖已废弃的 `GrepOperations.readFile`，改为覆写 `grep` 的 `execute()`。
- [ ] 探测工具存在性使用 `"name" in tools`，所有发现 helper 均带 `await`。
- [ ] 脚本显式检查 `bash` 的 `exit_code` 与 MCP 的 `isError`，不把 Promise fulfilled 当成业务成功。
- [ ] `// @options:` 确实放在脚本第一行；图片结果走 `image()` 而非用 `text()` 打印 base64。
- [ ] 自定义沙箱资源池追踪真实 worker 退出回调释放名额，并清楚排队时间会扣减总 deadline。
- [ ] `compaction.reserveTokens` 只按触发阈值配置，不再拿来折算摘要输出长度。
- [ ] 使用 `gpt-6.2-sol` / `gpt-6.2-astra` 时不假设 thinking `off` 可用；如使用老代理且需保留空 `tools: []`，按需配置 `compat.requiresToolsWithToolHistory`。
- [ ] `find` 工具含 `/` 的路径模式已按搜索目录相对路径核对（跨层改用 `**/...`）。
- [ ] 恢复或重载会话后，核对活动工具、`--no-mcp` 隔离边界，以及 SDK 宿主的 `bindExtensions({})` 与 `takeUnreportedDiagnostics()`。
- [ ] 扩展包若需在更新后展示变更说明，检查 `package.json` 版本号与 `pi.changelogPath`；使用 Nix/mise 托管时按需配置 `PI_SELF_UPDATE_COMMAND` 或 `selfUpdateCommand`。

### 12.3 核对固定版本源码快照

本书依据固定标签源码编写，完整提交 SHA 与区间文件统计记在 `sources/UPSTREAM.md` 和 `sources/versions.json` 里。读者如需对照任意一行上游代码，可以直接按对应标签检出官方仓库，不影响本机已安装的全局 Pi：

```sh
git clone --depth 1 --branch v1.6.0 https://github.com/earendil-works/pi.git pi-v1.6.0
git -C pi-v1.6.0 rev-parse HEAD
# faac585884ae2c013a6b1400f3e9e77f10b34bac
```

### 实验 A：开启 Code Mode 观察信息瘦身

在已安装 v1.6.0 或兼容版本的终端里，用增量参数启动：

```sh
pi --tools +codemode
```

或者把下面配置合并进项目的 `.pi/settings.json`（保留原有其他字段）：

```json
{
  "defaultTools": ["+codemode"]
}
```

让模型同时检查仓库里五到十个配置文件并只返回名称和版本号。观察 TUI 里出现的一个外层 `codemode` 卡片和内部多次嵌套 `read` 调用，再对比要求它直接逐个输出完整文件内容时的上下文膨胀速度。

### 实验 B：零模型依赖跑通 Python 教学模型与 20 项单元测试

本书 `examples/pi_runtime_model.py` 用零第三方依赖的 Python 3.10+ 复刻了 Pi v1.6.0 的四块核心架构机制：树形会话投影与截断配对、Code Mode 工具桥接及 `pi-schema` 宽松转换与 worker 排队、v1.5.0 增量流式 JSON 解析与 v1.6.0 `Container` 只读渲染缓存、以及 MCP exposure 矩阵与多语种 `reachesTools` 延迟连接。配套的 20 项单元测试可以直接在终端验证：

```sh
python3 -m unittest discover -s examples -v
```

同时，原有的 Node 模拟检查脚本 `examples/run-checks.mjs` 依然保留，验证 `examples/scripts/` 里的 6 段 JavaScript 教学脚本（字段筛选、部分失败、非零 shell 退出码、成功与失败的 `store` 提交、磁盘副作用留存和图片分类正反契约）：

```sh
node examples/run-checks.mjs
```

这两套本地检查都用模拟工具验证控制流与不变量，不消耗模型费用，也不替代 QuickJS 安全审计或上游集成测试。

### 实验 C：亲眼验证分支 store 与磁盘文件的分离

在一个可丢弃的临时目录里开启会话，跑一段脚本先用 `tools.write` 写一个测试文件，再调用 `store("step", 1)`。接着用 `/tree` 回到该脚本执行前的节点开辟新分支，在新分支里调用 `load("step")` 并检查目录里的测试文件：此时 `load("step")` 返回 `undefined`，而那个测试文件依然稳稳躺在磁盘上。切勿拿生产数据做这个实验。

### 实验 D：对照官方 SDK 示例装配三个内置扩展

打开本书保存的 `examples/sdk-codemode-mcp.ts`（与 v1.6.0 上游 `packages/coding-agent/examples/sdk/14-codemode-mcp.ts` 逐字节一致），盯住三处关键装配点：`extensionFactories` 显式传入内置 Code Mode、`tool_search` 与 MCP 扩展，`defaultTools` 增量开启能力，以及创建会话后调用 `session.bindExtensions({})` 触发后台连接。

### 五道自测题

1. **Code Mode 带来的收益只是把工具调用从串行变并行吗？** 现在的普通工具调用默认就已经支持同回合并行；Code Mode 真正多出来的是执行侧的控制流、异常处理与中间数据筛选。
2. **脚本执行到一半抛异常，前面已经写出的磁盘文件会跟着回滚吗？** 不会。`codemode-store` 只在脚本成功结束时提交，它和外部文件系统之间没有分布式事务。
3. **MCP 工具调用返回的 Promise 正常 resolve 了，就能直接判定操作成功吗？** 不能。必须检查返回的 `CallToolResult.isError` 字段和具体内容。
4. **把一个 TUI 扩展升级到 v1.6.0 后，为什么往 `super.render(width)` 返回的数组里 `push` 一行状态提示会导致画面错乱？** 因为 `Container` 在子组件未变化时直接返回缓存数组引用；原地修改会污染缓存，导致下一帧重复叠加。必须先拷贝一份新数组再修改。
5. **QuickJS 里拿不到 `fs` 和 `fetch`，是不是就意味着脚本里跑任何工具都绝对安全？** 不是。脚本的破坏半径由宿主注入的工具及其背后进程或容器的真实权限决定。

### 术语速查表

| 术语 | 在本书语境下的准确含义 |
|---|---|
| Agent loop | 模型生成响应、执行工具调用、判断是否进入下一轮的核心循环 |
| Harness | 环绕在 Agent 循环外围的会话、上下文投影、资源、扩展与生命周期层 |
| AgentSession | 普通编码代理中统筹单场会话与运行状态的核心对象 |
| Turn / Run | Turn 指单次模型回复及其工具调用；Run 指一次用户输入触发的整串回合直至 `agent_settled` |
| Exposure | 控制工具如何声明给模型、能否被脚本或 `tool_search` 触达的策略 |
| Nested call | 由编排工具内部发起、原始结果先回到调用方脚本的工具调用 |
| Projection | 从磁盘原始树形历史按活动分支、压缩边界与上下文编辑算出的模型输入 |
| Copy credit | `StreamingJsonParser` 用来限制大容器开路径快照拷贝频次的信用额度，防止退化为平方复杂度 |
| Read-only render cache | v1.6.0 `Container` 对未变子树返回同一 `readonly string[]` 引用以省去逐层拷贝的机制 |
| Journal / replay | Durable 运行时按顺序记录已交付工具结果并在重启后确定性重放的机制 |
| Idempotency | 同一外部业务操作在重试时不会造成重复不可逆副作用的契约 |

### 顺着源码继续深挖的路线

想先吃透普通 CLI，推荐从 `docs/how-pi-works.md` 和 `docs/sdk.md` 入手，再依次读 `docs/codemode.md`、`extensions/codemode/tool.ts` 与 `execute.ts`；想研究 v1.5.0 和 v1.6.0 的热路径优化，直接翻 `packages/ai/src/utils/streaming-json.ts`、`packages/tui/src/tui.ts` 中的 `renderChildLines()` 与 `packages/schema/`；打算把受限脚本能力嵌进自有产品，优先读 `packages/codemode/README.md`；只有确定业务真的需要跨进程重启恢复长任务时，再进入 `packages/durable/README.md` 与 `packages/env/README.md`。
