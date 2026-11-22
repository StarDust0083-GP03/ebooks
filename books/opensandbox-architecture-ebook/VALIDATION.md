# 验证记录（release-1.3.0-rc.2 同步版）

## 上游同步核对

- **前版快照**：`c2f55b20e945d89efe3d35220942a997a3551751`（2026-10-30）
- **当前快照**：`release-1.3.0-rc.2`，commit `45043e7f0df258b6b76ef6753dd9b1819d59a332`（2026-11-19）
- **来源清单**：`sources/inventory.json` 共收录 104 个核对路径与 SHA-256 哈希；已移除上游删除的 `server/opensandbox_server/api/devops.py` 与 `docs/guides/network-isolation.md`，补入 `api/diagnostics.py`、`docs/guides/privilege-reduction.md`、`docs/guides/network-security.md`、`components/execd/pkg/runtime/initmode_linux.go`、`hardening_linux.go`、`services/fast_sandbox/snapshot_runtime.py`、`services/k8s/workload_provider.py` 与新增 OSEP 提案。

## 图表视觉与结构核查

全书共 6 张手工正交 SVG，其中 2 张沿用已验原图，1 张按 `v1.3.0-rc.2` 更新，3 张为本轮新增并全部通过 Chromium 无头截图逐张目视审查：

| 图表文件 | viewBox | 节点块数 | 文本数 | 涵盖主题 |
|---|---|---:|---:|---|
| `architecture.svg` | 1160×980 | 8 | 35 | 控制面、访问路径、三后端与旁路观测 |
| `request-flow.svg` | 1080×980 | 4 | 20 | BatchSandbox 池化创建到执行成功路径 |
| `lifecycle.svg` | 1060×980 | 7 | 29 | BatchSandbox 粘滞 `Completed` 终态与可重试暂停循环 |
| `execd-init.svg` | 1100×860 | 15 | 39 | OSEP-0018 `EXECD_INIT=1` 对比与 `opensandbox-launcher` 降权链 |
| `snapshot-modes.svg` | 1100×840 | 8 | 39 | `full` 与 `rootfsOnly` 快照准入、S3/OCI 覆盖与恢复行为 |
| `egress-vault.svg` | 1100×860 | 9 | 43 | DNS/nftables、平台优先级、Vault Exchange 与 FSB Subject 状态机 |

目视审阅阶段在 `egress-vault.svg` 中检出两处文本宽度过紧（规则框文本贴近右侧 Exchange 框、状态机 `Hook 等 <=50 ms 确认` 间距偏窄），已放宽框距、精简措辞并重新渲染复查通过，零重叠、零裁切。

## 构建、校验与测试结果

完整执行以下门禁命令：

```bash
bash scripts/build.sh
```

验收项全部通过：

1. **多页站与离线合订本**：26 个 HTML 页面（首页、搜索页、离线全书 `wiki/book.html`、23 个章节与附录页），零重复 ID，所有内部相对链接与锚点可达；`book.html` 零外部脚本，6 张 base64 SVG 与 `book/diagrams/*.svg` 字节一致且 `url(#...)` marker 引用完整。
2. **EPUB 3 导出**：`exports/opensandbox-architecture.epub`（并同步至 `wiki/exports/`）包含 23 个 spine 章节，4 个含内联 `<svg xmlns="http://www.w3.org/2000/svg">` 的章节全部在 `OEBPS/content.opf` 中声明 `properties="svg"`，所有 XHTML 与 OPF 通过 XML 合法性解析。
3. **PDF 打印版**：`exports/opensandbox-architecture.pdf`（并同步至 `wiki/exports/`）由无头 Chromium 直接渲染 `wiki/book.html` 生成，文件完整非空。
4. **中文标点与零破折号门禁**：对全部 22 篇手写章节、`README.md` 及 `VALIDATION.md` 运行 `check-punctuation.sh --lang zh` 与零破折号扫描，全部返回 `punctuation: ok`，正文中零 em-dash（U+2014）和零 en-dash（U+2013）。
5. **教学模型单元测试**：`examples/mini_osb.py` 演示脚本运行正常，31 项 `unittest` 离线测试全部通过。

## 边界说明

本验证覆盖构建产物完整性、链接与锚点、XML/EPUB 结构、图表渲染、标点规范与教学模型单元测试；不构成真实 OpenSandbox 集群部署测试、性能压测或官方 epubcheck 认证。
