# 基础检索：先交付一条能复现的离线链路

数据模型和切分边界敲定后，第一步是把一条全过程透明的基线跑通。先不接远程 LLM，也不急着下几百兆的向量模型。只有当权限过滤、分词、打分、来源标签和预算打包全部能单步核对，后续引入稠密向量和生成器时，你才分得清效果变化究竟是谁带来的。

## 中文检索为什么不能直接用空格分词

英文教程里常见一句 `doc.page_content.split()` 就建好了 BM25 索引，放到中文规范里会直接失效：连续汉字之间没有空格，整句话会被当成一个超长词项，几乎永远匹配不上。

配套 `examples/mini_rag.py` 的 `tokenize()` 用了一个零依赖的折中办法：英文和数字（含下划线，比如 `callback_v2`）按词保留，连续中文切成重叠双字组（bigram），单字单独保留。

```text
支付回调验签
→ 支付 / 付回 / 回调 / 调验 / 验签
```

切出来的“付回”“调验”本身没有词义，但“支付”“回调”“验签”都能稳定参与命中，而且无需额外词典文件。它当然比不上带领域词库的专业分词器，更代替不了语义编码模型；遇到标点切断的词组、单字查询或特定行业缩写时，必须放进单元测试单独校验。无论线上选哪套分词方案，记住一条硬规矩：修改分词规则后，倒排索引必须全量重建。

## BM25 与词频余弦各算什么账

BM25 综合三件事给片段打分：查询词在当前片段里出现的次数、这个词在可见语料里的稀有程度（IDF），以及片段长度相对于平均长度的归一化惩罚。词频收益会很快饱和，避免某段反复堆砌“支付”几十次就霸榜。配套代码采用常见的教学参数 `k1=1.5`、`b=0.75` 和正值 IDF，并且严格只在当前用户的 `visible(user)` 子集里统计文档频率，不让 Bob 的私人笔记影响 Alice 的 IDF 分数。

词频余弦则把查询和片段各表示为词频向量，算点积再除以各自模长。它和 BM25 一样依赖字面重合：用户问“同一事件多次投递”，原文写“重复通知”，两者没有共享双字组时，词频余弦同样给零分。

真正的语义召回靠训练好的稠密编码模型（dense embedding）把文本映射到连续向量空间。把这类模型接进线上时，模型名称、向量维度、是否做 L2 归一化以及距离度量（余弦、点积还是欧氏距离）四项元数据必须绑在索引版本上。换了 embedding 模型却继续去查旧向量库，返回的就是毫无意义的随机近邻。

## 跑一遍离线基线看真实输出

在项目根目录执行：

```bash
python3 examples/mini_rag.py --query "支付回调验签和幂等如何处理"
```

在 10 条教学片段的固定快照上，默认 `fusion` 模式返回两条候选 `callback-v2:0` 与 `callback-v2:1`，并原样装入最终证据包，连同编号和来源标签共占 168 个字符。验收这条输出时先检查三件事：命中的是不是 v2 现行版，有没有混入 Bob 的私人笔记，验签和幂等是不是两条都在。

再试一个语料里根本不存在的词：

```bash
python3 examples/mini_rag.py --query xyzunfindable
```

程序返回空列表，并打印“没有可交付证据。此示例不生成答案。”它不会为了凑足 `k=2` 而强行把零分文档塞进上下文。在检索层守住“没命中就返回空”，下游才有机会触发改写或拒答。

## 直接照搬上游 Simple RAG 会踩的框架坑

上游 `simple_rag.ipynb` 和 `simple_rag_with_llamaindex.ipynb` 演示了基于 OpenAI embedding 和 FAISS 的经典接线方式。把这类教学 Notebook 改造成长期维护的服务时，框架大版本迁移往往比检索算法本身更耗时。

对照上游提交 `2fd7bd0f`（2026-10-31 完成的 PR #212 与 #211），可以看到两条非常真实的迁移教训：

| 框架与组件 | 旧写法（常见于早期教程） | 2026-11 新版写法（上游实测） | 踩坑后果 |
| --- | --- | --- | --- |
| LangChain 1.x 旧链与压缩器 | `from langchain.chains import RetrievalQA` | `from langchain_classic.chains import RetrievalQA` | 升级后直接 `ImportError` |
| LangChain 1.x 文档与模型校验 | `langchain.docstore.document`、`langchain_core.pydantic_v1` | `langchain_core.documents.Document`、原生 `pydantic` | 废弃模块移除导致启动失败 |
| LangChain 1.x 检索器调用 | 实现并调用 `get_relevant_documents(q)` | 实现 `_get_relevant_documents(q, *, run_manager=None)`，调用 `.invoke(q)` | 自定义检索器或 `helper_functions.py` 报错 |
| LlamaIndex 0.14 节点与 BM25 | 修改 `node.text = ...`、`llama_index.legacy.retrievers.bm25_retriever` | 调用 `node.set_content(...)`、`llama_index.retrievers.bm25` | 新版 `.text` 变为只读属性，赋值直接抛异常 |
| LlamaIndex 默认对话模型 | 依赖框架默认 `Settings.llm` | 显式指定 `Settings.llm = OpenAI(model=LLM_MODEL)` | 默认 `gpt-3.5-turbo` 于 2026-10-23 停服，未显式配置即全线失败 |

把业务核心抽象成自己的 `Chunk`、`Retriever` 和结果协议，框架只留在最外层的薄适配层里，升级 LangChain 或 LlamaIndex 时才不至于牵一发而动全身。

## 本章对应代码与资料

- [分词、BM25 与词频余弦实现](../../examples/mini_rag.py)
- [基础检索与空命中测试](../../examples/test_mini_rag.py)
- [上游 Simple RAG（LangChain 1.x）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_rag.ipynb)
- [上游 Simple RAG（LlamaIndex 0.14）](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_rag_with_llamaindex.ipynb)
- [上游本地 Hugging Face + FAISS](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/local_rag_huggingface_faiss.ipynb)
