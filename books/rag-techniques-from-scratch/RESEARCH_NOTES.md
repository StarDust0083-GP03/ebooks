# 编写与核对记录

## 本轮目标

参考 NirDiamant/RAG_Techniques，写成中文 ebook。用户随后明确要求以本地 SAM 系列博客作为主要写作参考。因此停止旧的仪器预约式教材草稿，改为「开篇、需求 PRD、技术方案、逐模块实现、完结串联」的工程专栏结构。

旧草稿保留在本地 sources/drafts-v0/，不进入最终书稿与构建目录。

## SAM 写法参考

本地资料位于 learning_sam/，没有重新请求知识星球接口，也不复制文章全文或图片到新书。阅读样本包括：

- payment 的开篇词：从具体问题进入、说明范围，再安排模块。
- payment 的技术方案设计：需求映射到模块，比较候选方案与代价；阅读了代表段落，未做支付技术事实审计。
- payment 的完结篇：用一次完整请求回顾前文，并明确设计适用条件。
- goods 的商品读接口定义：调用方需求、方案对比、接口、DTO、实现与对应代码。
- ddd-practice 的从配货单表开始：用数据与操作边界解释概念，再落到类与目录。

这些材料只用于结构与表达参考。原作者叙述的生产经历、规模数据和架构结论不迁移为本书事实。详细来源索引见 sources/style-reference.md。

用户已有电子书用于目录、Markdown 书稿和阅读站组织参考：
- agent-memory-from-scratch，远端 main 核对提交 661e5bd6d8979e178bfc5178cc8d8594e9e4ae91。
- langchain-langgraph-from-scratch，远端 main 核对提交 5f5a5d8ba4078cc786864d18e590a2d68a4628eb。

## 技术来源

上游：https://github.com/NirDiamant/RAG_Techniques
固定提交：8e9febb8b974daaed2d4f3ebf370c7bf877858ac
核对日期：2026-10-27

核对 README、LICENSE、helper_functions.py、Notebook 清单；逐 cell 静态阅读 simple_rag、choose_chunk_size、fusion_retrieval、reranking、contextual_chunk_headers、HyPE、context_enrichment_window_around_chunk、self_rag、crag、light_rag、memorag 的代码。其他 Notebook 作为路径索引和概念参考，不声称逐一复现或完成源码审计。

完整 Notebook 清单、文件 SHA-256、cell 数量与对应章节存于 sources/notebooks.json。克隆与提取文本只留本地，不纳入分发内容。

## 需要保留的实现差异

1. README 的方法编号、42+ Notebook 宣传口径、实际文件数不是同一统计对象。
2. HyDE 生成假设文档；HyPE 在索引阶段生成问题向量并回映射原文。
3. fusion_retrieval 的 all_docs、BM25 原序与 vector_results 未按稳定 ID 显式对齐，存在数组位置错位风险。只报告静态观察，不报告未运行的远程结果。
4. contextual_chunk_headers 可见演示比较原块和加标题块的重排分数，不等于完整检索基准。
5. choose_chunk_size 同时改变 chunk_size 和 overlap，是配置组合实验。
6. self_rag 无相关上下文时仍会无检索生成。本书目标是内部规范问答，另行设定材料不足不作无依据结论。
7. Self-RAG 教程的提示调用不等于论文的反思 token 训练；MemoRAG 教程的主题键值不等于原论文 KV 压缩。
8. LightRAG 教程的无向 NetworkX 图、名称归一化和有限语料，不证明解决了方向、消歧和全量更新。
9. 上游自定义非商业许可，商业使用受许可材料须先取得书面授权，不整包转载上游 Notebook、图像或全文。

## 论文范围

直接读取了 8 篇 arXiv 标题与摘要：2005.11401、2212.10496、2310.11511、2401.15884、2401.18059、2404.16130、2410.05779、2409.05591。用于核对核心定位，没有阅读全文，不引用未核对的实验数字。摘要采集文件留本地 sources/papers/，不放进电子书。

## 本书独立实现

教学资料为原创虚构的支付规范、退款说明、对账说明与团队目录。Python 标准库实现 BM25、词频余弦、RRF、当前范围过滤、邻域与完整块打包。没有语义 embedding、模型回答、图抽取、OCR 或 Agent，正文明确区分实现与扩展方案。

## 质量检查

- 先固定目录与对应来源，再填写正文。
- 逐章检查业务条件、源码与论文差异，以及是否虚构第一人称经历。
- 运行离线单元测试、固定题集、构建与内部链接 / EPUB 结构校验。
- 模型质量未做远程测试；本地小题集不支持生产准确率和吞吐承诺。
- 书稿为首版，仍需用户线性通读审稿。不自动发布、不创建远端仓库、不推送。
