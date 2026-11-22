# 文档切分：验签和幂等，为什么总只召回一半

支付回调规范第一段写验签和字段核对，第二段写幂等键和重复通知去重。研发一口气把两个点合在一句话里问出来，检索器按 top-1 或小预算返回时，往往只抓回第一段。有人图省事把整份三十页的支付手册塞成一个大块，检索确实不漏段了，可每次提问都把退款、对账和密钥轮换全灌进上下文，既烧 Token 又让模型注意力发散。切分设计绕不开的就是这个矛盾。

## 把召回单位和阅读单位拆开

解决大块太吵、小块太碎的核心思路，是让检索用小块做精确定位，交付给模型时再把相邻语境补回来。切得足够细，验签段只表达验签，幂等段只表达幂等，向量不会被无关主题稀释；命中其中一个块后，再沿同一文档、同一版本取出前后相邻段落，送进模型的就是完整规则。

![字符切分失真与结构感知邻域扩展对比](../../wiki/assets/diagrams/chunking-boundary.svg)

邻域补全必须带护栏。同一份文档的相邻两段，如果分属不同权限范围（比如紧挨着公开规范放了一段内部排查草稿），或者版本号不同，物理位置再近也不能拼进同一个交付窗口。

## 固定长度切分的边界与陷阱

最朴素的切分器按固定字符数和重叠窗口滑动。配套代码提供了最小演示函数 `split_text(text, size=400, overlap=80)`：

```python
def split_text(text, size=400, overlap=80):
    if type(size) is not int or type(overlap) is not int or not 0 <= overlap < size:
        raise ValueError("require integer 0 <= overlap < size")
    return [text[i:i + size] for i in range(0, len(text), size - overlap)]
```

每次前进的步长是 `size - overlap`（这里为 320 个字符），边界附近的 80 个字符会同时落在相邻两块里，缓解句子拦腰斩断的问题。入口必须严格校验 `0 <= overlap < size` 且均为整数：一旦配置失误把 `overlap` 设成等于 `size`，步长归零，切片循环就会卡死。

固定字符切分有两个现实短板。第一，字符数不等于 Token 数，中英文混排时同样的 400 字消耗的 Token 差别很大；第二，它眼里没有段落、编号列表和表格边界，上一章提到的 OCR 4.1 案例里，固定切分器正好一刀把表格和表题切成了两半。

## 用结构标签守住表格与标题

一旦解析阶段拿到了结构化的块类型（如 Markdown 语法树或 OCR 4.1 的 `blocks`），切分就从数字符变成了按结构打包。配套 `examples/mini_rag.py` 里的 `block_aware_split(blocks, max_chars=400)` 演示了这条规则：普通段落按预算合并，遇到紧跟在 `caption` 后面的 `table` 时，把表题和整张表绑成一个原子单元，即便超出 `max_chars` 也不在表格中间拦腰切断。

单元测试 `test_block_aware_split_keeps_table_with_caption` 用 `max_chars=40` 模拟极端窄预算，确认表题 `表 1：支付回调事件与确认规则` 和表格里的 `pay.succeeded`、`refund.failed` 始终留在同一块里。上线处理带费率表、错误码表的技术规范时，这一步往往比调什么高级向量模型见效更快。

## 语义切分与命题切分各治什么病

纯结构切分解决不了长篇散文里的主题漂移。语义切分（Semantic Chunking）先把文本断成句子，逐句算 embedding，在相邻句子余弦相似度骤降的地方落刀，让同一块尽量只讲一个主题。它额外引入了断句误差、模型调用和阈值敏感性，仍需配上最小和最大块长兜底，遇到编号条款和表格也容易误切。

命题切分（Proposition Chunking）走得更彻底：让语言模型把原文改写成一条条主语明确、脱离上下文也能独立成立的原子事实。代价在于改写本身可能引入语义失真。原句写着“重复回调不重复发货，退款事件另行处理”，如果模型顺手概括成“所有事件都不重复处理”，就把关键限定词吃掉了。采用命题切分时，每条命题必须挂回原片段 ID，并针对否定词与条件从句做抽样复核。

这两种方法开销都不低。先看离线失败样本，确认问题真的出在主题混杂或指代不明，再按需引入。

## 上下文标题增强先用现成结构

孤零零的一块文字写着“核对成功后才更新支付状态”，脱离上下文根本看不出说的是支付回调还是退款回调。给用于检索的文本前面拼上章节路径，比如 `"支付规范 v2 / 回调处理：核对成功后才更新支付状态"`，召回和重排都能省力不少。

这里有两条实用经验。第一，原文如果自带清晰的多级标题，直接继承文档结构比花钱让 LLM 为每块瞎猜标题稳得多。第二，要把 `retrieval_text`（带增强标题，专供建索引）和 `source_text`（原始正文，用于页面展示与引用核对）拆成两个字段保存，避免把模型生成的标题误当成规范原文。上游 `contextual_chunk_headers.ipynb` 对比了加标题前后的重排打分，直观展示了标题的作用，但单靠几个片段的重排分不能直接推导出全库召回率提升，仍需放进固定题集实测。

## 切分实验怎么避免归因混乱

调切分参数最容易犯的错，是一次同时改好几个旋钮。上游 `choose_chunk_size.ipynb` 在演示实验里同步调整了 `chunk_size` 和 `overlap`，这在对比两组完整配方时没问题，但不能把结果全归功于块长本身。自己做回归测试时，固定同一份解析输出和同一套题集，记录块长、重叠、必要证据召回和最终注入字符数；要单独评估某一个参数，就保持其余条件不动。

## 本章对应代码与资料

- [固定切分、块感知切分与邻域扩展](../../examples/mini_rag.py)
- [切分与边界单元测试](../../examples/test_mini_rag.py)
- [上游 Choose Chunk Size](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/choose_chunk_size.ipynb)
- [上游 Semantic Chunking](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/semantic_chunking.ipynb)
- [上游 Proposition Chunking](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/proposition_chunking.ipynb)
- [上游 Contextual Chunk Headers](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/contextual_chunk_headers.ipynb)
