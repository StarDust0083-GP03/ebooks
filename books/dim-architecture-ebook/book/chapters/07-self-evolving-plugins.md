# 在会话里给自己写插件与热重载自演化

普通编码 Agent 碰到缺工具的场景，常见做法是让模型临时写一段一次性 Python 或 Bash 脚本跑完就扔。下次再遇到同类任务，它还得重新拼命令行、重新处理边界条件，也没法把这块能力沉淀成带结构化参数校验、独立用户配置项和明确风险等级的一等工具。

Dim 给出的路径完全不同：因为运行时本身就是插件化的，而且内置了 Node 运行时（`runtime/runtimes.json` 固定 Node `24.21.0`、Python `3.12.15` 和 `uv 0.13.7`，按需下载），用户可以在一个有写权限的会话里，直接让 Dim 给自己写一个 TypeScript 插件，当场用 `plugin` 工具装进正在运行的守护进程，下一轮模型请求就能直接调用这个新工具，发现 Bug 改完代码原地热重载，全程不需要重启守护进程，也不会丢掉当前会话的上下文。

## `plugin-authoring` 技能：把装配规矩装进目录

仓库内置的三个默认或可选插件包位于 `runtime/plugins/`，`runtime/default-plugins.json` 默认启用了其中的 `plugin-authoring`：

```json
{
  "plugins": {
    "plugin-authoring": "plugins/plugin-authoring"
  },
  "optional": {
    "light-ocr": "plugins/light-ocr"
  },
  "locked": ["PluginManager", "Rpc", "Transport"]
}
```

这里有两处值得细看的设计。一是 `locked` 列表把 `PluginManager`、`Rpc`、`Transport` 锁成不可被插件管理操作卸载或禁用的核心行，防止模型或误操作把自己和外部的通信链路拆掉。二是 `plugin-authoring` 本身并不是一段代码，它的 `dim-plugin.json` 只声明了 `"skills": "skills"`，向 `SkillsCatalog` 贡献了一份教模型怎么写 Dim 插件的技能文档 `runtime/plugins/plugin-authoring/skills/plugin-authoring/SKILL.md`。

按这份技能文档，一个最小可运行的 TypeScript 插件目录只需要四个东西，而且**不需要任何编译打包步骤**（Node 24 运行时直接加载 `index.ts`）：

```text
echo-plugin/
  dim-plugin.json    # 清单：name、description、code.entry、code.inject
  index.ts           # 入口模块：直接加载 TypeScript，无需 tsc 构建
  package.json       # 锁定 @arcships/rutis 版本
  node_modules/      # 执行 npm install 生成的依赖
```

代码结构完美复用上一章讲过的“单进程行 + `partRegistry` 登记多作用域部件”模式：

1. `dim-plugin.json` 里声明 `"code": { "entry": "index.ts", "inject": ["partRegistry", "settings"] }`。这里有一条由 `rutis 0.23.0`（ADR 025 D4）强制校验的规则：入口默认导出实际读取的每一个必填服务，都必须列在 `code.inject` 中，且外部插件自身的 `inject` 必须等于或是该列表的子集。
2. 入口 `default` 导出在进程作用域执行一次 `apply`：先向 `settings` 数据面注册自己的业务配置源（带 JSON Schema、默认值和 `userOnly` 列表），再向 `partRegistry.registerDefinition('session', 'EchoTools', tools)` 注册 Session 级部件定义。
3. Session 部件 `tools` 注入 `['toolRegistry', 'settings']`，在每个活跃会话（包括**当前正在写这个插件的会话本身**）挂载时，向该会话的 `toolRegistry` 注册新工具，声明名称（1 到 64 位字母数字及 `_`/`-`）、JSON Schema 和必填的 `risk` 风险等级（`read_filesystem`、`write_filesystem`、`execute_process`、`network` 或 `session`）。
4. 工具处理器收到 `call.arguments` 后自行校验参数，实时从 `settings.read` 读取最新业务配置，返回 `{ content: string, isError?: boolean }`。其中 `content` 是模型唯一能读到的文本。

## 从写代码到热重载的完整链路

当模型在会话里写好这几个文件并跑完 `npm install` 后，接下来的自演化动作全靠宿主提供的 `plugin` 工具完成：

| 步骤 | `plugin` 工具子命令 | 守护进程内部发生的事 |
| --- | --- | --- |
| 1. 安装 | `install <dir>` | `PluginManager` 校验清单与依赖，经 `rutis/loader` 创建新进程行并等待就绪；返回 `running`、`failed`（附带错误详情）或 `pending`（附带 `waitingFor` 缺失依赖名） |
| 2. 活体挂载 | 无需重启 | 进程行 `apply` 调用 `partRegistry.registerDefinition` 后，当前打开会话里的 optional mounter 监听到登记表变化，立刻把 `EchoTools` 挂进本会话并注册工具到 `toolRegistry` |
| 3. 下一轮即用 | 模型下一次请求 | `ToolBinding` 在下一次模型请求装配时读到新工具声明，模型立刻可以调用 `echo` 测试 |
| 4. 改错与重载 | `reload <name>` | 卸载旧一代插件行，自动撤回 `partRegistry` 定义、`settings` 源与所有会话里的 `toolRegistry` 工具注册，重新导入 `index.ts` 并装配新一代 |
| 5. 动态调参 | `config <name> set <k> <v>` | 写入业务配置源，**不触发插件重载**；下次工具调用直接读到新值 |
| 6. 下线 | `disable <name>` / `remove <id>` | 停用或卸载行，等待各会话副本清理完毕 |

这里藏着三个非常接地气的工程细节，都是只有真跑热重载才会碰上的坑：

第一，**模块缓存边界**。Node 的 ESM 缓存机制决定了 `reload` 会重新导入入口文件 `index.ts`，但不会自动失效 `index.ts` 再 `import` 进去的二级子模块。所以 `SKILL.md` 直接提醒作者：需要频繁边改边重载的逻辑尽量留在 `index.ts` 里。

第二，**敏感配置的权限隔离**。如果插件需要用到外部 API Key，可以在注册 `settings` 的 JSON Schema 里给该字段加上 `"writeOnly": true` 并放进 `userOnly` 列表。这样一来，模型通过 `plugin config` 既读不到密钥明文，也改不了这个字段，只能由用户本人修改。

第三，**活会话故障隔离**。如果一个已经发布的会话正在运行，而此时某个插件注册了一个有 Bug 的 Session 部件并挂载失败，`rutis-usage.md` §2 规定该失败只会记日志并停留在可选部件局部，绝不会把整个已经发布的活会话拖垮。

## 一个真实的源码观察：Alpha 期的文档版本漂移

核对当前快照 `88a3df3669405730cbee6bfd113a7f75cf520677` 的源码时，可以发现一处很真实的 Alpha 期细节：在 ADR 025 把整个工作区、`runtime/package.json` 和 `runtime/plugins/workflow/package.json` 全部升级到 `@arcships/rutis` `0.23.0` 之后，`runtime/plugins/plugin-authoring/skills/plugin-authoring/SKILL.md` 第 36 与第 40 行的示例里仍然写着 `"@arcships/rutis": "0.17.1"`。因为协议 5 与旧版不兼容，如果用户严格照抄那份技能文档里的 `0.17.1` 版本号去 `npm install`，在当前 `0.1.0-alpha.27` 上握手会直接被拒绝。这类真实的版本同步滞后在快速演进的 Alpha 仓库里很常见，也恰恰说明为什么理解底层 `rutis` 协议版本与 `runtime/package.json` 基线比死背示例片段更重要。

最后别忘了安全底线：能在会话里给自己写插件并加载，前提是用户给了写文件和调用 `plugin` 工具的权限；一旦插件被装入本地 Node 运行时，它就是受信任的本地代码（AGENTS 规则 11），不是靠 `inject` 声明就能困住的沙箱。关于权限、配置启用与执行隔离的边界，我们在第 9 章会专门拆解。在这之前，先看看随着会话拉长、工具和插件越来越多，Dim 是怎么控住上下文窗口和内存占用的。
