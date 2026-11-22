# 附录 B：来源、版本与事实边界

## 上游快照固定信息

| 项目 | 核对值 |
|---|---|
| 仓库 | `https://github.com/opensandbox-group/OpenSandbox` |
| 发布标签 | `release-1.3.0-rc.2` |
| 提交 SHA | `45043e7f0df258b6b76ef6753dd9b1819d59a332` |
| 提交时间 | `2026-11-19 17:32:30 +0800` |
| 提交标题 | `Merge pull request #2944 from opensandbox-group/release/1.3.0-rc.2` |
| 核对与采集日期 | `2026-11-22` |
| 前版基线 | `c2f55b20e945d89efe3d35220942a997a3551751`（2026-10-30） |
| 上游许可 | 代码 Apache-2.0；文档 CC BY 4.0（`CHARTER.md` 第 7.b.iv 条） |

本书 `sources/inventory.json` 固定记录了 104 个核对过的上游源码、契约、Chart、指南与提案路径及其 SHA-256 哈希和行数。仓库继续演进后若出现细节漂移，以该提交为准。

## 核验范围与未做事项

本书基于静态源码精读、OpenAPI 契约比对、官方文档与发布说明核对，以及独立编写的标准库教学模型 `examples/mini_osb.py`（31 项离线单元测试）完成。

需要明确边界的事项只有五条：

- 未部署真实 OpenSandbox 集群，未执行端到端集成测试或容器/KVM 运行时验证；
- 未复现性能压测，文中所有延迟与吞吐数字均来自上游公开文档并标明口径；
- 不构成安全审计或法律合规审查，对隔离、nftables、Vault 与签名的阐述基于上游源码与文档声明；
- 独立仓库 `opensandbox-group/fast-sandbox` 的内部实现通过本仓库适配层、Chart 模板、架构文档和正式发布说明交叉核对，未单独克隆该上游仓库逐行审计；
- 教学实现 `examples/mini_osb.py` 仅在内存中复刻本书讨论的结构不变量，不能替代真实沙箱平台。

## 正文性能与限额数字出处

| 数字 | 出处 | 适用条件与边界 |
|---|---|---|
| SDK 创建 p50 64.0 ms / p90 93.8 ms / p99 108.3 ms | `docs/architecture/fast-sandbox/performance.md` | ACK 两节点，2026-10-08，Firecracker 1.16.1，模板已缓存，串行 100 次 |
| SDK 创建并发 10：p50 102.7 ms / p99 125.4 ms | 同上 | 100/100 成功 |
| SDK 创建并发 20：p50 136.7 ms，51/100 成功 | 同上 | 仅统计成功尝试；48 次 429 与 1 次超时被排除 |
| 纯运行时创建 p50 33.5 到 50.0 ms | 同上 | 不含网关与 SDK 就绪探测 |
| SDK 同节点恢复中位 62.66 s（runtime 恢复 35.49 ms） | 同上 | 3 个样本，差距根因尚未定位 |
| BatchSandbox 池化交付 100 个沙箱耗时 0.92 s | `docs/architecture/control-plane/operator.md` | 与非池化 `agent-sandbox`（76.4 / 23.2 / 33.9 s）的预热与现建对照 |
| 隔离会话创建约 100 ms | `docs/guides/isolation-sessions.md` | bubblewrap 启动加短暂退出探测 |
| `execd` 单行输出切分上限 16 MiB | `components/execd/pkg/runtime/output_limits.go` | 按 UTF-8 边界分段，防大行 OOM |
| `execd` OTLP 挂载 stat 超时 2 s | `components/execd/pkg/telemetry/init.go` | 防卡死网络挂载阻塞指标采集 |
| Jupyter 回复收尾默认超时 2 s | `components/execd/pkg/flag/parser.go` | `--jupyter-reply-finalize-timeout` |
| Vault Exchange 出向头上限 64 个、单值 8 KiB | `components/egress/` 与 `release-1.3.0-rc.2` 说明 | 越界以 407 fail-closed 拒绝 |
| `data-plane-ready` Hook 等待确认上限 50 ms | `components/egress/fastsandbox_actions.go` | 实验性 credential-bound 模式 |
| ingress WebSocket 握手预算 45 s、单消息上限 64 MiB | `docs/architecture/network/ingress.md` | RFC 6455 over HTTP/1.1 |
| egress Docker 停止预算 9 s | `docs/architecture/network/egress.md` | 删除时等待在途投递完成 |
| Kubernetes 支持版本范围 `v1.22` 至 `v1.34` | `manifests/charts/*/Chart.yaml` | `kubeVersion: ">=1.22.0-0"` |

## 仓库中观察到的命名与契约现状

1. **发布坐标沿用历史 `alibaba` 命名**：仓库虽位于 `opensandbox-group/OpenSandbox`，但 Go module 路径仍为 `github.com/alibaba/OpenSandbox/...`，npm 包名为 `@alibaba-group/opensandbox`，JVM 坐标为 `com.alibaba.opensandbox`。
2. **`/pty` 未覆盖于 `execd-api.yaml`**：实现完整但不在 OpenAPI 文件内。
3. **隔离会话 `diff`/`commit` 在契约里返回 `503`**：`/v1/isolated/capabilities` 如实回报不支持。
4. **`specs/` 文件后缀混用**：`sandbox-lifecycle.yml` 与 `diagnostic-api.yml` 用 `.yml`，`execd-api.yaml` 与 `egress-api.yaml` 用 `.yaml`。
5. **文档指南更名与废弃清理**：原 `docs/guides/network-isolation.md` 已被 `docs/guides/network-security.md` 取代；原 `server/opensandbox_server/api/devops.py` 已删除并全面收口至 `api/diagnostics.py`。

## OSEP 提案索引（28 份）

本快照 `oseps/` 目录共收录 28 份提案，正文重点引用了其中 17 份：

| 编号 | 主题 | 本书对应篇章 |
|---|---|---|
| 0004 | 安全容器运行时 | 第 15 篇 |
| 0005 | 客户端沙箱池 | 第 13 篇 |
| 0007 | Fast Sandbox 运行时支持 | 第 8 篇 |
| 0008 | 基于 rootfs 快照的暂停/恢复 | 第 7、13 篇 |
| 0009 | 访问即续期 | 第 10、13 篇 |
| 0011 | 安全 endpoint 访问 | 第 10、14 篇 |
| 0012 | Credential Vault | 第 12 篇 |
| 0013 | 隔离执行 API | 第 9、15 篇 |
| 0014 | 多租户 | 第 14 篇 |
| 0018 | `execd` 作为沙箱 init（`EXECD_INIT=1`） | 第 9、15 篇 |
| 0019 | 节点级沙箱采集（Node Agent） | 第 16 篇 |
| 0022 | 多沙箱共享 egress 控制面 | 第 8、11、12 篇 |
| 0023 | 凭据绑定的 TLS 拦截（实验性） | 第 11 篇 |
| 0024 | Fast Sandbox 空闲暂停与访问唤醒 | 第 10、13 篇 |
| 0025 | Credential Vault Provider SPI（Exchange） | 第 12 篇 |
| 0027 | 可选 E2B 兼容层（`components/e2b-shim`） | 第 2、18 篇 |
| 0028 | RootfsOnly 创建期 CPU/内存覆写 | 第 8、13 篇 |

其余 11 份涵盖 FQDN 出向控制（0001）、`agent-sandbox` 支持（0002）、卷与卷绑定（0003）、开发者控制台（0006）、OpenTelemetry 观测（0010）、Pod 快照（0015）、统一 umbrella 发布治理（0016）、SDK 传输韧性（0017）、生命周期钩子（0020）、可扩展客户端池预热（0021）以及 Fast Sandbox 资源超卖核算（0026）。
