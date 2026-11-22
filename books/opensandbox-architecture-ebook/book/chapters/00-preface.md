# 前言：一次创建请求背后到底发生了什么

周五下午你给编码 Agent 派了个修 bug 的活：拉仓库、跑单测、改两行代码、再跑一遍测试。模型很快吐出了一段补丁和三条 shell 命令，可这几条命令必须找个地方真跑起来。直接放宿主机跑，一次失误就能把本地环境搅乱；放到传统微服务部署链路里，等 CI 构建完镜像再调度 Pod，对话早就凉了。

OpenSandbox 解决的就是这个缺口：在几秒甚至几十毫秒内，按一次 HTTP 请求拉起一个带隔离边界的执行环境，让 Agent 跑命令、读写文件、开交互终端、按白名单联网，用完即焚。本地用 Docker 起步，上集群换成 Kubernetes 或 Firecracker microVM，调用方看到的始终是同一套沙箱接口。

全书围绕一条主线展开：**客户端发出 `POST /v1/sandboxes` 之后，控制面、运行时后端、沙箱内守护进程和网络组件各自做了什么，又为什么这样划分边界。**

## 从 2026-10-30 快照到 v1.3.0-rc.2 的关键演进

本书前版固定在 `c2f55b20e945`（2026-10-30），本次全面核对并同步至 **`release-1.3.0-rc.2`**（提交 `45043e7f0df258b6b76ef6753dd9b1819d59a332`，2026-11-19）。在这三周涵盖 `1.2.1` 与 `1.3.0-rc.1` / `rc.2` 的迭代里，底层两平面、三后端的骨架没有推倒重来，但有六处直接影响架构与运维的变化已经融入对应章节：

1. **OSEP-0018 `execd-as-init` 成为服务端默认拓扑**。`runtime.execd_run_as_init` 默认值由 `false` 改为 `true`，服务端自动注入 `EXECD_INIT=1`，让 `bootstrap.sh` 以 `exec execd --init -- <entrypoint>` 接管容器 PID 1（在 Fast Sandbox 中为 guest init 下的 subreaper）。孤儿进程回收、信号转发、退出码透传以及基于 `opensandbox-launcher` 的进程降权地板从此覆盖镜像入口进程（第 9、15 篇）。
2. **`rootfsOnly` 快照登上公开生命周期 API 并覆盖原生 S3 与 OCI**。五大 SDK 全部暴露 `create_snapshot(mode="rootfsOnly")`，产出 KB 级可写层制品并以冷启动方式恢复；服务端移除了多余的 OCI 预检镜像逻辑，把模式准入交由 FastPath 门禁与 Fastlet 的 fail-closed 节点资质自推导把关，而 pause/resume 检查点依然保持 `full` 模式（第 8、13 篇）。
3. **BatchSandbox 新增粘滞 `Completed` 终态，消除状态悬空与两个静默丢失竞态**。主容器以退出码 0 正常结束后进入粘滞 `Completed` 相位，对外映射为 `Terminated`（原因码 `SANDBOX_COMPLETED`）并拒绝 pause/resume，不再从 `Succeed` 跌回 `Pending`；同时 Docker 续期纳入每沙箱生命周期锁序列化，Kubernetes 到期删除与状态写入都带上了 UID 或 `resourceVersion` 前置校验（第 6、7、13 篇）。
4. **Credential Vault Exchange 与实验性 credential-bound 出向链路落地**。OSEP-0025 的动态凭据交换在 `1.2.1` 与 `1.3.0` 补齐了出向请求头透传（上限 64 个头、单值 8 KiB、超限 407），支持 SigV4/ACS 按请求重签且不落盘明文；Fast Sandbox 侧的实验性 credential-bound 拦截推进到 50 ms `data-plane-ready` 确认、不可变 dispatch 快照、排水围栏与源 IP 复用阻断（第 11、12 篇）。
5. **观测与诊断面完成清理与扩容**。已废弃的 `api/devops.py` 纯文本路由与 `osb devops` 命令群彻底移除，稳定的 Diagnostics API 强制要求 `scope` 参数；`execd` 补上了带 2 秒挂载超时的 OTLP 磁盘用量与 IO 指标，Node Agent 新增可选的 `vm-console` 采集源（第 9、16 篇）。
6. **部署基线与控制面扩展边界收紧**。所有 Helm Chart 将 Kubernetes 最低版本提升至 `1.22`；Pool 管理移至 `WorkloadProvider` SPI 之后；Fast Sandbox 强制 `--role=all` 单副本 `Recreate` 升级、开放 `controller.fastletProxy.resources` 与可选 gRPC-Web 端口、补齐 FSEP-0003 的 Fastlet ServiceAccount JWKS 鉴权；实验性 `components/e2b-shim`（OSEP-0027）已以骨架形态入仓（第 5、8、17 篇）。

## 快照与证据边界

把研究边界在这里一次性说清，正文不再反复叠甲。

- **上游仓库**：`https://github.com/opensandbox-group/OpenSandbox`
- **版本与提交**：`release-1.3.0-rc.2`，commit `45043e7f0df258b6b76ef6753dd9b1819d59a332`（2026-11-19）
- **核对日期**：2026-11-22
- **上游许可**：代码 Apache-2.0，文档 CC BY 4.0（`CHARTER.md` 第 7.b.iv 条）

本书全部结论来自静态阅读源码、`specs/` 下的四份 OpenAPI 契约、`docs/` 下的架构与运维指南、`oseps/` 下的 28 份设计提案，以及用 Python 标准库独立编写的配套教学实现 `examples/mini_osb.py`。本书没有部署真实集群，没有复现性能压测，也不构成安全审计。正文出现的所有延迟数字均转述自上游公开基准并标注口径与适用条件；遇到基于静态代码推理设计动机的地方，会直接点明这是工程推演而非作者口述记录。完整来源清单与 104 个核对文件的 SHA-256 哈希见附录 B 与 `sources/inventory.json`。

## 怎么读这本书

全书 20 篇正文按一条请求的生命周期顺序排开，另附 3 份附录。

- **只想弄清调用方式与系统边界**：读第 1、2、3、10、13 篇，查阅附录 A 的路径表。
- **在评估自建还是接入这套执行层**：重点看第 2、5、17、18 篇，它们专门算兼容性、单活边界和设计代价。
- **准备改代码或写新后端**：从第 4 篇顺序读到第 16 篇，先吃透控制面的四条不变量和三后端分工，再进 `execd`、ingress、egress 的源码。
- **想离线跑一遍核心语义**：直接运行 `python3 examples/mini_osb.py` 与 `python3 -m unittest discover -s examples -v`（附录 C 收录了完整源码）。
