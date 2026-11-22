# 一次对话：从 WebSocket 入队到工具熔断与推送

用户按下回车却迟迟不见回字，新手往往直接去换模型或查 API Key，老手则会沿着一条请求穿过的六道关卡逐段排查：WebSocket 建连鉴权过了没有、线程订阅有没有赶在首个 chunk 发出前注册好、消息入队后走的是新回合还是在途追加（steer）、路由解析出的模型是不是已经失效、工具调用有没有触发熔断或审批暂停、最后历史记录和帧推送有没有落到正确的线程上。

下面沿着 `/api/agents/{agent_id}/chat/ws` 把一次完整回合拆开来看。

![一次对话回合的六步处理链路](../../wiki/assets/diagrams/turn-pipeline.svg)

## 先把传输连接和任务执行拆开

浏览器连上 `/api/agents/{agent_id}/chat/ws` 后，服务端在 `src/octop/api/routers/chat/ws.py` 里独立完成 JWT 校验和专家访问权核查，再把连接登记进 `WebSocketHub`。这条连接只管收发控制帧（`user_turn`、订阅、取消、审批响应）和接收下行推送，不亲自持有模型循环。

当收到一个 `user_turn`，`src/octop/api/routers/chat/turn.py` 的 `prepare_dashboard_turn()` 先把网页表单校验并归一化：调用 `resolve_thread_id()` 确认线程归属和会话绑定，验证本轮勾选的 MCP 和 Skill 列表，提取乐观渲染用的前端消息 ID（`client_message_id_from_turn`），把工作区附件转成结构化块，最后打包成 `octop-gateway` 定义的统一 `InboundMessage`，送进 `ChannelManager.enqueue()`。

这里的入队是进程内异步队列，用来让 Web、CLI、飞书、钉钉、Telegram 和 Cron 共用同一套消费管线，不依赖外部消息中间件。对于 IM 入口，下行 chunk 会经 `src/octop/infra/gateway/process/stream_project.py` 投影成各平台能发的 `MessageEvent`；对于 Web 工作台，则由 `GlobalProcessor.iter_turn_chunks()` 保持细粒度原生帧直接推送。

## 一个靠时序踩出来的修复：先订阅，再让出控制权

`ws.py` 里藏着一条 `v1.0.3b1` 修过的经典异步竞态。从 `v1.0.2` 开始，Octop 支持对话进行中插话补一句（steer），不用等当前回合彻底结束。处理 `user_turn` 时，服务端要异步等待 steer 接纳判断。

如果代码写成先 `await` 接纳判断、再调用 `hub.subscribe()` 把当前 socket 加进线程房间，事件循环在 `await` 那一瞬间交出控制权，后台正在跑的回合恰好吐出前几个 chunk 往房间里广播，刚发消息的这个连接就会把开头几帧全漏掉。所以 `ws.py` 严格保证：**线程 ID 确定之后，立刻同步执行 `hub.subscribe()`，然后再去 `await` 后续 steer 接纳或入队**。这是纯事件循环调度问题，和模型响应快慢毫无关系。

## GlobalProcessor 里的前置拦截与模型路由

消息出队进入 `src/octop/infra/gateway/process/processor.py` 的 `GlobalProcessor` 后，并不是所有输入都会直奔 LLM：

- 如果输入是斜杠命令（如 `/compact`、切换模型等），或者正好匹配当前线程挂起的 IM 文本审批口令（`ApprovalUtterance`），宿主会在进入模型循环前直接截流处理，走专门的 slash 或 HITL 恢复路径。
- 如果本轮带了超长视觉图片，`v1.0.3b3` 起网关会在发给模型前先缩放长图，避免触发供应商尺寸限制报错。
- 接着进入 `_resolve_harness_model_route()` 决定这轮到底调哪个模型。它的优先级是一条清晰瀑布：本轮显式覆盖或线程记录模型 → 校验是否仍可用且在当前用户组权限内（失效或被停用的旧模型自动丢弃，防止死卡在已删模型上） → 专家显式默认模型 → 用户偏好模型 → 系统兜底模型 → 仍无则交给 harness 自动路由（AUTO）。如果本轮含多模态图片而选中模型不支持视觉，自动升级到具备视觉能力的候选模型。`v1.0.3b5` 还统一了 IM 通道的推理强度覆盖（`_resolve_reasoning_overrides`），让飞书和 Telegram 对话与网页端行为一致。

如果是托管专家（`kind="managed"`），此时还会调用 `managed_mcp_specs()` 组装外部连接器和 6 个本地回环 MCP 地址，并根据 `acp/session_modes.py` 算出本轮要下发的 `session/set_mode` 和审批模式。

## 执行循环里的三道护栏：熔断、故障转移与记录

进入 `AgentManager.stream()` 后，回合在 `octop-harness` 的运行图里向前推进，宿主靠几层中间件和观察器把现场托住：

- **连续相同调用熔断**：第 4 章读过的 `ConsecutiveToolCallMiddleware` 在每次模型返回 `tool_calls` 后检查签名连击数。同一工具同一参数连撞超过上限（默认 40 次），直接写错误 `ToolMessage` 与 `[tool_call_repeat]` 结束标记并 `jump_to="end"`，避免无限空转；只要换了工具、改了参数或回了纯文本，计数立刻归零。
- **受控模型故障转移**：`SafeModelFailoverMiddleware` 只在自动路由且命中可恢复异常类型时，按健康状态（`providers/health.py` 的半开探测与隔离机制）、多模态要求和上下文窗口容量挑选下一个健康候选模型，不会在用户显式锁死模型或发生业务逻辑错误时胡乱切模型重跑。
- **边流边记与产物去重**：流式循环中，`UsageTracker` 累计包含缓存 token 在内的用量，`TurnHistoryTracker` 收集消息增量并在回合节点写回 `thread_messages`，同时记录包含模型实际收到系统提示词在内的 `trajectory_events`（`v1.0.3b2` 新增）。工具执行产出新工作区文件时，会被转换成附件帧并去重，同时 `v1.0.3b6` 起汇总本轮改过的文件路径、所在目录和增删行数展示给对话界面，而且确保更早回合生成的文件不会在新回合里被重复发送一遍。

## 断线、取消与失败各自什么语义

把这三种非正常结束混成一件事，系统状态就会彻底糊掉：

- **WebSocket 断开**：`ws.py` 的清理块明确只注销当前连接订阅，**绝不取消正在运行的后台回合**。用户合上浏览器一分钟后再回来，任务可能已经跑完并落入历史，或者仍在运行等待重新订阅接上后续帧。
- **显式取消（cancel）**：校验线程归属后，标记 steer 取消、丢弃排队内容，并向 `AgentManager` 发送取消信号终止当前计算。但取消挡不住已经发出去的外部副作用：已经发出的邮件、已经删掉的云盘文件不会因为点停止按钮自动撤回。
- **上游异常失败**：`processor.py` 靠 `stream_ok` 标志和 `finally` 收尾区分完成与异常。出错时往客户端推送错误事件、在历史里按未完成或失败形态落账，不会把一句异常提示伪装成一条正常的 AI 回答。

## 源码入口

- [`src/octop/api/routers/chat/ws.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/ws.py)
- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)
- [`src/octop/infra/gateway/process/processor.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/process/processor.py)
- [`src/octop/infra/gateway/process/stream_project.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/gateway/process/stream_project.py)
- [`src/octop/infra/agents/middleware/consecutive_tool_call.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/consecutive_tool_call.py)
- [`src/octop/infra/agents/middleware/model_failover.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/middleware/model_failover.py)
