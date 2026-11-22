# 资料入库：先把文档读对，再谈检索

支付回调规范里有一张两列表格，左列写事件类型，右列写确认规则。普通 PDF 文本抽取器按底层字符流顺序扫描，先一口气把所有左列读完，再把所有右列拼在后面。导出来的纯文本既没乱码、字数也完全对得上，可事件和处理规则已经彻底错位。源头一旦读歪，后面的 embedding、重排器和提示词越强，反而把错误结论包装得越像真话。

## 解析验收别只盯着字数和乱码

入库流水线最先要验的，是解析器到底有没有把人眼看到的版面语义保留下来。挑出最难啃的代表文档（双栏排版、合并单元格、跨页表格、扫描件、带脚注条款），逐项核对标题层级、阅读顺序、表头对应、数值单位和否定词是否完整。比如规范里的“重复通知不重复发货”，丢一个“不”字，整条规则就翻了面。

验证解析质量时，先别急着把大模型接上去看最终回答。模型有时候会凭训练时的支付常识蒙对答案，恰好掩盖了底层的解析错乱。正确的验收动作是直接打印解析后的原始块文本，对照原 PDF 页面肉眼或脚本核对，同时把页码、块编号和原文版本一起落盘，方便用户点击引用直接跳回原页。

## Mistral OCR 4.1 给出的三段对照教训

上游仓库在 `2fd7bd0f` 提交中把 `all_rag_techniques/document_parsing_with_mistral_ocr.ipynb` 升级到了 Mistral OCR 4.1（上游 README 标注由 Mistral AI 赞助，并随仓库附带了 `two_column_paper.json`、`scanned_page.json` 和 `erp_table_b1.json` 三份预计算结果，方便无密钥离线复现）。这份 Notebook 最有价值的启发不在某家模型的跑分，而在于它把同一条流水线跑了三遍对照：

1. 默认 `PyPDF`（仓库 `encode_pdf()`）抽取 PDF 后，表格结构在建索引前就已被破坏，后面再怎么重排也救不回来。
2. 原封不动保留切分器、向量库、检索器和提示词，只把解析器换成 Mistral OCR 4.1，表格内容确实恢复成了干净的 Markdown 表格，但固定字符切分器紧接着又把表格和紧挨着它的表题（caption）切在了两个块里，答案照样丢失。
3. 第三遍利用 OCR 4.1 返回的页面 `blocks` 结构标签做块感知切分，把每张表当作原子块并与表题绑在一起，检索与重排才真正拿到完整证据。

这个三步对照点破了一个常被忽略的事实：解析和切分是紧密耦合的上下游。只升级解析器却沿用粗暴的字符切分，等于刚把桌子拼好又一斧子劈开。第 4 章我们会用配套代码里的 `block_aware_split()` 直接复现这套结构保护逻辑。

## 文档 ID 与片段 ID 必须分层管理

同一份回调规范有 `v1` 和 `v2` 两版，它们的 `doc_id` 都是 `callback`，版本号不同，第一个片段的全局标识分别是 `callback-v1:0` 和 `callback-v2:0`。不要只用正文内容哈希充当唯一主键：不同部门的两份规范可能复制粘贴了同一段免责声明，它们的权限范围、生效时间和归属文档完全不同，合并成一条记录会直接搞乱授权与引用。

配套实现在构造 `Chunk` 和初始化 `Retriever` 时就把守两道入口校验：必填字符串（`id`、`doc_id`、`version`、`text`、`source`、`scope`）全部不能为空，全库不允许出现重复 `id`，同一文档同一版本下也不允许出现重复 `position`。来源字段为空虽然不影响算 BM25 分数，却会让最终引用失去核对价值，所以必须在入库边界直接拦下。

## CSV 和 JSON 别丢掉字段语义

企业知识库除了 PDF 和 Markdown，还混着大量渠道配置表（CSV）和接口描述（JSON）。把一行 CSV 直接拼成 `"DEMO_PAY 异步 CNY"`，脱离表头后连人都看不懂，更别说检索器。入库序列化时应显式带上键名与语义角色，写成 `"渠道：DEMO_PAY；回调方式：异步；结算币种：CNY"`；遇到日期字段要写明是生效日期还是废弃日期；嵌套 JSON 拆块时要把父级模块名和记录主键一并带进子块文本。

与此同时要看清检索的边界：像“统计当前所有已启用人民币渠道有多少个”这种全集聚合题，不能靠向量检索取 top-k 去数。检索器天生只返回局部相关片段，没法证明全集只有这几条。这类问题应当走授权后的结构化 SQL 或业务 API 查询，模型只负责把查询结果讲清楚。

## 版本切换与派生数据的连锁刷新

把规范从 v1 升到 v2，稳妥的发布顺序是：把 v2 原文、切分块、向量和各类派生索引全部写进新版本槽位，跑完完整性检查再原子切换活动版本指针，最后清理旧版本问答缓存。千万不要先停用 v1 再慢悠悠重建 v2，中途任何网络抖动都会让线上进入空库或半成品状态。

更隐蔽的坑在派生数据。只要某段原文改了一个字，基于它算出来的稠密向量、HyPE 问题代理、章节摘要、知识图谱边以及命中缓存全部可能过期，原页码和字符偏移也会跟着挪位。给每一份派生记录保存父片段 `id`、原文版本和生成配置版本，后续更新或删文档才能按血缘精准重建与清理，不用靠人工猜哪些缓存该清。

## 本章对应代码与资料

- [教学语料快照](../../examples/documents.jsonl)
- [入库校验与块感知切分](../../examples/mini_rag.py)
- [上游 Mistral OCR 4.1 对照](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/document_parsing_with_mistral_ocr.ipynb)
- [上游 CSV RAG（LangChain 版）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_csv_rag.ipynb)
- [上游 CSV RAG（LlamaIndex 版）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_csv_rag_with_llamaindex.ipynb)
- [上游 JSON RAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/json_rag.ipynb)
