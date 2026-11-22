# 存储与历史：六类数据平面、下线归档与安全清理

刷新页面后少了一段回复，或者删完一位旧专家磁盘空间一点没降，排查这类问题时最怕顺手找一个所谓的唯一数据库。在 Octop 里，数据按读写模式和一致性需求拆成了六个平面，而且在单机 SQLite 模式和集群 PostgreSQL 模式下的落盘位置差异很大。

![SQLite 与 PostgreSQL 六类数据平面分布](../../wiki/assets/diagrams/storage-planes.svg)

## 为什么必须把图状态和展示历史拆开

初学者常把 LangGraph 的 checkpoint 直接当成聊天记录库，跑两周就会踩进两个坑。第一个坑是上下文压缩：长对话触发 `/compact` 或自动裁剪后，checkpoint 里保留的是压缩摘要和后续图状态，如果前端直接读 checkpoint，用户会发现昨天的原始问答凭空蒸发了。第二个坑是查询形态：前端需要稳定分页、说话人标记、工具调用折叠卡片、本轮改过的文件增删行数（`v1.0.3b6` 新增）、错误状态和耗时轨迹，而 checkpoint 保存的是面向图恢复的序列化快照。

所以 Octop 把这两件事拆得清清楚楚：checkpoint 只管运行图恢复和 HITL 断点续跑；控制面里的 `thread_messages` 和 `trajectory_events` 表负责面向前端和审计的展示投影（由 `infra/history/projection.py` 维护）；长期提炼出的用户画像和事实记忆归 `octop-memory`；上传的文档切片和向量归知识库索引；`SOUL.md`、技能和生成产物归 `BackendWorkspace`。

## SqlitePool 与 PostgresPool 的克制封装

控制面的连接池封装在 `src/octop/infra/db/pool.py`。两个实现共用同一套 `DatabasePool` 协议（`connect`、`transaction`、`close`）和成对编号的迁移脚本（`NNN_*.sql` 与 `NNN_*.pg.sql`），但底层各有各的工程细节。

`SqlitePool` 用一把 `threading.RLock` 保护进程内唯一连接，建库时开启 `PRAGMA foreign_keys = ON` 和 `PRAGMA journal_mode = WAL`，POSIX 下把数据库文件权限设成 `0600`。这里有两处非常真实的生产踩坑记录直接写在了代码里：

1. **事务固定用 `BEGIN IMMEDIATE` 而非延迟 `BEGIN`**，如果用默认的延迟事务，先读后写时才去抢写锁，一旦另一条连接（比如 CLI 命令或多个 worker）在中间提交了事务，SQLite 会立刻抛 `SQLITE_BUSY_SNAPSHOT`，连 5 秒的 `busy_timeout` 都救不了。提前拿写锁让竞争者乖乖排队，把快照冲突扼杀在入口。
2. **Windows 下改为 `PRAGMA synchronous = NORMAL`（`v1.0.3b3` 修复）**，WAL 配合默认的 `FULL` 会在每条自动提交语句上调用 Windows 的 `FlushFileBuffers`，把应用和测试拖成龟速。只在 `os.name == "nt"` 时降为 SQLite 官方推荐的 WAL 设置 `NORMAL`，应用崩溃不损坏库文件，其他平台继续保持 `FULL`。

`PostgresPool` 则包了一层 `_PgConnectionProxy`，暴露与 SQLite 相似的 `execute`、`executemany`（`v1.0.3b3` 补齐适配）和 `_CompatRow`（同时支持列名和下标取值），并用简单的 `qmark_to_pyformat()` 把 `?` 占位符替换成 `%s`。源码明确注明它不解析 SQL 字符串字面量，只服务于受控的仓储 SQL，不伪装成通用方言翻译器。

## ADR 003：PostgreSQL 接管技能包与知识库

在 `v1.0.3b1` 时期，哪怕你配了 PostgreSQL 控制面，技能包源文件和知识库索引依然死绑在 `~/.octop/skill-packages/` 和本地 `index.sqlite` 上。`v1.0.3b3` 落地 `docs/adr/003-skill-package-kb-pg-storage.md` 后，局面彻底变了：控制面驱动选定后，内容平面自动绑定，不再提供控制面用 PG 但知识库留本地的分叉配置。

| 控制面驱动 | 技能包源文件 | 知识库原文 | 知识库向量 |
| --- | --- | --- | --- |
| `sqlite` | 本地 `skill-packages/` | 本地 `knowledge/<id>/docs/` | 本地 `index.sqlite` |
| `postgresql` | PG 表 `skill_package_files` | PG 表 `knowledge_files` | 同库 `pgvector`（`knowledge_chunks`） |

ADR 003 做了一个很克制的选择：不造新的二进制打包协议，直接复用 `octop-harness` 的 `PostgresBackend` 形态（路径一行一文件、`path` + JSONB 内容），但分表与专家工作区隔离；技能包绑定到专家时，依然保持从 Store 复制进专家工作区的既有行为，不强迫专家改成复杂的 composite 挂载。另外，按 ADR 002 的约定，Octop 建表迁移绝不私自执行 `CREATE EXTENSION vector`，`pgvector` 扩展由部署时的初始化脚本或 DBA 提前装好。

需要注意，至今仍没有官方的 SQLite 到 PostgreSQL 一键数据迁移工具。单机转多副本集群时，需要从一套全新的 PostgreSQL 实例起步。

## 为什么下线 history_v2

上一版架构书曾花大篇幅介绍过默认关闭的分段历史库 `history_v2.sqlite`。它的本意是通过写入前提交保证流式历史不丢，但在产品演进中暴露出两个硬伤：它只支持 SQLite 控制面，和 PostgreSQL 多副本路线天然冲突；同时多出一套独立 SQLite 文件，让备份、在线恢复、跨库一致性和历史分页变得格外沉重。

因此在 `v1.0.3b5` 中，Octop 果断下线了 `history_v2`，删除了旧的 `history/service.py` 和 `history/recorder.py`，只留下 `projection.py`（消息投影）和 `retire_v2.py`（启动时单次迁回 `thread_messages`）。敢于把一个已经上线的复杂实验特性彻底收口退场，比硬撑着维护两套历史引擎健康得多。

## v1.0.3b7 关键修复：PostgreSQL 孤儿 checkpoint 绝不误删

控制面 `threads` 行和 checkpoint 处于不同存储层，异常崩溃或老版本缺陷可能留下没有对应 `threads` 行的孤儿 checkpoint。命令行 `octop chats gc-orphans` 就是干这件清理活的。

但在 PostgreSQL 模式下，这里藏过一个极其凶险的边界：`octop_memory` 的 PostgreSQL checkpoint 表是**同 DSN 下所有智能体、甚至可能包含多台宿主实例共享的一张大表**，而 LangGraph 原生 checkpoint 表结构本身并不带强归属外键（Octop 只能靠写入 checkpoint `metadata->>'agent_id'` 去反推归属）。如果某条记录缺标签、或者同库里别的智能体正挂着某个 `thread_id`，按单个专家的线程表一刀切去删，就会把其他智能体的正常对话直接清空。

`v1.0.3b7` 的 `src/octop/infra/agents/memory/thread_cleanup.py`（PR #2960）把这条风险焊死了：

```python
if backend == "postgres":
    if dsn != control_dsn:
        skipped.append(SkippedCheckpoints(row.agent_id, "external_database", None, 0))
        continue
    if live_threads is None:
        live_threads = _all_live_threads(services)
    usage = _postgres_thread_bytes(dsn, row.agent_id)
    kept = [nbytes for tid, nbytes in usage.items() if tid not in live_threads]
    if kept:
        skipped.append(
            SkippedCheckpoints(row.agent_id, "postgres_report_only", len(kept), sum(kept))
        )
    continue
```

读懂这十几行就能抓住它的三条防护逻辑：

- 如果该专家的记忆 DSN 和当前控制面不是同一个库，归为 `external_database` 直接跳过不扫描。
- 即使在同一个控制面 PostgreSQL 上，先汇总**全库所有专家**的活跃线程集合 `_all_live_threads()`，绝不只拿单个专家的线程表做差集。
- 最关键的是，就算传入了 `--yes`（`apply=True`），PostgreSQL 分支也永远只把统计出的孤儿线程数和字节数记入 `skipped`（原因 `postgres_report_only`），**绝不执行 DELETE**；只有独占文件的 SQLite 孤儿才会在 `apply=True` 时走 `delete_stored_thread()` 清理，并在空间充足时触发有界增量 vacuum（`_DELETE_VACUUM_PAGES = 2000`，整库 `VACUUM` 前还会检查磁盘剩余空间至少比库文件大 64MB）。

同时，针对 PostgreSQL 记忆表的日常瘦身，`src/octop/infra/agents/memory/postgres_storage.py` 走的是另一条安全路径：用 `pg_try_advisory_xact_lock` 按命名空间互斥，用 `FOR UPDATE OF t SKIP LOCKED` 每次最多锁 500 行超过 30 天的已拒绝候选或超过 14 天的流水线日志，**先把精确的待删行刷盘备份成 `0600` 权限的 JSONL 并 `fsync`**，再在同一事务里按 `namespace + id` 精准删除，绝不对共享表执行 `VACUUM FULL` 或触碰 checkpoint 与活跃记忆。

## 源码入口

- [`docs/adr/002-database-backends.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/002-database-backends.md)
- [`docs/adr/003-skill-package-kb-pg-storage.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/adr/003-skill-package-kb-pg-storage.md)
- [`src/octop/infra/db/pool.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/pool.py)
- [`src/octop/infra/history/projection.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/history/projection.py)
- [`src/octop/infra/history/retire_v2.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/history/retire_v2.py)
- [`src/octop/infra/agents/memory/thread_cleanup.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/thread_cleanup.py)
- [`src/octop/infra/agents/memory/postgres_storage.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/postgres_storage.py)
- [`src/octop/infra/db/migrations/001_initial.sql`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/db/migrations/001_initial.sql)
