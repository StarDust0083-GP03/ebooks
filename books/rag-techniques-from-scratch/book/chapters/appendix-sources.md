# 附录 B：源码快照、论文范围、术语与研究边界

## 上游源码快照与增量审计

- 上游仓库：[NirDiamant/RAG_Techniques](https://github.com/NirDiamant/RAG_Techniques)
- 固定提交：`2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6`
- 上游提交时间：2026-11-02
- 本地核对日期：2026-11-22
- 上游许可：[Custom License Agreement](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/LICENSE)（非商业使用并要求署名，商业使用受许可材料需原作者事先书面授权）

本书正文、架构图与零依赖 Python 教学实现均独立编写，仅保留上游路径索引与静态源码分析，不分发上游源码全包、Notebook 输出或图片，也不对第三方材料重新授权。

相比上一核对快照（`8e9febb8`，2026-10-27），`2fd7bd0f` 引入的关键变化已并入对应章节：

| 上游变更 | 涉及文件 | 本书落点 |
| --- | --- | --- |
| Mistral OCR 4 升级至 OCR 4.1（#214） | `all_rag_techniques/document_parsing_with_mistral_ocr.ipynb` | 第 3 章、第 4 章 |
| LangChain 1.x 与 LlamaIndex 0.14 迁移（#212） | 全仓 Notebook、`helper_functions.py`、21 份脚本 | 第 5 章、第 6 章、第 14 章 |
| 停服模型升级与 DeepEval `GEval` logprobs 行为（#211） | 33 处 `gpt-5.4-mini`、6 处 `gpt-5.4`、`evaluation/evaluation_deep_eval.ipynb` | 第 5 章、第 9 章、第 14 章 |
| HyPE 逗号语法修复与无密钥 CI 烟雾检查（#211、#209） | `HyPE_Hypothetical_Prompt_Embeddings.ipynb`、`.github/workflows/notebook-smoke.yml` | 第 7 章、第 9 章 |

静态逐 cell 重点核对的 Notebook 包括：Simple RAG（LangChain / LlamaIndex）、Document Parsing with Mistral OCR 4.1、Choose Chunk Size、Fusion Retrieval（LangChain / LlamaIndex）、Reranking、Contextual Chunk Headers、HyPE、Context Enrichment Window、Self-RAG、CRAG、LightRAG、MemoRAG、DeepEval，以及 `helper_functions.py`、`tests/` 和 `.github/workflows/`。其余 Notebook 作为路径索引和概念对照。

## 原始论文核对范围

下列 8 篇论文直接核对了 arXiv 标题与摘要，用以校验核心定位与方法边界，不引用未复现的论文实验榜单数值：

| 主题 | 原始论文 | 本书参考边界 |
| --- | --- | --- |
| RAG | [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401) | 参数知识与外部非参数检索协同定位 |
| HyDE | [Precise Zero-Shot Dense Retrieval without Relevance Labels](https://arxiv.org/abs/2212.10496) | 查询时假设文档向量检索原文 |
| Self-RAG | [Learning to Retrieve, Generate, and Critique through Self-Reflection](https://arxiv.org/abs/2310.11511) | 按需检索与微调反思 token，区别于多轮提示演示 |
| CRAG | [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884) | 检索质量三态评估与纠错分流 |
| RAPTOR | [Recursive Abstractive Processing for Tree-Organized Retrieval](https://arxiv.org/abs/2401.18059) | 递归聚类摘要树与跨层检索 |
| GraphRAG | [From Local to Global](https://arxiv.org/abs/2404.16130) | 实体图、社区摘要与全语料全局问答 |
| LightRAG | [Simple and Fast Retrieval-Augmented Generation](https://arxiv.org/abs/2410.05779) | 低层具体实体与高层主题双层检索 |
| MemoRAG | [Global Memory-Enhanced Retrieval Augmentation](https://arxiv.org/abs/2409.05591) | 全局记忆生成检索线索回溯原文 |

## 核心术语对照表

| 术语 | 本书口径 | 常见混淆点 |
| --- | --- | --- |
| Chunk | 带全局 ID、版本、位置与来源的检索单元 | 整篇文档或检索名次 |
| Embedding | 稠密编码模型输出的连续语义向量 | 本书离线实验中的词频向量 |
| BM25 | 综合词频饱和、逆文档频率与长度归一的词面评分 | 简单子串匹配 |
| RRF | 按稳定文档 ID 汇总多路名次倒数 `1 / (c + rank)` | 原始分数归一化加权 |
| Reranking | 对有限候选池做精细二次打分重排 | 从全库补回漏召回片段 |
| Grounding | 生成结论与实际注入原文之间存在可验证支持关系 | 答案读起来通顺合理 |
| Citation | 指向 `injected_ids` 内具体片段的可核对编号 | 编号存在即代表推论正确 |
| HyDE | 查询时生成假设文档供向量召回 | 将假设文本直接当作证据 |
| HyPE | 入库时为片段预生成代理问题并回映射父片段 ID | 查询时直接生成回答 |
| CRAG | 按检索质量走直出、受控重试或拒答的三态纠错路由 | 无条件上网搜索补齐内部规范 |
| ACL | 召回前及所有旁路必须执行的访问控制边界 | 靠相关性分数或提示词过滤 |
| Recall / Complete | 必要证据的命中比例 / 全部必要证据同时交付的布尔率 | 只看首位命中的 MRR |

## 实验口径与研究边界

全书配套语料（`examples/documents.jsonl`）、6 题评估脚本（`examples/evaluate.py`）和 20 项单元测试（`examples/test_mini_rag.py`）均为本地零依赖教学实现，运行过程不调用远程大语言模型、语义 embedding 服务、OCR 接口或外部图数据库。书中手写的示范回答均已显式标明，静态代码观察、离线实测结果与生产架构建议互不混写。本地小题集与单元测试用于验证系统不变量和排障方法，不构成真实生产环境的准确率、延迟或安全认证承诺。
