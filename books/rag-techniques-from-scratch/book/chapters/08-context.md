# 证据组装：检索到了，还要让模型完整读到

排障时经常遇到一种让人摸不着头脑的现象：翻开检索日志，验签片段排第一，幂等片段排第二，召回和排序全对；可最后模型给出来的回答还是漏掉了幂等。顺着链路往后查一步才发现，组装上下文的预算太紧，放完第一条加上来源标签就快满额，第二条直接被静默丢弃了。检索做对了，交付环节把证据截断了，用户看到的照样是错误答案。

## 邻域扩展必须锁死文档、版本与权限

第 4 章把块切小是为了召回精准，到了组装阶段再把相邻语境补回来。执行下面这条命令就能看到效果：

```bash
python3 examples/mini_rag.py \
  --query "回调处理顺序" --mode bm25 --k 1 --window 1
```

只取 top-1 候选时，BM25 只召回了 `callback-v2:0`；加上 `--window 1` 后，`Retriever.expand()` 以命中块为中心，在同一个 `doc_id`、同一个 `version`、且再次通过 `visible(user)` 校验的集合里拉出 `abs(position - center.position) <= 1` 的邻段，把 `callback-v2:1` 顺带补齐，并按原文 `position` 顺序排好、按 ID 去重。

这套边界挡住了三类常见事故：不会把退款规范里的 `position=1` 拼到回调规范后面；不会把已废弃的 `callback-v1:1` 混进 v2 窗口；也不会把紧挨着公开段落的 Bob 私人笔记带进 Alice 的上下文。到了生产存储层，查找邻块应当走文档主键加位置范围查询（比如数据库复合索引或键值存储），不要为了取前后两段再对向量库扫一遍。

## 固定窗口与 RSE 的取舍

固定半径窗口逻辑直观，缺点是不管前后段跟当前问题有没有关系都会一起带进来。如果一段回调规范后面紧跟着一段毫不相干的商户入驻说明，`radius=1` 就会塞进噪声。

相关片段提取（Relevant Segment Extraction，上游 `relevant_segment_extraction.ipynb`）换了种拼装方式：根据各块的相关性得分分布，动态合并得分高的连续区段，跳过低分段落。它能减少无关背景，但需要调片段得分阈值与连续区段惩罚参数，也要多一套回归用例。无论用固定窗口还是 RSE，都别把“物理相邻”当成“逻辑同条件”：规范里前后紧挨着的两节，完全可能写着国内渠道和海外渠道两套相反的结算规则，保留清晰的章节标题边界最保险。

## 上下文压缩最怕误删限定词与否定词

文档太长、上下文塞不下时，上下文压缩（Contextual Compression，上游 `contextual_compression.ipynb` 在 LangChain 1.x 中使用 `langchain_classic.retrievers.ContextualCompressionRetriever` 和 `LLMChainExtractor`）会先让模型挑出与问题相关的句子或做一次摘要。

压缩规范类文本风险很高。原文写着“先验签，再核对商户号、订单号、金额和币种，核对成功后才更新支付状态”，压缩器如果为了精简改成“验签后更新支付状态”，长度省了一半，业务条件却被砍废了。优先采用抽取式裁剪（只保留原句，不改写措辞），并把抽取结果绑定回父片段 ID；如果必须做生成式摘要，专门针对否定词、金额阈值和前置条件做抽样核对。

## 打包预算一定要把标签和分隔符算进去

很多线上“超出上下文长度”报错，是因为预算只算了正文字符数，漏算了片段编号、来源路径、换行分隔符、系统提示词和工具定义。配套 `pack_context(chunks, budget=1200)` 故意把整块开销算得很实：

```python
block = f"[{chunk.id}] {chunk.source}\n{chunk.text}"
cost = len(block) + (2 if blocks else 0)
if used + cost <= budget:
    blocks.append(block)
    selected.append(chunk.id)
    used += cost
```

它坚持按整块装入，宁可跳过超预算的块也不把一句话从中间劈断，避免把“不重复发货”截成“重复发货”；同时返回 `(context, injected_ids)`，让下游和日志一眼看见究竟哪些块真正进了模型。上线接大模型时，记得把这里的字符计数换成对应模型的真实 Tokenizer，并提前扣出系统指令、历史对话、用户问题和预期输出空间。

另外，贪心按序打包并不天然保证多子问题均衡覆盖。如果一个问题拆出了验签和幂等两路子查询，打包器可能需要按子问题配额各保底留一条，避免第一路把预算吃干抹净。

## 引用只能从 `injected_ids` 里产生

交给生成器之后，系统提示和后置校验要死守一条规则：回答里出现的每一个引用编号，必须严格属于本次实际交付的 `injected_ids` 子集，绝不允许模型引用被预算裁掉的候选，更不允许凭记忆编造编号。

编号存在只是第一关，更难的是逐句核对支持性（grounding）：编号指向的原文到底支不支持前面那句结论。上游 `explainable_retrieval.ipynb` 演示了让模型解释“为什么这条片段与问题相关”，这种解释有助于调试召回，但“包含相同关键词所以相关”不等于“这条现行规则支持你的最终结论”。界面上把结论和原文摘录并排展示，往往比多写一段流畅的解释更能帮值班工程师迅速确认对错。

## 本章对应代码与资料

- [邻域扩展 `expand` 与预算打包 `pack_context`](../../examples/mini_rag.py)
- [窗口边界与打包计数测试](../../examples/test_mini_rag.py)
- [上游 Context Enrichment（LangChain）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/context_enrichment_window_around_chunk.ipynb)
- [上游 Context Enrichment（LlamaIndex 0.14）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/context_enrichment_window_around_chunk_with_llamaindex.ipynb)
- [上游 Relevant Segment Extraction](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/relevant_segment_extraction.ipynb)
- [上游 Contextual Compression](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/contextual_compression.ipynb)
- [上游 Explainable Retrieval](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/explainable_retrieval.ipynb)
