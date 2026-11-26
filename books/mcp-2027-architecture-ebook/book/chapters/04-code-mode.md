# Code Mode：终结上下文膨胀与 N 轮串行调用

让一个 Agent 做件日常排障活：先去 Linear 拉出 `core` 团队所有未关闭的 issue，挑出最高优先级 P0 的那几条，再分别去 Sentry 查它们过去 24 小时的报错次数和 P95 延迟，最后输出一张汇总表。按 2025 年最常见的 plain tool calling 跑法，事情会立刻变得既慢又贵。宿主先把两台 MCP Server 的几十个工具 JSON Schema 全量塞进系统提示；第一轮模型调用 `linear.listIssues`，返回 20 条带长描述的 issue 原始 JSON，整包灌进对话历史；第二轮模型挑出 3 条 P0，再逐一调用 `sentry.getIssueStats`，每一条多走一轮推理，又把一大段堆栈和标签写回上下文。把两台服务器的数据拼在一起，实际是拿昂贵的模型上下文窗口当内存，拿串行模型推理当 `for` 循环。

Philipp Schmid 在《Why I'm Bullish on MCP for 2027》里把 **Code Mode** 放到了第一条核心变化的位置，并指出这种模式正在成为 OpenCode 2.0、Claude Code、OpenAI Codex、Pi 和 Factory Droid 等新一代客户端的默认执行路径。下面这张图对比了传统串行 tool calling 和 Code Mode 在上下文占用与往返轮次上的差别。

![传统 Tool Calling 与 Code Mode 上下文和往返开销对比](../../wiki/assets/diagrams/code-mode-vs-toolcall.svg)

图里上半部分的普通调用链路中，全量中间数据都必须经过模型上下文窗口，哪怕最终汇报只需要三个字段，前几十 KB 的冗余文本也会跟着后面每一轮推理反复计费。下半部分的 Code Mode 则由宿主提供一个隔离执行沙箱，把远端 MCP 工具映射成带类型的可调用函数，让模型在一轮推理里写出异步脚本，把并发查询、过滤和跨服务器 Join 全部放在便宜的沙箱内存里完成，最后只把精简摘要返回给模型上下文。

## 为什么 `outputSchema` 是 Code Mode 的前提

早在 2025 年，就有团队尝试让模型直接写 Python 或 TypeScript 去调外部工具，但经常写出一堆字段名猜错的崩溃代码。根源在于旧工具只声明了 `inputSchema`，没声明返回结构。模型知道该传什么参数，却不知道返回值里列表字段叫 `items`、`issues` 还是 `data.nodes`，也不确定优先级字段是 `"P0"` 字符串还是数字 `1`。每次碰到未知结构，模型不得不先发一轮试探调用看一眼输出，再写处理脚本，平白多出往返和试错成本。

MCP 的 `outputSchema` 与 `structuredContent` 把这块拼图补齐了。只要服务端在 `tools/list` 里同时声明 `inputSchema` 和 `outputSchema`，客户端就能在模型动笔前，自动生成强类型桩代码（Typed Stubs），直接暴露给沙箱环境。Philipp Schmid 原文给出了这个最直观的 TypeScript 示例：

```typescript
// Auto-generated from MCP inputSchema + outputSchema
// interface LinearIssue { identifier: string; title: string; priority: number }
// interface SentryStats { count24h: number; p95Ms: number }
//
// declare const linear: {
//   listIssues(args: { team: string; state: "open" | "closed" }): Promise<{ items: LinearIssue[] }>;
// };
// declare const sentry: {
//   getIssueStats(args: { query: string }): Promise<SentryStats>;
// };

// Model writes 1 script instead of N tool-calling turns
const issues = await linear.listIssues({ team: "core", state: "open" });
const p0s = issues.items.filter(i => i.priority === 1);

const errors = await Promise.all(
  p0s.map(i => sentry.getIssueStats({ query: i.identifier }))
);

// Only the joined summary enters model context
return p0s.map((i, idx) => ({
  id: i.identifier,
  title: i.title,
  events24h: errors[idx].count24h,
}));
```

这段不到 15 行的脚本体现了 Code Mode 的四个工程收益。**推理轮次从 $N+1$ 压到 1**，原本要串行跑四五轮的查询，现在由 `Promise.all` 在单次沙箱执行里并发完成，端到端延迟显著下降。**过滤与聚合落在廉价计算层**，在第 14 章的教学基准测试（`test_09_code_mode_slashes_turns_and_context_bytes`）里，面对 20 条带 600 字节正文的 issue 和对应带堆栈的 Sentry 统计，普通 tool calling 需要 4 轮推理并向模型上下文灌入上万字节原始载荷，Code Mode 只用 1 轮推理，进入上下文的最终摘要体积缩小了超过 20 倍。**静态类型提前挡住字段幻觉**，只要沙箱开启 TypeScript 类型检查或运行前校验，把 `i.priority === "p0"` 写错成字符串类型在执行前就会报错，根本不需要等到远端接口失败。**提示词里的工具列表大幅瘦身**，宿主无需把 50 个冗长的 JSON Schema 原文塞进系统提示，只需提供精简的类型签名索引，甚至按需动态检索子集。

## 三种工具调用路线的真实取舍

工程落地时，普通 tool calling、本地 CLI 管道和 MCP Code Mode 各有适用边界，把三者放在同一张表里对照，选型会非常清晰：

| 维度 | 普通 Tool Calling | 本地 CLI + 管道（`jq`/`curl`） | MCP Code Mode |
| --- | --- | --- | --- |
| 多步跨系统 Join | 每步一轮模型推理，中间数据全进上下文 | 沙箱内一次完成，但需要人工或模型手写解析正则 | 沙箱内并发执行，按 `outputSchema` 强类型 Join |
| 多模态返回 | 支持图像、音频、资源块 | 标准输出为纯文本字节流，需落盘临时文件拼路径 | 同时返回 `structuredContent` 给代码和独立图像/音频块给模型 |
| 凭证隔离与授权 | 宿主托管 OAuth 2.1，模型不见密钥 | 依赖磁盘配置文件或环境变量，沙箱内代码可直接读取 API Key | OAuth 2.1 与作用域控制留在沙箱外，沙箱仅通过受控桥接调工具 |
| 非研发用户可用性 | 高，零本地安装 | 低，需要终端、包管理器与环境配置 | 高，宿主透明提供轻量沙箱（如 JS/Wasm 隔离区） |
| 适用场景 | 单次简单工具调用、快速确认动作 | 本地代码库操作、Git、编译测试与系统运维 | 跨多台远程 SaaS 的批量过滤、统计聚合与复杂编排 |

这里最容易被低估的是**沙箱外 OAuth 边界**与**原生多模态**。很多开发者喜欢在本地终端里用 `gh`、`stripe`、`aws` 等 CLI 工具配 `SKILL.md`，在个人开发机上确实顺手。可一旦把这套方案搬到企业团队或云端托管 Agent，CLI 往往逼着你把宽权限 API Key 塞进容器环境变量。只要沙箱里执行的脚本读了一段带提示注入的外部文档，攻击者一句 `env` 或 `cat ~/.config/...` 就能把凭证偷走。

而在 MCP Code Mode 架构里，代码沙箱本身是无网、无磁盘凭证的纯计算隔离区（例如 V8 Isolate、QuickJS/Wasm 或无密钥子进程）。沙箱里的 `linear.listIssues(...)` 和 `sentry.getIssueStats(...)` 只是向沙箱外宿主发起的受控 RPC 桥接；真正的 OAuth 2.1 Access Token、作用域校验、人工审批弹窗和出站 HTTPS 连接全都由沙箱外的 MCP Client 负责。即使沙箱里的脚本被恶意输入诱导，它也看不到任何原始密钥，更无法绕过宿主允许的工具清单去访问任意外网地址。

多模态场景同样如此。如果一个 BI 或监控 MCP Server 返回了图表 PNG 图像和底层指标数组，Code Mode 脚本可以直接拿 `structuredContent` 里的数组算同比增长率，同时把 `content` 里带 `type: "image"` 的独立块原样透传给多模态模型看图，不用在脚本里折腾 Base64 落盘和文件路径传递。

## 落地 Code Mode 时绕不开的三个工程细节

把 Code Mode 真正做稳，服务端和宿主各有几件不能偷懒的事。

对 **MCP Server 作者**而言，第一件事是把 `outputSchema` 当成一等公民来写，哪怕返回的是数组或嵌套对象，也要给足准确的字段类型和 `required` 列表，并保证 `structuredContent` 严格遵守该 Schema。只返回一坨 Markdown 表格的工具，在 Code Mode 里会退化成难解析的字符串，直接拖累调用成功率。第二件事是注意分页和体积上限：即使沙箱内存比上下文窗口便宜得多，无限制拉取十万条记录照样会触发沙箱 OOM 或执行超时，工具设计时应当提供明确的过滤参数（如 `team`、`state`、`limit`）与分页游标。

对 **宿主与客户端作者**而言，第一件事是隔离沙箱必须锁死资源配额与出站网络：限制 CPU 时间、内存上限、单次脚本并发 RPC 数量，禁用原生 `fetch`/文件系统/环境变量访问，所有对外交互只留 MCP 桥接函数这一个窄口。第二件事是把人工确认（Human-in-the-Loop）和写操作拦截依然卡在宿主层：沙箱脚本调用只读工具可以静默并发放行，但一旦触发破坏性写操作或服务端返回 `InputRequiredResult`，宿主必须能暂停脚本执行、把确认弹窗推给用户，等拿到结果再恢复 Promise，或者直接拒绝并抛异常给脚本处理。第三件事是错误堆栈的回传策略：脚本里某次 `tools/call` 返回 `isError: true` 时，宿主应当把这条可执行错误信息映射成可读异常，既允许脚本内 `try/catch` 自行兜底，也在未捕获时完整还给模型，让模型在下一轮迅速修正脚本。

当沙箱脚本在执行半途遇到服务端需要人工确认或外部授权，或者普通远程调用需要向用户追问额外参数时，无状态的集群怎么在没有粘性会话的前提下把这一来一回接上？下一章我们拆解 `2026-07-28` 的多往返请求机制（MRTR）和 URL Mode Elicitation。
