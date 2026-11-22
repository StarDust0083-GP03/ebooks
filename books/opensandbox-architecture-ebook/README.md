# OpenSandbox 架构解剖：从一次创建请求到沙箱里的进程

基于 `opensandbox-group/OpenSandbox` 发布标签 **`release-1.3.0-rc.2`**（提交 `45043e7f0df258b6b76ef6753dd9b1819d59a332`，2026-11-19）的中文架构电子书。沿着一次 `POST /v1/sandboxes` 请求走完整条链路：控制面四条不变量、三种运行时后端、容器内 `execd-as-init`（`EXECD_INIT=1`）数据面、入向与出向网络、Credential Vault Exchange、暂停与 `full`/`rootfsOnly` 快照、多租户、分层隔离、可观测性，最后复盘这套设计的十项权衡。

全书包含 20 篇正文、3 份附录与 6 张手工排版架构图，每章均附固定到提交的源码入口表。全部内容基于静态阅读源码、OpenAPI 契约与官方文档独立撰写，未部署生产集群，未复现性能压测或开展安全审计；所有延迟数字均标注了上游出处与测试边界。

## 阅读与下载产物

![pages](https://github.com/StarDust0083-GP03/opensandbox-architecture-ebook/actions/workflows/pages.yml/badge.svg)

- **在线阅读站**：<https://StarDust0083-GP03.github.io/opensandbox-architecture-ebook/>
- `wiki/index.html`：分章目录、全文搜索、深浅主题与字号调整
- `wiki/book.html`：单文件离线合订本，内联 CSS/JS 与 6 张 SVG，零运行时外部请求
- `exports/opensandbox-architecture.pdf`：Chromium 无头打印版 PDF（构建时同步至 `wiki/exports/`）
- `exports/opensandbox-architecture.epub`：EPUB 3 电子书，内联 SVG 并声明 `properties="svg"`（同步至 `wiki/exports/`）
- `book/OpenSandbox架构解剖.md`：合并 Markdown 全书稿
- `book/SUMMARY.md`：分章目录索引
- `book/diagrams/`：6 张手工排版正交 SVG（由 `scripts/make_diagrams.py` 管理）与预览截图
- `sources/inventory.json` 与 `sources/UPSTREAM.md`：104 个固定源码与文档路径的 SHA-256 清单

## 篇章结构

| 篇章 | 核心内容 |
|---|---|
| 00 至 02 | 前言（含 v1.3.0-rc.2 演进导读）、六条需求、一条契约 / 三种后端 / 两个平面 |
| 03 至 05 | 四份 OpenAPI 契约边界、控制面四条不变量、`SandboxService` 与 `WorkloadProvider` SPI |
| 06 至 08 | Docker 单端口与续期锁、Kubernetes BatchSandbox `Completed` 终态与预热池、Fast Sandbox 与 Firecracker |
| 09 | `execd`：`EXECD_INIT=1` 默认拓扑、`opensandbox-launcher` 降权链、`CommandTimeout`、16 MiB 切分与磁盘指标 |
| 10 至 12 | endpoint 与 ingress、DNS + nftables + 共享 egress、Credential Vault Exchange（OSEP-0025） |
| 13 至 15 | 绝对过期、`full`/`rootfsOnly` 快照模式、多租户与 FSEP-0003、四层隔离与加固地板 |
| 16 至 19 | Diagnostics API 收口与 Node Agent、升级与运维红线、十项结构权衡、一次请求串联全书 |
| 附录 A 至 C | 源码阅读地图、来源清单与 28 份 OSEP 索引、配套标准库教学实现 |

## 运行配套教学模型

Python 3.10+ 零外部依赖，直接运行即可离线验证本书讨论的核心不变量（三后端路由、单宿主端口代理、Pool 缓冲与 `Completed` 终态、Docker 续期竞态锁、`rootfsOnly` 节点资质 fail-closed、`EXECD_INIT=1` 进程回收与信号盾、egress 尾点归一化、Vault Exchange 64 头上限）：

```bash
python3 examples/mini_osb.py
python3 -m unittest discover -s examples -v
```

当前包含 31 项离线单元测试，全部通过。该实现仅用于教学演示，不连接真实沙箱。

## 本地重建全部产物

需要 Node.js 22+、Python 3.10+ 与 Chromium（用于导出 PDF）：

```bash
bash scripts/build.sh
```

脚本会依次更新架构图、构建多页阅读站与离线合订本、打包 EPUB 3、导出打印版 PDF 并同步至 `wiki/exports/`、执行结构与链接校验（`wiki/verify.py`），并跑完 31 项教学单元测试。

本地预览阅读站：

```bash
python3 -m http.server 8000 --bind 127.0.0.1
# 浏览器打开 http://127.0.0.1:8000/wiki/
```

## 来源与许可

上游快照固定在 `release-1.3.0-rc.2`（`45043e7f0df258b6b76ef6753dd9b1819d59a332`）。上游代码采用 Apache-2.0 许可，文档采用 CC BY 4.0 许可（`CHARTER.md` 第 7.b.iv 条）。本书书稿与教学代码均独立撰写，仓库许可见 [LICENSE](LICENSE)。
