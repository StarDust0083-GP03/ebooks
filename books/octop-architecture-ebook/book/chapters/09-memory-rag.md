# 记忆与知识库：命名空间维护、图谱扩展与重排序

用户上周在对话里交代过一句“以后周报默认用中文表格”，团队又往系统里上传了三十份产品规范 PDF。两者都会在回答问题时被召回进上下文，但工程上绝不能塞进同一张表：前者是随对话演化的个人偏好与事实提炼，允许用户手工编辑用户档案和强记忆、遇到冲突弹窗确认；后者是有明确文件夹层级、版本号和用户组可见权限的文档证据，需要解析、切片、建索引并带出处引用。

Octop 把这两条检索路径彻底分开：长期记忆交给独立运行库 `octop-memory`（`>=1.0.5`），文档知识库留在宿主 `src/octop/infra/knowledge/` 下，并在 `v1.0.3b3` 到 `v1.0.3b6` 连续补齐了 `pgvector` 存储、二阶段重排序和实体图谱检索。

## Memory 后端解析与 PostgreSQL 命名空间瘦身

`src/octop/infra/agents/memory/backend.py` 的 `open_memory_kwargs()` 负责把专家配置翻译成 `octop-memory` 需要的 `(namespace, backend, backend_config)`。默认策略紧跟控制面驱动：

- SQLite 控制面下，每个专家把记忆和 checkpoint 放在自己系统目录下的 `memory.sqlite`，命名空间按 `agent_<agent_id>` 规整化。
- PostgreSQL 控制面下，除非该专家显式覆盖为本地 SQLite，否则默认复用同一条 PostgreSQL DSN，所有专家共享 `octop_memory` schema 下的 11 张表（`raw_events`、`candidates`、`atoms`、`entities`、`aliases`、`entity_pages`、`memory_nodes`、`thread_active_entities`、`journal`、`episodes`、`digests`），严格靠 `namespace` 列隔离。

有两点很容易在运维时踩坑。第一，记忆提炼使用的辅助抽取模型可以与日常聊天模型分开配置，因此聊天正常但长期记忆停止生长时，要单查抽取模型的可用性与运行开关（`v1.0.3b5` 专门修了捕获和召回运行开关被覆盖的问题，`v1.0.3b3` 升级 `octop-memory` 后长会话改为分批提炼且重启不重跑）。第二，前端快速切换专家查记忆时，旧请求可能比新请求晚返回，`v1.0.3b7` 在界面侧加上了过期响应丢弃，防止旧专家的记忆列表把当前页面盖掉。

共享 PostgreSQL 上做记忆空间整理最怕锁死整张表。第 6 章提到的 `infra/agents/memory/postgres_storage.py` 设计了一套边界极严的在线维护协议：

1. 巡检阶段开 `REPEATABLE READ, READ ONLY` 事务，设 15 秒语句超时与 1 秒锁超时，按命名空间精确统计 11 张表的行数和列字节大小。
2. 清理阶段先抢占按命名空间哈希的事务级咨询锁 `pg_try_advisory_xact_lock`，把并发触发的维护任务折叠掉。
3. 每次只挑最多 500 行超过 30 天且无原子引用的已拒绝候选，以及超过 14 天的 6 类流水线日志，加 `FOR UPDATE OF t SKIP LOCKED` 避开在途写入，排除衍生全文检索列 `fts_tsv`，写完备份 JSONL 并 `fsync` 之后才执行精准 `DELETE`，全程不动活跃记忆、决策日志、checkpoint，也绝不跑锁表的 `VACUUM FULL`。

## 知识库的双存储平面：SQLite Sidecar 与 PGVector

`src/octop/infra/knowledge/plane.py` 根据控制面驱动动态决定打开哪一套索引实现：

- **SQLite 模式（`index.py`）**：每个知识库对应一个本地 `index.sqlite` sidecar，文档切片文本、元数据和二进制 embedding 存在 `chunks` 表中，`replace_doc_chunks()` 在单库事务里先删旧片再插新片，查询时在进程内扫描计算余弦相似度。这种设计零外部依赖，适合个人和小团队，数据量扩大时需要盯着内存与扫描延迟。
- **PostgreSQL 模式（`pg_index.py`，ADR 003）**：知识库原文进 PG 文件表，切片和向量进 `pgvector` 支撑的 `knowledge_chunks` 表，支持多副本同时读写和共享。文档解析本身支持本地 OCR（完整镜像内置 `rapidocr` + `pymupdf`）、`firecrawl-anydoc` 转换、以及本地 ONNX 嵌入（`fastembed`）或远程供应商嵌入模型（`v1.0.3b3` 修复了远程批量 embedding 返回乱序的缺陷）。此外，`v1.0.3b5` 支持在知识库文件夹之间移动文档和子目录。

## v1.0.3b6 的三段检索管线：向量召回、二阶段重排序与实体图谱

翻开 `src/octop/infra/knowledge/retrieve.py` 的 `_retrieve_context_sync()`，能看到 `v1.0.3b7` 的知识库检索已经进化成一条带权限前置和双路增强的清晰管线：

```text
校验知识能力与 query → 生成 query embedding
  → 取当前主体可见知识库集合（管理员全量，普通用户走 list_visible）
  → 与本轮选中的 knowledge_base_ids 取交集
  → 仅在 status == "ready" 且非目录的文档范围内检索：
      1. 向量召回：开重排序时扩召 candidate_count(k)（4*k，夹在 20~50），否则取 k=8
      2. 实体图谱扩展（可选）：open_knowledge_graph() 查关联实体事实与相关片段
  → 按相似度初排；若启用重排序，调供应商 /rerank 接口精排
  → _with_graph_passages() 按配额混入图谱关联段落（最多占 k/4）
  → 在 char_budget=6000 字符预算内格式化正文、图谱事实（最多占预算 1/5）与引用标记
```

这里有三个设计细节非常值得拆出来看：

1. **权限过滤一定在召回之前**：先算 `visible_by_id` 再查对应知识库的索引，绝不把所有知识库混搜一遍再指望提示词让模型别引用无权文档；同时只认 `status == "ready"` 的文档，避免解析失败或索引半成品污染回答。
2. **重排序（`rerank.py`）的候选扩张与优雅降级**：向量检索偏召回，容易把字面相似但答非所问的段落排前面。开启重排序后，系统先按 `max(k, min(max(k * 4, 20), 50))` 多捞一批候选，再发往配置好的供应商 `/rerank` 接口（兼容 Jina、Cohere、SiliconFlow、vLLM、Xinference 和 TEI 格式）让精排模型同时看问题和段落打分。如果重排序供应商超时或报错，调用方记日志并直接退回向量原序，不拖垮整轮对话。
3. **实体图谱（`graph.py`、`graph_extract.py`、`graph_store.py`）作为增量通道**：图谱查询包在独立的 `_expand()` 捕获块里，挂了就退化为纯向量检索；成功时用固定比例（段落不超过 `k/4`，事实文本不超过字符预算的 `1/5`）把跨文档实体关联补进上下文，防止图谱事实把原始证据挤出窗口。

最后要留意一个有意为之的取舍：外层的 `retrieve_context()` 把整个同步检索丢进线程池执行，遇到任何未捕获异常都会记一条警告日志并返回空字符串。这样做的好处是嵌入模型欠费或网络抖动时，聊天不会直接崩溃；代价是模型会失去私有知识证据，退化成靠通用训练数据作答。如果你的业务场景要求严禁脱稿回答，需要在产品侧把检索失败显式透出，而不是默认 fail-open。

## 源码入口

- [`src/octop/infra/agents/memory/backend.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/backend.py)
- [`src/octop/infra/agents/memory/postgres_storage.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/agents/memory/postgres_storage.py)
- [`src/octop/infra/knowledge/index.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/index.py)
- [`src/octop/infra/knowledge/pg_index.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/pg_index.py)
- [`src/octop/infra/knowledge/retrieve.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/retrieve.py)
- [`src/octop/infra/knowledge/rerank.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/rerank.py)
- [`src/octop/infra/knowledge/graph.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/graph.py)
- [`src/octop/infra/knowledge/jobs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/knowledge/jobs.py)
