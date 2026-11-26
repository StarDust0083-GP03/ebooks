# 核心原语与强类型契约

很多团队在 2025 年写完第一个 MCP Server 后，很快遇到两个让人哭笑不得的故障。第一个故障来自返回格式：工具把查询结果拼成一段自然语言文本塞回 `content`，结果上游字段多了一个逗号或者模型需要二次提取 ID 时，只能靠肉眼读字符串硬猜，一旦要做批量过滤或跨系统关联，失误率直线上升。第二个故障来自参数校验：用户说把机票订在 12 月 12 日，模型不小心填成了去年的年份 `"12/12/2024"`，服务端一看日期不对，直接扔出一个 JSON-RPC 协议错误 `-32602 (Invalid params)`；客户端把协议错误当成系统异常拦在外面，根本没告诉模型为什么错，模型只好盲目重试三遍一模一样的过去日期，最后任务彻底卡死。

这两个坑分别在 `2025-06-18`、`2025-11-25` 和 `2026-07-28` 被逐一填平。正是这组看似不起眼的契约升级，让 MCP 工具从给人看的字符串拼接接口，变成了给程序和模型共同消费的强类型合约。

## 三大原语的控制权划分

MCP 服务端暴露的核心原语一直保持克制，只有三类：Tools、Resources 和 Prompts。官方规范用控制权归属把它们的边界切得很清楚，免得团队把所有东西都一股脑塞成 Tool：

| 原语 | 默认控制权 | 典型协议方法 | 适用场景 | 缓存与 MRTR 支持（`2026-07-28`） |
| --- | --- | --- | --- | --- |
| **Tools** | 模型控制（Model-controlled） | `tools/list`、`tools/call` | 执行有副作用的操作、查库、调外部 API、触发计算 | `tools/list` 支持 `ttlMs`/`cacheScope`；`tools/call` 支持 MRTR |
| **Resources** | 应用驱动（Application-driven） | `resources/list`、`resources/templates/list`、`resources/read` | 只读上下文数据（文件、库表结构、日志、Skill 文档、UI 模板），按 URI 寻址 | 列表与读取均支持缓存；`resources/read` 支持 MRTR 与 URI 模板（RFC 6570） |
| **Prompts** | 用户控制（User-controlled） | `prompts/list`、`prompts/get` | 显式触发的工作流模板（如客户端里的 `/` 斜杠命令） | `prompts/list` 支持缓存；`prompts/get` 支持 MRTR |

工具命名也有了明确规范（`SEP-986`）：名称长度应在 1 到 128 个字符之间，区分大小写，仅使用 ASCII 字母、数字、下划线、连字符和点号（例如 `getUser`、`DATA_EXPORT_v2`、`admin.tools.list`），避免空格和特殊符号。同时，规范在 `tools.mdx` 里特意提醒聚合代理和客户端：工具名只在单个服务器内唯一，不要拿自报的 `serverInfo.name` 做命名空间前缀来消歧，因为它不保证全局唯一，客户端应当使用自己分配的稳定服务器标识来隔离同名工具（例如两台服务器都叫 `search`）。

另外，每个工具还可以附带可选的 `annotations`（描述是否只读、是否具有破坏性等）和 `icons`（`SEP-973`），但规范用醒目的安全警告提醒客户端：除非来自明确受信任的服务器，否则工具注解必须视作不可信输入，绝不能仅凭服务端自称只读就跳过破坏性操作的用户确认。

## `inputSchema`、`outputSchema` 与 `structuredContent`

从 `2025-11-25`（`SEP-1613`）起，MCP 确立 JSON Schema 2020-12 为默认方言；到了 `2026-07-28`（`SEP-2106`），规范进一步放开了限制：`inputSchema` 和 `outputSchema` 允许使用完整的 JSON Schema 2020-12 关键字，且 `structuredContent` 不再局限于 JSON 对象，可以是任意合法 JSON 值（对象、数组、字符串、数字、布尔或 `null`），同时要求实现做好 `$ref` 解析以及组合关键字的资源上限防护，避免恶意深度嵌套 Schema 拖垮解析器。

看一个带完整输入输出 Schema 的工具定义示例（遵循 `docs/specification/2026-07-28/server/tools.mdx`）：

```json
{
  "name": "list_open_issues",
  "title": "List Open Issues",
  "description": "Return open engineering issues for a team",
  "inputSchema": {
    "type": "object",
    "properties": {
      "team": { "type": "string" },
      "state": { "type": "string", "enum": ["open", "closed"] }
    },
    "required": ["team", "state"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "items": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "identifier": { "type": "string" },
            "title": { "type": "string" },
            "priority": { "type": "integer" }
          },
          "required": ["identifier", "title", "priority"]
        }
      }
    },
    "required": ["items"]
  }
}
```

当工具声明了 `outputSchema` 时，服务端**必须**返回符合该 Schema 的 `structuredContent`，客户端**应当**在消费前按该 Schema 做校验。为了兼容还不认识 `structuredContent` 的旧客户端，规范建议服务端在 `content` 里同步放一份序列化文本或可读摘要。这里需要注意规范特别划出的一条概念区分：`structuredContent` 是**服务端**产生的结构化工具返回值，与大语言模型推理侧的 Structured Outputs（受限解码生成 JSON）完全是两回事。

不过，同时存在 `content` 和 `structuredContent` 也带来了一定心智负担和实现分歧。官方在 2026 年 8 月更新的路线图（`docs/development/roadmap.mdx` 第 4 项优先领域）里坦率承认了这一点：新成立的 Core Primitives 工作组计划在下个周期精简 `tools/call` 的返回形态，解决结构化与非结构化输出并存导致的保真度差异，并同步探索服务端渐进发现（Progressive Discovery）机制。

## 原生多模态内容块与资源引用

相比只能输出纯文本字节流的 CLI，MCP 工具结果从协议层就原生支持混合内容块。单次 `tools/call` 的响应可以同时把机器可计算的字段放进 `structuredContent`，把给人或视觉/语音模型看的内容放进 `content` 数组里的独立块：

- **文本块（`type: "text"`）**，承载可读摘要或向后兼容的 JSON 字符串。
- **图像块（`type: "image"`）与音频块（`type: "audio"`）**，携带 Base64 数据与 `mimeType`（如 `image/png`、`audio/wav`）。
- **资源链接（`type: "resource_link"`）**，只返回资源 URI、名称和描述，避免在工具结果里直接内联几十兆的大文件，让客户端按需调 `resources/read` 懒加载。规范提醒：工具返回的 `resource_link` 不一定出现在 `resources/list` 目录中。
- **内嵌资源（`type: "resource"`）**，当数据量不大且需要随结果立即生效时，直接携带 `uri`、`mimeType` 和内容体。

所有内容块都可以挂 `annotations`，用 `audience`（如 `["user"]` 或 `["assistant"]`）、`priority` 和 `lastModified` 提示宿主这段内容该给谁看。比如一张给人类复核的高清截图可以标成 `audience: ["user"]`，而不必白白浪费视觉模型的输入 Token。

## `SEP-1303`：把参数校验失败还给模型自纠错

回到本章开头那个订机票填错年份的死循环。`SEP-1303` 在 `2025-11-25` 落地并延续到 `2026-07-28`，把工具错误严格划成了两类，核心判据只有一条：**模型看到这条报错后能不能自己改参数修好**。

1. **协议错误（Protocol Errors）**，走标准 JSON-RPC `error` 对象，用于请求框架本身坏了或模型很难自修的问题，包括：调用了不存在的工具名（`-32602`）、请求外层不符合 `CallToolRequest` 结构、HTTP 头与 Body 不匹配（`-32020`）、服务端内部崩溃（`-32603`）。客户端可以选择直接拦截，不一定会喂给模型。
2. **工具执行错误（Tool Execution Errors）**，走正常的 JSON-RPC `result` 对象，带上 `"resultType": "complete"` 和 `"isError": true`，把清晰可执行的修复提示写在 `content` 里。覆盖三类场景：外部 API 调用失败、**所有输入参数校验错误**（格式不对、日期在过去、枚举越界、必填字段缺失），以及业务逻辑错误（如句柄已过期、余额不足）。

规范明确要求客户端**应当**把 `isError: true` 的工具执行错误完整交给模型。回到订机票的例子，服务端返回：

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "resultType": "complete",
    "content": [
      {
        "type": "text",
        "text": "Invalid departure date: must be in the future. Current date is 2026-11-26."
      }
    ],
    "isError": true
  }
}
```

模型看到当前日期提示后，下一轮马上就能把参数修正为合法日期并完成预订，不再需要人工打断。类似地，前面提到的显式状态句柄如果过期了，服务端也应当用 `isError: true` 明确回复 `"basket bsk_a1b2c3 has expired"`，引导模型重新调用创建工具，而不是扔一个冰冷的 `"invalid argument"`。

无状态传输、强类型 `inputSchema`/`outputSchema`、`structuredContent` 多模态返回和可自愈的错误边界都齐备之后，客户端执行工具的方式迎来了质变。下一章我们看 Phil Schmid 文章里的第一个重头戏：Code Mode 如何借着这套强类型契约，彻底改写 Agent 调用 MCP 的成本曲线。
