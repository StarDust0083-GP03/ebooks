# 脱离工作、工具审批与权限边界

一个能改文件、跑 Shell、连 MCP 服务器的本地编码 Agent，真正容易出事故的往往不是模型写错一行语法，而是权限边界画歪了。早期版本里，Dim 自己也踩过两个很典型的坑：一是给每个基于配置启动的外部命令（MCP、Hook、Workflow、插件）都准备单独搞一套“批准、指纹、撤销”接口，结果把“配置要运行”和“模型临时决定调用工具”混成一团；二是给工具调用设了两套互相打架的准入关卡，上面 `Permission` 刚判了放行，底下的 `HostPlatform` 又去读一份独立的 `host-isolation.json` 把它否决掉，导致用户选了 `full-access` 却照样跑不动普通构建命令。

在 `docs/adr/` 里，ADR 003、009、010、012、014、017 和 018 完整记录了 Dim 怎样把这团乱麻拆成三条互不越权的清晰边界。

## 脱离工作：为什么删掉统一的后台任务层

先看一类经常让权限和中断逻辑发疯的场景：用户在当前 turn 里让 Agent 启动了一个耗时很长的编译或测试命令（`BackgroundExec`），或者派出了一个协作子代理（`Collaboration`）。这个工作越过了发起它的那个 turn 继续在后台跑，等将来跑完时再自动把结果送回分支并唤醒模型。Dim 把这类工作统称为**脱离工作（detached work）**。

早期实现里曾有过一套共享的 `BackgroundTasks`、`BackgroundTimeline`、`task` 工具以及共享事件 `task/started`、`task/ended`、`task/stop`。后来在 `event-driven-flows.md` §3 的重构中，这整套统一后台任务层被彻底删除了。原因和第 2 章撤回接线模块如出一辙：把所有后台任务塞进共享管家，既制造了第二份任务状态，又模糊了取消与重启恢复的归属权。

现在的合同简洁得多：

- **谁发起、谁拥有**：任务句柄、取消令牌、输出采集、结果查询工具和重启恢复全部归发起它的能力自持，用 `rutis` effect 登记卸载清理。结果用所有者自己的事件表达，例如 `BackgroundExec` 的 `exec/started` 与 `exec/ended`，`Collaboration` 的 `collaboration/result`。
- **两档中断各管各的**：用户按下普通中断发出 `scope=turn` 的 `stop/interrupt` 时，**不取消**该 turn 启动的脱离工作，后台进程继续跑，跑完后由所有者在结果事件的分发位置决定是否向分支提交一条 `mode=steer` 的唤醒输入；只有 `scope=branch` 的中断才会通知所有者取消本分支全部脱离工作，且必须**先释放资源，再写入终态事件**，且不再触发自动唤醒。
- **独立提交结果与唤醒通知**：`exec/ended` 和附带截断预览的唤醒输入分两次提交（`timeline-storage.md` 末尾）。这样做的好处是两次提交之间可以正常触发上下文压缩，唤醒输入自带预览、不反向引用 `exec/ended`，恢复快照也就不需要背着沉重的命令输出事实跑。
- **空闲关机保护，但不污染空闲状态**：所有者在接纳每项脱离工作前调用 `SessionLifecycle::take_hold()` 拿到会话活动持有，直到最后一个结果事实提交后才释放。只要还有活动持有，`daemon.shutdown { ifIdle: true }` 和会话空闲卸载就会被拒绝（显式关闭则直接停止并清理）。与此同时，`Session::idle` 依然只看 `Loop` 是否空闲，不把后台长任务伪装成主循环卡死。

## 三条权限边界与五份核心 ADR 的演进

![工具审批、文件事实、配置启用与执行隔离边界](../../wiki/assets/diagrams/approval-permission.svg)

上面这张图把 Dim 在当前版本收敛出的权限体系拆成了两条执行路径和一个独立的隔离开关，背后对应着几份互相承接、甚至自我推翻的架构决策。

### ADR 003：审批只管模型决定，不管配置文件

ADR 003 划出了第一道大坝：

- **路径 A（模型决策）**：模型临时决定调用哪个工具（包括调用某个已连上的 MCP 服务器里的工具），归 `Permission` 和 `Approval` 裁决。
- **路径 B（配置驱动）**：MCP stdio 进程启动、MCP HTTP/SSE 连接、Hook 脚本、Workflow 定义、插件包，这些命令之所以运行是因为配置文件写了它，不是模型临时起意。它们不走审批，没有指纹，也没有 `mcp.approve` 或 `mcp.revoke`（这些旧方法直接返回 `unknown_method`）。
- **项目来源默认禁用**：凡是来自工作区项目目录（如 `.mcp.json`、`.dimcode/`）的配置条目，默认全部禁用，而且项目文件里的 `enabled` 字段会被忽略。只有用户通过本地配置操作（记录在用户自己的 `mcp-projects` 等本地存储里，按规范化工作区路径和条目 id 索引）显式启用后才会运行。

ADR 003 D4 很坦诚地写明了接受的代价：一旦用户启用了某个项目条目，以后 `git pull` 拉下新提交改了该条目的命令或参数，不会因为指纹变化再次弹窗询问。启用一个项目条目，本身就意味着信任该项目对这条配置的修改；这本来就不是操作系统级沙箱，真要防恶意代码必须靠进程隔离。

### ADR 009 与 ADR 010：Permission 拍板，HostPlatform 只管执行，沙箱默认关闭

ADR 009 撤掉了 `HostPlatform` 内部的 `escalation` 二次否决门禁，确立了单一决策权：一个工具能不能跑，由 `Permission` 说了算；`HostPlatform` 只负责按照当前执行设置去拉起受限或非受限进程，不再反过来推翻已经通过准入的调用。

紧接着，ADR 010 面对本地编码 Agent 的真实体验做了一次务实纠偏：旧设计默认开启严格沙箱并断网，导致没写过策略文件的普通用户一上手就找不到 `$HOME` 下的工具链，包管理器连不上网，连常规开发都跑不动。ADR 010 决定：

1. **沙箱改为 opt-in（默认关闭）**：由用户在设置里通过开关决定是否开启进程隔离（macOS 用 seatbelt，Linux 用 `bwrap`，Windows 暂无沙箱后端，开启后准备阶段直接报 `isolation_unavailable`）；旧的 `host-isolation.json` 与 `host.isolation.*` 管理接口全部移除。
2. **剥夺模型自选沙箱的权利**：`exec` 工具彻底删掉了 `sandbox`、`paths` 和 `network` 参数，任何仍传 `sandbox` 字段的调用直接按 `exec_invalid_request` 拒绝。模型只能接受宿主给定的环境，永远不能通过调工具给自己松绑。
3. **`full-access` 不改变沙箱开关**：`full-access` 的语义严格限定为“不再弹窗询问工具审批”，它不会悄悄关掉用户打开的沙箱，也不会打开网络。

### ADR 012 与 ADR 014：固定文件事实与会话级持久化授权

对于 `read`、`grep`、`glob`、`write`、`edit` 这些文件操作，ADR 014 把裁决依据固定成四类事实，由 Session 级部件 `FileAccessFacts` 统一计算：`workspace`（工作区目录及经 Git 验证的受管 worktree 根目录）、`system`（平台系统只读根）、`owned`（本会话通过 `SessionFiles` 拥有或由本会话生成的命令输出文件）以及 `grant`（会话级授权）。路径检查排在工具白名单前面，先统一展开 `~`、`..` 和符号链接，再做裁决。

当文件操作落在工作区之外触发询问时，如果每次恢复会话都要让用户重复点同意，体验会很差。ADR 012 引入了持久化的 `session_grants` 表（按 `(session_id, path)` 建键，记录 `kind: file | dir` 和 `mode: read | write`），并严格锁死了四条安全约束：

- 授权仅在 `Permission` 判为 `Ask` 时生效，**绝不能把 `Deny` 洗白成 `Allow`**，在 `read-only` 预设下写授权完全不起作用；读授权也不能放行写操作。
- 授权只属于当前 Session，重启可恢复，随会话删除事务一起清空，但分叉出的新 Session 和协作子代理**不继承**任何授权。
- **模型永远不能写授权**：没有暴露给模型的 `add` / `remove` 工具，审批回复里也没有授权字段，领队模型替协作子代理答复审批时也不能写入授权；客户端是在用户点击“本会话允许”后，先调 `session.grants.add` 再回答 `approval.respond`。
- 目录授权按规范化路径前缀匹配而非 inode，读取授权失败一律默认拒绝。

### ADR 017 到 ADR 018：一次诚实的交互回撤

从 ADR 017 到 ADR 018 的转折，非常值得做产品和架构的工程师反复细看。ADR 017 当时觉得单个预设名说不清“文件范围”和“工具是否询问”，于是把 `PermissionPolicy` 扩出了两个可选覆盖字段 `fileAccess` 和 `toolApproval`，并在 `/permissions` 面板上摆了文件访问、工具审批、进程隔离三个独立控件。

上线后团队很快发现这套拆分反而把用户搞糊涂了：三个预设本身就已经把最合理的组合定死了，拆开之后暴露出来的组合（比如“路径无限制但每次调工具都要问”，或者“路径只读却跳过所有工具询问”）几乎没人真的想要；而且把属于 `ExecutionSettings` 的进程隔离硬塞进 `/permissions` 面板，反而让一个菜单背后挂了两个不同所有者。

于是 ADR 018 干脆利落地把拆分撤了回去：

- `/permissions` 面板只留三个预设：`read-only`、`workspace-write`、`full-access`，不再把文件范围和工具审批拆成独立控件，进程隔离退回 Settings 里的执行设置。
- `PermissionPolicy` 删掉 `fileAccess` 和 `toolApproval`，只保留 `preset`、`allowedTools` 和 `disallowedTools`（显式拒绝永远压过显式允许，且两张列表永远不能绕过文件事实或进程隔离）。
- 为了不重写用户磁盘上按 ADR 017 写下的旧 `permission/changed` 事件和定时任务文档，`PermissionPolicy` 主动拿掉了 `#[serde(deny_unknown_fields)]`，让旧记录里的那两个字段反序列化时被安静忽略。ADR 018 顺手把代价明明白白写进文档：这相当于放弃了对策略 JSON 拼写错误的解析拦截，拼错的字段会回退为默认行为；但外层的 Scheduler 任务和 Workflow 运行文档依然保持严格校验。

## 分支级审批状态机：双端抢答与中断竞争

沿着规则落地到代码里，每一次工具调用在 `ToolExecutor` 执行之前，都要穿过分支级 `Approval` 状态机（`docs/architecture/rust-agent/plugins/Permission.md` 与 `event-driven-flows.md` §2）：

1. `Approval::admit` 调用 `Permission::admit_decision`，依次结合预设、命令规则（`user/permission` 里的 `exact`/`prefix` 词级匹配，无项目级来源）、文件事实，若结果不是 `deny`，再按名称顺序询问已注册的工具顾问（例如 `HooksBranch` 注册的 `PreToolUse` 钩子，组合规则为任意 `deny` > 任意 `ask` > 任意 `allow`，顾问无权推翻既有的 `deny`）。
2. 若最终判定需要询问，`Approval` 向本分支时间线写入 `approval/request { approval_id, source, call, reason }`，通过 `SessionBroadcast` 推给所有在线客户端（新附着的客户端通过 `attach` 补发当前未决请求）。
3. 两个客户端同时抢答 `allow` 和 `deny`，或者用户在审批等待期间按下中断键，所有动作都转成写向同一条分支时间线的事实：`approval/respond` 或 `stop/interrupt`。
4. `Approval` 只按 `Dispatcher` 分发到的序号裁决：如果 `approval/respond` 先到且请求仍在未决表里，首个有效决定胜出，后面迟到的回复直接丢弃；如果 `stop/interrupt` 先到，或者同意后工具尚未启动时收到中断，`Approval` 写下 `approval/cancelled` 结案。每个已提交的 `approval/request` 都保证有且仅有一次终态结算，每次工具调用最多执行一次。

权限和审批守住了外部动作的入口，而模型侧的两大核心引擎，则是下一章要拆解的内嵌 JavaScript 沙箱 Code Mode 和统一多协议模型宿主 Aimux。
