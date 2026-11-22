# 图谱检索：从模块一路查到技术负责人

线上报警响了，研发问：“支付回调模块出问题找谁评审？”打开我们的教学语料看两条记录：`owner-v1:0` 写着“支付回调模块由结算组维护”，`team-v1:0` 写着“结算组的技术负责人是陈禾，负责评审与故障协调”。第二条里根本没出现“回调”两个字。去掉题目里的提示词后，单次检索很容易只拉回 `owner-v1:0`，到“结算组”就断了线。这里缺的是一条跨文档实体连接。

## 先试受控两步检索，还是直接建图

遇到两跳问题，最省力的做法通常是显式写一条两步查询，不必立刻采购图数据库：先按“支付回调模块 维护团队”查到“结算组”，再把“结算组 技术负责人”送进第二次检索拿到 `team-v1:0`。如果业务里的跨文档路径就固定那么两三条（比如模块 → 团队 → 负责人），受控两步检索逻辑透明、不用维护离线抽取流水线，维护成本低得多。

只有当实体关系网很密、大量问题反复沿着不同关系链跳转（比如服务依赖、接口调用、人员职责、制度引用交织在一起），把高频连接提前沉淀成图结构才划算：

```text
支付回调模块 ──维护团队──→ 结算组 ──技术负责人──→ 陈禾
  来源 owner-v1:0              来源 team-v1:0
```

## 图里的每条边都要带方向、出处与权限

真正把知识图谱用稳，难点不在画几个节点和连线，而在四项数据纪律：

- 边必须有明确方向和关系语义。“陈禾负责结算组”反过来不成立，“维护支付回调模块”也不等于“有权审批生产退款”。`team-v1:0` 特意写明“此记录不代表批准任何真实退款”，就是为了防止下游把职责关系擅自升格成操作授权。
- 每条边必须绑定原文出处（chunk ID 与版本）。只把三元组塞进模型，用户没法核对这条关系是原文真写的，还是抽取模型自己脑补的。
- 同名实体必须靠稳定业务标识消歧。简单把字符串转小写去空格，根本分不清两个都叫“结算组”的不同事业部团队，合并错节点会把整条链路带沟里。
- 图遍历必须在当前用户可见的子图上执行。如果中间节点或边来自某份受限文档，不仅不能返回原文，连节点的存在和度计数都不该暴露给无权用户。另外，组织架构会变动，查“现在归谁管”和查“去年故障时归谁管”需要按生效时间切视图。

## 上游五条 Graph RAG 路线定位各不相同

很多人嘴里都说“上 Graph RAG”，实际指向的是完全不同的架构。核对上游仓库的 5 份相关 Notebook，就能分清它们各自解什么题：

| 上游 Notebook | 技术侧重 | 适合哪类问题 |
| --- | --- | --- |
| `graph_rag.ipynb` | 通用实体关系抽取配合文本检索 | 局部多跳关系查找 |
| `graphrag_with_milvus_vectordb.ipynb` | 文本块与关系三元组分集合入库并做关系重排 | 已有向量库，希望补充三元组检索 |
| `graph_rag_local_attribution.ipynb` | 本地模型加 NetworkX，强调句级出处归因 | 数据不能出内网且重视逐句溯源 |
| `Microsoft_GraphRag.ipynb` | 实体图聚类 + 社区摘要 + 局部答案再全局汇总（arXiv:2404.16130） | 面向全语料的全局归纳题，而非简单两跳查询 |
| `light_rag.ipynb` | 低层具体实体 + 高层主题关系双层检索（arXiv:2410.05779） | 兼顾局部细节与高层关联的轻量方案 |

尤其要注意 Microsoft GraphRAG 的定位：它通过社区检测和社区摘要来回答“整个语料库反映出哪些核心主题”这类全局问题，构建与更新摘要的 LLM 账单很可观，拿它来替代一条简单的两跳负责人查询属于高射炮打蚊子。

## 读 LightRAG 示例时看清简化边界

静态核对上游 `all_rag_techniques/light_rag.ipynb`（提交 `2fd7bd0f`），它用 NetworkX 存图，并建了实体和关系两套 FAISS 索引来演示低层/高层双层召回，读起来很直观。但它为了教学做了几处明显简化：使用了无向图、实体主键主要靠名称去空格转小写拼接，测试语料片段也做了截取。

把这套原型搬进真实业务前，需要自己补齐有向关系、实体消歧、多租户权限过滤和文档删除后的边清理机制。同时必须保留纯文本回退链路：LLM 抽三元组难免漏抽，图里查不到边只说明没抽出来，绝不能直接推导成“文档里没有这项关系”。

## 图谱回归测试要验收整条证据路径

评估跨文档负责人题时，不能只看最终答案里有没有出现“陈禾”两个字，必须要求交付证据里同时包含 `owner-v1:0` 和 `team-v1:0`，并且把起点实体、经过的边和每条边的原文出处一起记进日志。这样才能拦住模型凭记忆猜中人名，或者误经过同名团队撞巧答对的假阳性。专项回归里还要专门塞入三类破坏用例：负责人已换任的旧路径、经过无权限中间节点的越权路径、以及颠倒主宾语的反向关系提问。

## 本章对应资料

- [跨文档负责人样例（`owner-v1:0`、`team-v1:0`）](../../examples/documents.jsonl)
- [上游通用 Graph RAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/graph_rag.ipynb)
- [上游 Milvus Graph RAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/graphrag_with_milvus_vectordb.ipynb)
- [上游本地句级归因 Graph RAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/graph_rag_local_attribution.ipynb)
- [上游 Microsoft GraphRAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/Microsoft_GraphRag.ipynb)
- [上游 LightRAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/light_rag.ipynb)
- [GraphRAG 论文（arXiv:2404.16130）](https://arxiv.org/abs/2404.16130)
- [LightRAG 论文（arXiv:2410.05779）](https://arxiv.org/abs/2410.05779)
