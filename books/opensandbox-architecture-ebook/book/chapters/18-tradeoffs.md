# 权衡：这套架构主动放弃了什么

前十七篇一直在解释现有设计如何运转。任何系统只要跑在真实约束下，换取一项特性就必然丢掉另一项。把 OpenSandbox 最结构性的十个取舍翻到反面来看，才能判断它在什么时候顺手、什么时候会开始硌脚。

## 1. 无状态前缀路由，放弃多后端扩展弹性

`CompositeSandboxService` 用 `fsb-` 前缀把请求分给 Fast Sandbox，其余归 Kubernetes 容器后端。这省掉了维护 `sandbox_id -> backend` 映射表的一致性负担，控制面崩溃重启后路由信息零损失。代价是前缀成了散落在路由、SDK 约定与运维排障里的隐式知识；目前两个分支撑得住，一旦未来接进第三个需要并存的底层运行时，靠字符串前缀就得换成真正的元数据索引。

## 2. 统一公开契约，放弃后端能力完全等价

调用方用同一份 SDK 从本机 Docker 迁到 Kubernetes 集群，学习与迁移成本极低。反面代价是 API 类型无法表达各后端的语义落差：Docker 和 Kubernetes 拒接 `rootfsOnly` 快照（`409`），Kubernetes 暂停恢复后 Vault 为空而 Fast Sandbox 自动带走，Fast Sandbox 不支持卷挂载。目前全局层面没有统一的能力协商端点，只能靠 `docs/api/implementation-compatibility.md` 和局部探测（如 `/v1/isolated/capabilities`）补位。

## 3. 预热池与模板，放弃按请求任意定制

预热 Pod 把 100 个沙箱的交付压到 0.92 秒，预编译 golden image 把串行 microVM 创建 p50 压到 64 ms。为了这份速度，请求侧失去了在创建瞬间随意改 Pod 模板的自由：池化沙箱的 `entrypoint`/`env` 改走分配后 task、`poolRef` 排斥 `networkPolicy` 和 `volumes`，模板沙箱直接拒绝请求级 `entrypoint`/`env`，数据面还必须为安全复用补上 `execd` 的 per-allocation 原子绑定与未绑定前 `503` 门禁。当每个请求都需要截然不同的定制镜像时，预热收益会被池碎片化吃光。

## 4. 容器 Sidecar 与 Fastlet 共享 Egress 的两套实现

每沙箱挂一个独立 egress sidecar，网络隔离最直观，策略彼此零干扰，但每个沙箱多起一套 DNS 代理和规则进程，节点密度上不去。Fast Sandbox 反过来在一个 Fastlet 里塞进多台 microVM 共用一个共享 egress 进程，把密度拉回来了，代价是执行路径陡然变厚：要靠源 IP 分发、维护 per-subject 状态机、在实验性 credential-bound 模式下做排水围栏和源 IP 复用阻断。上游没有勉强把两套压成一套逻辑，而是做成互斥 profile，诚实承认不同密度下没有单一最优解。

## 5. 全线 fail-closed，放弃静默降级换来的虚假可用性

从启动期缺少 API key 或命名空间不可达直接 `os._exit(1)`，到节点不合格时 `rootfsOnly` 快照直接失败绝不回退 `full`，再到 Vault 私有 socket 故障立刻返回 `503` 或断连、Exchange 超出 64 头直接返 `407`，这套系统在安全与数据边界上几乎清一色选了 fail-closed。原因很现实：安全组件一旦静默降级，请求看似全绿，实则在裸奔放行或泄漏秘密。运维要付的代价是，egress sidecar 和 Vault socket 从附属组件变成了数据面可用性的硬依赖。

## 6. 单活控制面，放弃零中断控制面升级

生命周期服务器默认单副本 `Recreate`，Fast Sandbox 全功能控制器在 `1.3.0-rc.2` 更是被 Chart 硬锁单副本 `Recreate`。这省掉了分布式选主、内存放置视图共识和跨节点锁的巨额复杂度，使得控制面代码保持单线程直觉，只在 PostgreSQL 公开快照创建这一窄路径上用 CAS 加恢复扫描支持双副本。这份取舍之所以站得住脚，前提是**控制面故障域与数据面解耦**，升级期间在跑沙箱不断流；一旦业务规模大到每秒百次创建、连几秒控制面升级窗口都扛不住，就必须等 ROADMAP 里的通用控制面高可用落地。

## 7. SSE 文本流，放弃命令通道内置双向控制

命令与代码执行选用 HTTP SSE 而不是全双工 WebSocket（仅 PTY 走 WebSocket），换来的是普通 HTTP 客户端、代理网关和浏览器零门槛消费。放弃双向通道的后果，是中断命令必须另发请求，客户端必须死守「收到 `execution_complete` 才算结束、超时识别 `CommandTimeout`、断流视为不确定」的解析纪律，并且还要警惕强类型语言客户端在事件新增字段时的反序列化兼容问题。

## 8. 绝对 TTL，放弃省心的原生空闲超时

把 `timeout` 钉死成创建时刻起算的绝对过期时间，暂停不延寿、重启不重置，换来了极强的资源审计确定性：任何沙箱为什么还活着都有唯一账本。代价是违背了交互式用户「只要还在用就不该断」的直觉，结果平台不得不再叠加上访问续期（OSEP-0009）与空闲暂停/唤醒（OSEP-0024）两套独立子系统来补齐体验。

## 9. `full` 与 `rootfsOnly` 双轨快照，接受恢复体验分化

`1.3.0` 把 `rootfsOnly` 推上公开 API 后，Fast Sandbox 终于能以 KB 级制品替代动辄数 GB 的内存快照，代价是同一份快照接口下出现两种截然不同的恢复语义：`full` 恢复保留内存与在跑进程，耗时数十毫秒级但强绑定同构 VMM 版本；`rootfsOnly` 丢弃内存与进程、冷启动重跑 `entrypoint`，耗时升到秒级但换来更轻的存储与跨节点可移植性。调用方必须清楚自己要的是「接着跑的现场」还是「干净的文件起点」。

## 10. 生态兼容靠独立旁路 Shim，不污染主契约

面对外部生态（如 E2B 协议调用方），`1.3.0-rc.2` 引入的 OSEP-0027 没有把异构路径揉进 `sandbox-lifecycle.yml`，而是在 `components/e2b-shim` 另起独立 Go 模块按骨架逐步演进。主契约因此保持克制干净，代价是兼容层独立部署与后续路由对齐需要额外维护成本。

## 给自建者的选型对照

如果正打算从零搭一套类似的 Agent 执行层，可以把这十条压成七句工程判断：

| 你的约束 | 推荐落点 |
|---|---|
| 运行时后端只有两个 | 前缀路由足够用，别过早造全局映射数据库 |
| 计划持续接入多种异构后端 | 第一天就设计机器可读的能力声明接口，别全靠文档维护 |
| 创建延迟压到百毫秒级是硬指标 | 上预热池或模板快照，但同步设计复用时的原子身份绑定 |
| 单机密度决定毛利 | 把每沙箱 sidecar 换成节点/Pod 级共享代理，并接受源 IP 围栏复杂度 |
| 安全子系统失效时无明显报错 | 全线 fail-closed，并把它当作数据面一等可用性依赖来监控 |
| 控制面变更频次不高 | 先守住控制面与数据面解耦，单活加清晰升级窗口远胜半吊子多活 |
| 存在多档快照与持久语义 | 在创建与快照 API 上显式区分模式（如 `full` / `rootfsOnly`），不让调用方猜 |

## 源码入口

| 路径 | 看什么 |
|---|---|
| `docs/api/implementation-compatibility.md` | 契约覆盖与后端能力落差的公开账本 |
| `docs/architecture/fast-sandbox/ha.md` | 单副本控制面的明确设计立场 |
| `server/opensandbox_server/services/composite_service.py` | 零状态前缀分发实现 |
| `oseps/0027-optional-e2b-compatibility.md` | 独立旁路兼容层（`components/e2b-shim`）提案 |
| `ROADMAP.md` | 当前公开承认的未完成项与优先级 |
