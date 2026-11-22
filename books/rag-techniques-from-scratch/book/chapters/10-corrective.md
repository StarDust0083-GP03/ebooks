# 纠错检索：找不到以后，系统走哪一条路

值班同事问：“退款超时后能不能直接再发起一笔新退款？”检索器这次没撞中退款规范，只拉回来两条支付成功回调的段落。如果系统傻傻地把无关材料塞给大模型，模型多半会顺着参数里的通用经验编出一段看似稳妥的建议。在企业内部规则场景下，拿通用常识冒充公司现行规范，比直接回答“当前资料不足”危害大得多。纠错检索要设计的，就是“检索质量不达标时的分流状态机”。

## 先分清相关性和充分性

一段文档提到“退款单字段定义”，对上面那个问题算相关，却根本回答不了“超时后能不能重试”；只有明确写着“先查询渠道结果，再决定是否重试，不得仅凭超时再次发起新退款”的 `refund-v2:0`，才算具备充分性。

给候选池加一层相关性评估、给最终证据包加一层充分性评估，思路很清晰，但别迷信模型吐出来的打分数字。提示词里让模型“给相关性打 0 到 1 分”，它给出的 `0.8` 并不对应统计学上的 80% 真实准确率。落地时优先用离散档位（比如充分、模糊、无关）配合人工标注阈值校准，比直接拿浮点分画死线可靠得多。

## Self-RAG 的论文与教程别混为一谈

Self-RAG（论文 arXiv:2310.11511）的原始设计是通过微调让模型学会输出特殊反思 token，自主决定要不要检索、证据有没有支撑、回答有没有用。上游 `self_rag.ipynb` 则是用多轮提示词串联检索判断、相关性评分、生成、支持度检查和效用评估，方便在通用模型上演示控制流，但开销和精度都与原论文的专门训练模型不同。

读 `self_rag.ipynb` 时要特别留意一个默认分支：当检索回来的段落全被判为不相关时，示例会退回无检索生成，直接靠大模型自身知识作答。做开放百科问答这样兜底没问题，放到内部支付规范助手上就必须改写这个分支：找不到有效证据时，要么在受控次数内换改写词再查一次，要么老老实实返回 `insufficient_evidence`，绝不把无证据猜测包装成正式结论。

## CRAG 把检索失败变成三态状态机

CRAG（Corrective RAG，论文 arXiv:2401.15884）把检索质量拆成三种状态处理：质量高直接提炼使用，质量模糊则改写补充，质量低则触发纠错回退。

![CRAG 与受控纠错检索状态机](../../wiki/assets/diagrams/crag-state-machine.svg)

配套 `examples/mini_rag.py` 里的 `crag_route()` 用纯 Python 实现了面向内部规范的受控版本：

```python
res = crag_route(
    retriever,
    query="xyzunfindable",
    user="alice",
    rewrites=["重复通知如何处理"],
    max_attempts=2,
)
```

这套路由有四条明确约束，并全部覆盖在单元测试里：

1. 首轮命中高分（`correct`）立刻停手返回 `evidence_ready`，不白白触发后续改写。
2. 首轮未命中或偏弱（`incorrect` / `ambiguous`）且未到 `max_attempts` 时，用下一条改写词重新检索；整个重试过程始终锁定同一个服务端 `user` 权限视图，绝不因为第一次没搜到就放宽权限或打开已废弃版本。
3. 达到重试次数上限仍无达标证据，立即终止并返回 `status="insufficient_evidence"`，杜绝死循环刷账单。
4. 全程输出 `trace` 记录每一轮查询词、状态、最高分和命中 ID，事后一眼能查清是第几轮救回来的。

## 内部规范为什么不能默认上网兜底

公开版 CRAG 教程（上游 `crag.ipynb`，在 LangChain 1.x 迁移后使用 `langchain_community.tools.DuckDuckGoSearchResults(output_format="json")` 供 JSON 解析器读取）在本地检索失败时会去搜公网。这一步搬到企业内网有两个硬伤：

- 外部网页查得到通用支付协议，查不到你们公司结算组昨天刚改的回调幂等约定。
- 把带着内部商户名、报错详情或风控规则的问题直接丢给公网搜索引擎，本身就是一次数据泄密事故。

如果业务确实同时涉及公开第三方 SDK 文档和内部规范，应当按数据分级拆开路由：公网搜索只允许在白名单问题类型上触发、先过脱敏过滤，并且把外部来源与发布时间标得清清楚楚；一旦公网说法和公司内部规范冲突，直接把两者的适用范围并排呈现，不要让模型擅自选“听起来更专业”的那一边。

## 自适应路由与反馈收集的克制用法

不是每条请求都值得走完整纠错循环。上游 `adaptive_retrieval.ipynb` 演示了先给问题分类再选检索策略：查精确错误码或接口名直接走关键词，问概念和原因走混合检索，查单笔实时交易转去业务 API，跨文档查负责人走受控两步检索。分类器本身也会误判，所以既要记录实际走了哪条路，也要留给用户手动切换或重试的入口。

至于线上点赞和点踩反馈（参考 `retrieval_with_feedback_loop.ipynb`），最稳妥的用法是回流到离线评估集做人工归因，而不是让一次点踩就自动降权或删文档。用户点踩可能是检索漏了、可能是回答编了、也可能是文档本身已经过时；不先定位层就直接喂回排序权重，只会把好规范慢慢冲走。

## 本章对应代码与资料

- [有界三态纠错路由 `crag_route`](../../examples/mini_rag.py)
- [首轮直出、改写恢复与耗尽拒答测试](../../examples/test_mini_rag.py)
- [上游 Self-RAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/self_rag.ipynb)
- [上游 CRAG](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/crag.ipynb)
- [上游 Adaptive Retrieval](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/adaptive_retrieval.ipynb)
- [上游 Feedback Loop](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/retrieval_with_feedback_loop.ipynb)
- [Self-RAG 论文（arXiv:2310.11511）](https://arxiv.org/abs/2310.11511)
- [CRAG 论文（arXiv:2401.15884）](https://arxiv.org/abs/2401.15884)
