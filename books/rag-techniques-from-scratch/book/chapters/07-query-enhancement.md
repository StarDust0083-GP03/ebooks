# 查询增强：用户不一定会说出幂等这个词

值班同学在聊天框里敲下：“为什么同一个通知来了两次，不能再发一次货？”规范文档里写得清清楚楚：“支付回调幂等以渠道流水号和事件类型为键。重复通知不重复发货。”文档没缺、解析没歪、切分也没断，可用户压根没提“幂等”两个字，只靠字面基线很容易把最关键的那段排到后面。查询增强要补的，正是用户口语表达与文档规范术语之间的这道鸿沟。

![查询改写、HyDE 与 HyPE 的对齐路径对比](../../wiki/assets/diagrams/query-routing.svg)

## 查询改写不能把业务限定改丢

改写模块把口语问题翻译成贴近知识库术语的检索语句，比如改写成“支付回调重复投递 幂等 发货去重”。动手写改写提示词时要守住三条线：

- 保留原有业务条件：用户问沙箱测试环境，改写绝不能偷换成生产环境；用户问“为什么不能直接重试”，也不能改写成“如何强制重试”。
- 保留原查询并行检索：把原始问题和改写结果分别送进检索器，再按 `chunk.id` 做 RRF 合并，防止改写跑偏时把原本能命中的精准词弄丢。
- 控制外传风险：用户提问里常常夹带真实订单号、商户号或报错堆栈。把原始问题发给外部模型做改写之前，必须过一遍脱敏或数据出境策略，别以为“还没生成答案”就不算数据外泄。

日志里要把原问题、改写后的查询以及各自命中的片段 ID 全部记下来，复盘时才能看清改写到底是帮了忙，还是悄悄换了一道更好答的题。

## 复合问题拆解与 Step-back 的边界

面对“退款失败为什么不能直接再发起？出了问题该找谁评审？”这种两段式提问，直接拿整句话去搜，常常出现前一半返回一堆退款条款、把后一半的负责人线索彻底挤出 top-k。

查询拆解把原问题切成“退款失败重试规则”和“支付回调/退款模块技术负责人”两条子查询，分别召回并按子问题检查证据覆盖度，防止“总条数够了，其中一个子问题却是零证据”。同时必须给子查询数量、每路 top-k 和总耗时设硬上限，避免一句复杂提问触发十几次连锁调用。

Step-back 则是把一个过细的问题往上一层抽象，比如把某渠道的具体报错上提成“异步回调一致性风险”。它适合给回答补足背景原理，绝不能替代当前版本规范里的精确条款。

## HyDE：查询时借假设文档做跳板

短问题和长段落放在同一个向量空间里，句式与信息密度天然不对称。HyDE（Hypothetical Document Embeddings，论文 arXiv:2212.10496）的做法是在查询时先让语言模型写一段“看起来像规范正文”的假设答案，再把这段假设文档编成向量去搜真实知识库。

这条路线有一条铁律：假设文档只用来算向量、找线索，绝不能混进最终交付给生成器的证据包。假设段落里模型很可能顺手编出一句“按订单号去重”，而真实规范写的是“渠道流水号和事件类型”。把假设当事实，等于自己给系统亲手喂了幻觉。顺便记一笔核对细节：上游仓库 README 里曾有文字把 HyDE 简写成假设问题，但对应 Notebook（`HyDe_Hypothetical_Document_Embedding.ipynb`）和原论文的核心动作都是生成假设文档，写设计评审时别和下面的 HyPE 搞混。

## HyPE：把生成开销前移到入库阶段

每次查询都先调一次 LLM 写假设文档，延迟和费用很快吃不消。HyPE（Hypothetical Prompt Embeddings）换了个方向：在文档入库建索引时，提前为每个片段生成若干“用户可能会怎么问”的代理问题，只对这些问题建索引；线上收到用户提问后，直接做“问题对问题”的匹配，命中代理后再回映射拿到原片段。

配套 `examples/mini_rag.py` 里的 `PromptIndex` 和 `PromptProxy` 在离线环境里完整复现了这套核心契约：

```python
proxies = [
    PromptProxy("p1", "callback-v2:1", "同一个通知来了两次会不会重复发货"),
    PromptProxy("p2", "callback-v2:1", "同一笔支付消息推送两遍怎么办"),
]
idx = PromptIndex(retriever, proxies)
hits = idx.search("同一个通知来了两次怎么办", user="alice", k=3)
```

看 `PromptIndex.search()` 的实现就能摸清上线 HyPE 必须盯住的三个工程点：

1. 权限与版本过滤必须先查父片段：只有当 `parent_id` 在 `retriever.visible(user)` 集合里，对应的问题代理才有资格参与打分。单元测试 `test_prompt_index_enforces_acl_and_active_view_before_scoring` 验证了 Alice 查“排查草稿”时拿不到 Bob 笔记的代理，也拿不到已废弃 `callback-v1:0` 的代理。
2. 多代理命中同一父块必须去重：`p1` 和 `p2` 都指向 `callback-v2:1`，返回列表里只保留一条 `callback-v2:1`，不把同一原文塞两遍。
3. 代理不是零成本：每块生成 5 个问题，索引体量和入库 LLM 费用会成倍放大，而且原文一旦修改或删除，所有关联代理必须同步重建或清理。此外，查询时依然需要算查询 embedding、做检索和生成答案，只是省掉了查询时的那一次假设生成延迟。

这里还有一条来自上游实操的工程警示：在 `2fd7bd0f` 之前，`all_rag_techniques/HyPE_Hypothetical_Prompt_Embeddings.ipynb` 曾在 `FAISS(...)` 构造参数里漏写了一个逗号，属于肉眼很难扫出来的语法错误，直到 2026-10-31 上游审计补上无密钥语法检查 CI（`notebook-smoke.yml`）才彻底拦住。索引构建脚本通常在夜间批处理里跑，离线语法与单测门禁省不得。

## 什么场景先试哪一招

别一上来就把四种查询增强全叠在一起。遇到固定行话对不上（比如“重复投递”对“幂等”），先配一张受控同义词表或“原查询 + 改写”双路融合；遇到多跳复合问题，先加受控拆解；短问题搜长规范持续漏召回时，再拿 HyDE 和 HyPE 对照评估：文档更新慢、查询并发高偏向把成本前移到 HyPE，文档频繁变动、查询量不大则更容易接受查询时的 HyDE。

## 本章对应代码与资料

- [离线 HyPE 代理索引 `PromptIndex`](../../examples/mini_rag.py)
- [代理映射、去重与权限隔离测试](../../examples/test_mini_rag.py)
- [上游 Query Transformations](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/query_transformations.ipynb)
- [上游 HyDE](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/HyDe_Hypothetical_Document_Embedding.ipynb)
- [上游 HyPE](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/HyPE_Hypothetical_Prompt_Embeddings.ipynb)
- [上游 Document Augmentation](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/document_augmentation.ipynb)
- [HyDE 论文（arXiv:2212.10496）](https://arxiv.org/abs/2212.10496)
