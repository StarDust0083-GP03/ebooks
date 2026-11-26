# 产品原生智能体化：MCP Apps

用户在对话窗口里提出要求：把上个季度各区域的营收异常和 P95 延迟对比拉出来，顺便把核心服务的发布单配好。如果 MCP Server 只返回一段 Markdown 表格和四条外部链接，用户接下来就得切出对话、连续点开四个浏览器标签页，在四个不同后台之间来回复制 ID、筛选时间范围、再切回聊天框告诉 Agent 自己选了哪几项。一旦需要多维交互筛选、表单联动配置、3D 模型/地图钻取或者实时监控看板，纯文本和静态图片很快就不够用了。

**MCP Apps**（`io.modelcontextprotocol/ui`，`SEP-1865` 已达 Final，扩展本身在 `ext-apps` 仓库中已达到 **Stable** 阶段）把交互式界面正式纳入了 MCP 协议。根据官方客户端支持矩阵与 Philipp Schmid 原文记录，目前 Claude（Web 与 Desktop）、ChatGPT、Cursor、VS Code GitHub Copilot、Microsoft 365 Copilot、Slack 新版 MCP 客户端、Google Antigravity、Goose、Postman、MCPJam 和 Glean 等主流宿主都已经落地了这一模式，覆盖行内聊天卡片、侧边发布看板和自定义文件查看器三种核心形态。

## 为什么不在工具结果里直接返回 HTML，而是用预声明 `ui://` 资源

早期的社区实验（如 MCP-UI 早期方案）曾尝试让工具调用直接在返回体里内联一段 HTML，虽然写起来省事，但很快在宿主侧暴露出三个硬伤：第一，每次工具执行完才开始传输前端代码包，首屏渲染延迟高，无法流式展示工具输入；第二，界面模板和业务数据搅在一起，宿主没法缓存 UI 壳子；第三，安全团队无法在执行前对 UI 资源做静态审查或版本核验。

因此 `SEP-1865` 统一了 MCP-UI 与 OpenAI Apps SDK 两条路线，确立了**工具声明加预声明 `ui://` 资源**的双原语解耦架构（`docs/extensions/apps/overview.mdx` 与 `docs/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp.mdx`）：

1. **能力协商**：客户端在每条请求的 `_meta["io.modelcontextprotocol/clientCapabilities"].extensions` 里声明支持 `io.modelcontextprotocol/ui` 及可渲染的 MIME 类型（目前首发基线为 `text/html;profile=mcp-app`）；服务端在 `server/discover` 中广告同一扩展。面对不支持该扩展的纯文本或命令行客户端，服务端必须按优雅降级原则继续返回有意义的文本或 `structuredContent`。
2. **元数据绑定与预加载**：需要交互界面的工具在工具描述中挂上 `_meta.ui.resourceUri`，指向一个 `ui://` 资源（例如 `ui://analytics/latency-dashboard`）。因为资源地址在 `tools/list` 阶段就已经声明，宿主完全可以在工具真正被调用之前就预取并缓存 HTML 模板，甚至在工具还在流式输出参数时提前把界面架子撑起来。
3. **资源获取与策略解析**：宿主通过核心 `resources/read` 拉取该 `ui://` 资源，拿到打包好的 HTML/JS/CSS（资源元数据 `_meta.ui` 内还可声明 `csp` 外链白名单与 `permissions` 如麦克风/摄像头请求）。
4. **受限 iframe 渲染与双向 JSON-RPC**：Web 或桌面宿主把 HTML 放进严格受限的沙箱 `<iframe>` 中渲染。App 与宿主之间不发明专有私有消息格式，也不向 iframe 注入宿主私有全局对象，而是通过浏览器标准 `window.postMessage` 通道收发 **MCP 风格的 JSON-RPC 消息**（复用 `tools/call` 等核心方法，加上 `ui/initialize` 等 `ui/` 前缀专用方法）。

下面这条完整交互链路最能体现 MCP Apps 相比独立 Web 应用的优势：

- 模型调用 `get_latency_report` 工具，服务端返回底层的 `structuredContent` 指标，宿主把结果推送进对话里已渲染好的折线图 iframe；
- 用户直接在图上拖拽时间区间、点选某条毛刺曲线，iframe 通过 `postMessage` 向宿主发一条 `tools/call` 请求下钻该时间段慢查询；
- 宿主按现有 MCP 鉴权与权限策略（必要时弹窗征求用户同意）转发给 MCP Server，拿到最新数据推回 iframe 刷新图表；
- 用户选中某条异常慢查询并触发助手分析动作，iframe 通过上下文更新消息把选中项回传给宿主和模型，对话直接接着往下走，全程零跳转、零重复登录、零手写外部前端鉴权代码。

更重要的是，iframe 里的 App 还可以把跨域动作**委托给宿主能力**。比如一个项目管理 MCP App 需要发一封延期通知邮件，它不需要自己去对接 Gmail 或 Outlook 的 OAuth 接口，只需向宿主发出意图请求，由宿主经用户确认后路由给用户早已接好的邮件 MCP Server 执行。

## 三种宿主视图形态与产品边界

在 Philipp Schmid 的观察和主流宿主落地实践中，MCP Apps 已经分化出三种各司其职的呈现形态：

- **对话行内视图（Inline in Chat）**：适合延迟图表、审批确认单、代码差异对比、地图点选和小型配置表单，紧贴上下文讨论，看完即走。
- **独立侧栏工作区（Side Panels）**：适合发布看板（Release Board）、持续刷新的实时监控面板、多步骤工单分诊队列，让用户一边在左侧和 Agent 讨论，一边在右侧持续操作持久视图。
- **自定义文件查看器（Custom Viewers）**：适合 PDF 批注、3D 场景（如 `ext-apps` 官方示例中的 Three.js 与 CesiumJS 地球仪）、音频乐谱、白板（如 Excalidraw）等富媒体内容，替代简陋的纯文本预览。

反过来，官方指南也很清醒地划出了**不该做 MCP App 的边界**：如果你的界面与对话上下文无关、不需要模型协助决策、也不需要复用宿主已连接的其他工具能力，做个普通独立 Web 应用反而更简单、屏幕空间也更大。MCP Apps 的真实价值在于把人机协作最频繁的那几步可视化确认和钻取无缝嵌进 Agent 工作流，而不是把整套后台管理系统硬塞进聊天框。

## 沙箱边界与宿主侧安全治理

把第三方 MCP Server 返回的可执行 HTML/JavaScript 直接放进用户主应用里跑，如果不把隔离做死，后果比普通跨站脚本更严重。`SEP-1865` 和 `ext-apps` 的安全模型（宿主侧官方提供了 `@modelcontextprotocol/ext-apps` 的 `AppBridge` 与 `@mcp-ui/client` 组件）守住了四道关口：

1. **强隔离 iframe 沙箱**：iframe 无法访问父页面 DOM、无法读取宿主 Cookie 和 `localStorage`、无法顶层导航劫持父页面，也不能在父窗口上下文里执行脚本。
2. **严格 CSP 与最小能力授权**：除非资源的 `_meta.ui.csp` 明确声明且宿主安全策略放行，默认封死任意外域脚本加载与外联请求，防止界面偷偷把对话推送进来的敏感业务数据外传（Data Exfiltration）。类似 `sendOpenLink`、麦克风、剪贴板等宿主能力默认关闭，按需受控开放。
3. **可审计的 JSON-RPC 桥接**：UI 发起的每一次 `tools/call` 或上下文注入都必须经过宿主的 `postMessage` 桥接层。宿主可以完整记录审计日志、限制该 App 只能调用自身所属服务器的白名单工具，并对所有写操作强制弹人工确认框。
4. **防界面侧间接提示注入**：UI 回传给模型的上下文更新同样来自不可信的第三方代码。如果一个恶意 MCP App 在用户无感知的情况下偷偷往模型上下文里塞进隐藏指令（比如诱导模型去调另一台服务器的删除工具），宿主必须把 UI 上下文更新明确标示来源，并对跨服务器工具调用保持严格的人工审批门禁。

说到这里，我们已经把 2027 年 MCP 运行时最亮眼的拼图全部摊开了：无状态 HTTP 内核、强类型契约与 Code Mode、MRTR 与 OAuth 2.1/企业 IdP、Skills、Tasks、Events 和 Apps。但 Philipp Schmid 在文章后半段特意泼了一盆很务实的冷水：这套体系还有三块硬骨头没啃完。下一章我们直面这些未解的工程缺口，把发现机制、意图可观测性和纵深安全攻击面一次拆透。
