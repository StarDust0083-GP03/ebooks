# 前端状态机：离线订阅、五道恢复门与历史竞态

写一个简单聊天页只要维护一个 `messages` 数组，收到 token 就往最后一条拼字，收到 `done` 就解锁输入框。但放到 Octop 的真实对话里，这套玩具写法几分钟就会崩：用户切去设置页再切回来，在途流状态全丢了；工具执行 40 秒没吐字，前端误以为断线把气泡封死了；团队房间里成员 A 刚吐完 `done`，主持人还在汇总，输入框就被错误解锁了；下拉刷新历史请求慢半拍回来，把屏幕上刚生成的新回答一把冲掉。

Octop 的 React 18 + TypeScript 前端（`dashboard/`）之所以把聊天核心做成一台独立的外部状态机，就是为了把这些并发时序锁死。

## 为什么把 chatStore 搬出 React 组件树

打开 `dashboard/src/pages/Chat/hooks/chatStore.ts`，最核心的设计决策写在开头：WebSocket 实例、流式累计消息、在途说话人集合和订阅状态全部放在**模块级 store** 里，React 页面组件只通过快照订阅读取状态。

这样一来，用户在工作台里从对话页切到知识库、云主机或设置页，聊天组件被 React 卸载，底层 WebSocket 和累计缓冲区纹丝不动；切回聊天页时组件直接接上最新快照，屏幕不闪烁也不丢字。同时要分清边界：这个模块级 store 只跨越单页应用内部的路由切换，页面真正刷新或关掉浏览器后，仍然要靠服务端历史接口和 `turn_active` 标志重新接轨。

另外从 `v1.0.3b5` 起，服务重启升级后前端会主动清理 Service Worker 缓存再重载（PR #2901），避免旧版静态 JS 与新版后端协议字段错位；Monaco 编辑器资源也改为从同源提供（`v1.0.3b3`），不依赖外网 CDN。

## 帧归属与多说话人合并

服务端推送下来的原始帧，经过 `dashboard/src/utils/parseHarnessChunk.ts` 统一翻译成前端事件，再由 `frameThread.ts` 决定它该落进哪条线程的缓冲区。这里有三个容易踩坑的边界：

- **同一条 WebSocket 上不止一位说话人**：团队房间里，主持人和多位成员的流通过同一条房间订阅推下来，前端必须严格按帧里的说话人标识分别归并气泡，成员的 `done` 事件只能封口该成员的气泡，不能把仍在活跃状态的主持人或整条房间流误判为结束。
- **本轮改动文件清单**：`v1.0.3b6` 起，对话流会把本轮编辑过的文件路径、所在目录与增删行数汇总展示在聊天区，让开发者不用切终端就能核对原生或托管专家到底动了哪些文件。
- **多方同时共享会话防重叠**：`v1.0.3b5` 修复了共享对话场景下并发回复重叠和消息顺序错乱的问题，结合前端乐观渲染生成的 `client_message_id`（后端在 `turn.py` 原样校验并沿用），保证用户提问和模型回复顺序对齐，也避免已经结束的一轮在历史加载后再重复追加一次（`v1.0.3b2` 修复）。

## wsResumeGate.ts 里的五道恢复门

`dashboard/src/pages/Chat/hooks/wsResumeGate.ts` 是整套前端里含金量最高的小模块，全文件只有一百多行，把弱流式恢复拆成了五个纯函数门禁，每个门禁都直接对应一种线上事故：

| 门禁函数 | 解决什么问题 | 关键常量与判断条件 |
| --- | --- | --- |
| `shouldResumeStreamAfterClose` | 意外断线后要不要自动重订阅 | 非主动关闭、正在流式、线程有效、未超 `MAX_STREAM_RESUME_ATTEMPTS = 8`、且当前标签处于聚焦状态（后台标签不疯狂重连） |
| `shouldProbeActiveTurn` | 打开历史页时要不要探测在途任务 | 仅在本地 `isStreaming` 或服务端历史返回 `turn_active === true` 时探测，绝不靠猜最后一条消息像不像没说完 |
| `shouldBlockHistoryRefresh` | 下拉刷新历史把在途新回复冲掉 | 仅当 socket 仍存活且存在正在生成的流、活跃说话人或 `streaming` 气泡时拦截；断线状态放行恢复路径 |
| `shouldForceSealStream` | 断线太久连不上，气泡永远转圈 | 仅在无活 socket、聚焦状态且距上次活动满 `STREAM_STALE_WITHOUT_SOCKET_MS = 20_000` 毫秒才强制封口；活 socket 豁免，绝不误杀长工具调用 |
| `shouldEmitStreamResumeNotice` | 重连成功后的提示控制 | 仅在非首次重连（`resumeAttempt > 0`）且服务端确认 `turnActive` 时弹一次提示 |

把这五个判断从巨大的 React 组件和 store 里抽成纯函数，有两个立竿见影的收益：第一，不会再有人提议“20 秒没收到 token 就把加载关掉”这种会误杀 60 秒工具调用的拍脑袋逻辑；第二，每个边界都能用极快的单元测试把边界条件全铺满。

## 历史回填与实时增量的竞态治理

在弱网或慢查询下，历史加载经常和实时流撞车：比如前端先发了一个拉取历史的 HTTP 请求，等它返回前 WebSocket 又推来了三段新文本和一条工具结果。如果直接粗暴地用历史响应覆盖当前列表，新字瞬间消失；如果无脑拼接，同一条工具结果又会出现两遍。

除了上面提到的 `shouldBlockHistoryRefresh`，`chatStore.history.test.ts` 里专门用事件序列测试锁死了各类合并边界：审批暂停时刷新历史、成员结束但主持人未结束、历史请求失败保留本地内容、取消后收到迟到帧，以及老版本里出现过的过期缓存把历史顺序倒置的问题（`v1.0.3b3` 修复）。类似的竞态也出现在记忆管理页：连续切换不同专家加载记忆列表时，慢返回的旧响应不能覆盖新专家的列表，这也是 `v1.0.3b7` 专门补齐的一处防过期响应守卫。

## 源码入口

- [`dashboard/package.json`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/package.json)
- [`dashboard/src/pages/Chat/hooks/chatStore.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/chatStore.ts)
- [`dashboard/src/pages/Chat/hooks/wsResumeGate.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/wsResumeGate.ts)
- [`dashboard/src/pages/Chat/hooks/frameThread.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/frameThread.ts)
- [`dashboard/src/utils/parseHarnessChunk.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/utils/parseHarnessChunk.ts)
- [`dashboard/src/pages/Chat/hooks/chatStore.history.test.ts`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/dashboard/src/pages/Chat/hooks/chatStore.history.test.ts)
