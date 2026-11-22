# 多 Agent 团队：共享展示时间线，隔离执行状态

把调研、法务审核和文档汇总交给三位专家一起干，最偷懒的做法是让三个模型轮流往同一条消息列表和同一份 checkpoint 里写内容。不出三轮就会乱套：谁的工具结果归谁、A 专家的私有工作区和记忆会不会漏给 B 专家、点停止按钮到底停哪一个、成员刚回复完结果该回推给网页还是飞书群。

Octop 在 `docs/expert-teams.md` 里定下了一条很清醒的架构原则：**用户看见的是一条共享房间时间线，底层每个专家各自维护独立的 checkpoint、工作区与长期记忆**。

## 主持人是特殊专家，不是一套平行表结构

在数据模型上，专家团队并没有另起一套复杂的独立实体系统。团队主持人本身就是 `agents` 表里 `kind = "team"` 的一条专家记录，团队编制（成员列表、分工说明等）存在主持人工作区的 `.octop/manifest.json` 里，复用同一套 `AgentManager` 生命周期、分享权限和会话体系。

区别只在能力边界：主持人的职责是拆解任务、通过 `TeamDispatchBriefingMiddleware` 向成员下发改写后的任务简报并收尾总结，不直接挂载完整的文件修改、浏览器控制和外部 MCP 执行工具；被编入团队的成员依然是独立的一等专家，同一位专家可以同时加入多个团队。

## 一个房间，多份独立 checkpoint

当用户在团队房间（会话 ID 即主持人的 `thread_id`）里发言，平台为房间内的不同角色分配各自隔离的图状态键：

```text
房间展示时间线 = 主持人 conversation_id
  ├── 主持人 checkpoint：conversation_id
  ├── 成员 A checkpoint：conversation_id~member_a
  └── 成员 B checkpoint：conversation_id~member_b

成员各自使用自己的 BackendWorkspace 与 octop-memory 命名空间
成员流式输出经房间转播，附带明确的说话人（agent）标识，同步到 Web 与 IM
```

展示共享与状态隔离分开之后，好处立竿见影：

- 成员加入团队，不会自动获得读取同事私有工作区或私有长期记忆的权限；团队级准则和汇总记忆留在主持人这一侧。
- 主持人把群聊背景和本次交付目标整理成明确派工简报发给成员，成员干完活只把结果交回房间，不把中间几十步探索工具调用全倒进主持人的上下文窗口。

## 异步派工、成员并行与回叫串行

团队协作有两种调用节奏：普通专家之间互相调用（`ask_agent`）以同步等待返回为主，托管专家也可以通过第 10 章的 `octop-agents` 回环 MCP 发起同样的专家回调；而团队主持人向成员派工则是异步的，主持人派完活立刻释放当前流，等成员跑完后由平台自动回叫主持人做下一轮汇总。

异步回调靠 `TeamManager` 和 `GlobalProcessor`（实现 `TeamProcessor` 协议）串联。每条派工任务都会带上 `source_agent_id`、`source_thread_id`、目标成员和 `job_id`，确保几分钟后成员交付结果时，平台准确知道该写回哪个房间 checkpoint，并通过 `on_reply` 把消息推回网页或对应的 IM 群聊。

并发调度遵循两套串行粒度：inbox 按被调用者（callee）协调，同一个成员收到的多条任务串行执行，不同成员之间可以并行跑；成员完成后回叫主持人时，则按源 `thread_id` 串行排队，避免两位成员同一秒完成把主持人总结流写花。成员在干活时可以同步询问另一位同事，但不允许异步再往群里拉新成员，防止无限套娃。

## TeamJobTracker：按 job_id 做幂等在途记账

团队正有成员在后台跑任务时，如果管理员突然把该成员移出团队或直接删掉这位专家，在途回调回来就会找不到目标。`src/octop/infra/agents/teams/jobs.py` 里的 `TeamJobTracker` 专门记录活跃任务计数，挡住这类破坏性操作。

看它的 `begin()` 实现：

```python
def begin(
    self,
    team_agent_id: str,
    member_agent_id: str,
    job_id: str | None = None,
) -> None:
    key = (team_agent_id, member_agent_id)
    with self._lock:
        if job_id:
            if job_id in self._jobs:
                return
            self._jobs[job_id] = key
            return
        self._counts[key] = self._counts.get(key, 0) + 1
```

以 `job_id` 为键做去重比简单加减整数稳得多：同一条任务因为重试或状态重放重复触发 `begin` 时不会把计数加两遍，结束时按 `job_id` 摘除也不会减成负数。同时源码注释也坦诚写明了它的边界：`TeamJobTracker` 是进程内字典加线程锁，进程重启就归零，它不提供跨进程或跨重启的任务 fencing。

## 点停止按钮，究竟停掉了谁

这是多 Agent 界面里最容易让用户困惑的一条边界，也是 `docs/expert-teams.md` 明确写死的行为：**用户在 Dashboard 点取消，只取消主持人当前的流；已经排队或正在运行的成员任务不会被一并强杀**，成员跑完后依然可能把结果回传并唤醒主持人。

如果业务方要求一键急停整个任务树并且禁止任何后续外发，光在前端隐藏成员气泡没用，必须在服务端引入整棵任务树的取消传播、回调抑制和工具侧拦截。了解当前的真实停止语义，比把既有行为误解成页面漏消息重要得多。

## 团队与 Subagent 别混着用

仓库里同时存在 `infra/agents/subagents/`（子 Agent）和 `infra/agents/teams/`（专家团队）。两者定位不同：子 Agent 是单个专家运行时内部临时派生出的子任务执行体，适合短平快的局部拆解；专家团队编排的是拥有独立账号归属、工作区、知识库、连接器权限和长期记忆的一等专家。能用单个专家加子 Agent 解决的流程，没必要硬建三四个长期专家来维护编制。

## 源码入口

- [`docs/expert-teams.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/expert-teams.md)
- [`docs/agent-interop-mailbox.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/agent-interop-mailbox.md)
- [`src/octop/infra/agents/teams/jobs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/jobs.py)
- [`src/octop/infra/agents/teams/team_manager.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/team_manager.py)
- [`src/octop/infra/agents/teams/service.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/teams/service.py)
