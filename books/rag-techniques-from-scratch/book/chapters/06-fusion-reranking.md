# 混合检索与重排：把不同候选放进同一张账

研发在排查故障时，有时直接贴一个报错码或函数名 `callback_handler`，这时倒排关键词比向量准得多；有时只记得大概意思，随手问一句“同一笔通知来了两次怎么办”，字面完全对不上“幂等”，需要语义向量来补位。把两路检索合起来跑，听上去顺理成章，真正写代码时最容易出纰漏的却是身份对齐和分数口径。

## 分数加权与按名次融合的区别

合并多路召回通常有两种算法。

第一种是分数归一化加权：把 BM25 得分和向量相似度分别缩放到 `[0, 1]`，再按 `alpha * vec + (1 - alpha) * bm25` 相加。隐患在于不同检索器的分数分布和方向都不同（比如 FAISS 默认 L2 距离越小越相似，BM25 则是越大越相关），而且单次查询里极值一抖，归一化后的相对差距就会剧烈变形。

第二种是倒数排名融合（Reciprocal Rank Fusion，简称 RRF）。它完全忽略原始分数尺度，只看每条片段在各路榜单里的名次，按稳定 ID 累加 `1 / (c + rank)`（常见常数 `c = 60`）。常数越大，第一名和第二名之间的差距越平缓。RRF 的好处是免调跨尺度权重，但也带来一个约束：融合得分只适合做同一次请求内的相对排序，绝不能当成“回答正确的概率”，也不能靠写死一个 RRF 阈值就断定证据一定充分。

![混合检索、RRF 身份对齐与重排链路](../../wiki/assets/diagrams/hybrid-rrf-rerank.svg)

## 静态核对发现的数组错位陷阱

在合并两路结果时，最隐蔽的一类 bug 是把“同一个数组下标”当成“同一份文档”。

```text
BM25 原序得分：     [A: 0.9, B: 0.1]
向量按距离排序返回： [B: 0.8, A: 0.2]

按下标直接相加：下标 0 把 A 的 BM25 分加到了 B 的向量分上
按 chunk.id 对齐：A 拿 A 的两路分数，B 拿 B 的两路分数
```

静态阅读上游 `all_rag_techniques/fusion_retrieval.ipynb`（固定提交 `2fd7bd0f` 第 17 个单元），可以看到 `fusion_retrieval()` 分别执行了三步：`all_docs = vectorstore.similarity_search("", k=...)`、`bm25_scores = bm25.get_scores(query.split())`（按 `cleaned_texts` 原始顺序排列），以及 `vector_results = vectorstore.similarity_search_with_score(query, k=len(all_docs))`（按当前查询的 L2 距离排序）。随后代码直接用 `alpha * vector_scores + (1 - alpha) * bm25_scores` 按数组位置相加，最后用下标去切 `all_docs`。三份列表没有按稳定文档 ID 做显式关联，存在真实的位置错位风险。

把这一课写进自己系统的底线很简单：不管用分数加权还是 RRF，融合函数的输入必须是带稳定 `chunk.id` 的字典或 ID 序列，彻底杜绝平行数组按位置拼接。

## RRF 为什么要在每路榜单内先去重

到了多向量或代理索引场景（比如第 7 章的 HyPE），同一段原文可能通过多个问题代理在同一路召回里命中三次。如果不先按 `chunk.id` 去重，单路里的重复出现就会被算成多次独立投票，人为抬高名次。

配套代码里的 `rrf()` 先用 `dict.fromkeys(ranking)` 保留每条榜单内每个 ID 的首次最佳名次，再累计倒数贡献，最后按 `(-score, id)` 稳定排序：

```python
ranking = rrf([
    ["callback-v2:0", "callback-v2:1"],
    ["callback-v2:1", "callback-v2:0"],
])
```

同分时按 ID 字典序决胜，目的是让离线回归结果确定、可复现，而不是每次跑出随机顺序。配套离线实验里融合的是 BM25 和词频余弦两路词面信号，用来验证身份对齐与去重机制；接进生产环境时，再把其中一路换成真正的稠密向量检索即可。

## 重排解决的是排序精度，补不了漏召回

召回阶段追求速度与覆盖，通常一次拉回 20 到 50 条候选；重排阶段把问题和每条候选拼在一起精读打分，再挑出头部几条送进证据包。常见两类重排器：

- Cross-Encoder 交叉编码器把 `(query, chunk)` 联合输入模型做深层交互打分，精度通常高于双塔向量，吞吐也受候选数限制。
- LLM 重排通过提示词加结构化输出（上游 `reranking.ipynb` 在 LangChain 1.x 下使用 `llm.with_structured_output(RatingScore)` 并把自定义检索器迁到了 `_get_relevant_documents`）逐条打分，灵活但延迟和费用更高。

用重排器必须盯紧两条边界。第一，如果幂等段落根本没进前 30 条召回候选，再强的重排器也不可能凭空把它变出来。第二，面对多条件复合问题（既问验签又问幂等），如果重排后只截断保留 top-1，虽然第一名精准度满分，整道题的证据完整性反而跌成失败。

## 元数据过滤与多样性裁剪放哪一层

权限范围（`scope`）、生效状态（`active`）、业务线和文档类型这类硬条件，必须在召回计分之前执行，防止受限内容影响统计量或混进候选池。多样性策略（如 MMR 或上游 `dartboard.ipynb` 的相关性与多样性联合选择）则放在召回之后、打包之前，用来清掉高度重复的摘录。但处理规则类文档时要格外克制：两段文字长得很像，可能分别针对境内支付和跨境支付，机械按相似度去重会把其中一条关键例外直接删掉。

## 本章对应代码与资料

- [RRF 身份对齐与融合实现](../../examples/mini_rag.py)
- [RRF 去重与同分稳定测试](../../examples/test_mini_rag.py)
- [上游 Fusion Retrieval（LangChain）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/fusion_retrieval.ipynb)
- [上游 Fusion Retrieval（LlamaIndex 0.14）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/fusion_retrieval_with_llamaindex.ipynb)
- [上游 Reranking（LangChain 1.x）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/reranking.ipynb)
- [上游 Reranking（LlamaIndex 0.14）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/reranking_with_llamaindex.ipynb)
- [上游多维过滤](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/multi_faceted_filtering.ipynb)
- [上游 Dartboard](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/dartboard.ipynb)
