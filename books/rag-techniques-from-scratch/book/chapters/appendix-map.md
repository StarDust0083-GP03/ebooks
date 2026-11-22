# 附录 A：上游 47 份 Notebook 与 21 份脚本阅读地图

上游快照 `2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6`（2026-11-02）包含 42 份技术 Notebook（`all_rag_techniques/*.ipynb`）与 5 份评估 Notebook（`evaluation/*.ipynb`），合计 47 份 `.ipynb` 文件；另有 21 份可独立运行的 Python 脚本位于 `all_rag_techniques_runnable_scripts/`。按实际文件路径统计包含 LangChain 与 LlamaIndex 两种框架实现变体，不等同于 47 种彼此独立的算法。

下表按仓库真实路径列出全部 47 份 Notebook 及其在本书的对应章节，所有外部链接均固定到本次快照提交。

## 全部 47 份 Notebook 索引

| Notebook 路径 | 本书对应章节 |
| --- | --- |
| [all_rag_techniques/Agentic_RAG.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/Agentic_RAG.ipynb) | [13-multimodal-agent](13-multimodal-agent.md) |
| [all_rag_techniques/HyDe_Hypothetical_Document_Embedding.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/HyDe_Hypothetical_Document_Embedding.ipynb) | [07-query-enhancement](07-query-enhancement.md) |
| [all_rag_techniques/HyPE_Hypothetical_Prompt_Embeddings.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/HyPE_Hypothetical_Prompt_Embeddings.ipynb) | [07-query-enhancement](07-query-enhancement.md) |
| [all_rag_techniques/Microsoft_GraphRag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/Microsoft_GraphRag.ipynb) | [12-graph](12-graph.md) |
| [all_rag_techniques/adaptive_retrieval.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/adaptive_retrieval.ipynb) | [10-corrective](10-corrective.md) |
| [all_rag_techniques/choose_chunk_size.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/choose_chunk_size.ipynb) | [04-chunking](04-chunking.md) |
| [all_rag_techniques/context_enrichment_window_around_chunk.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/context_enrichment_window_around_chunk.ipynb) | [08-context](08-context.md) |
| [all_rag_techniques/context_enrichment_window_around_chunk_with_llamaindex.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/context_enrichment_window_around_chunk_with_llamaindex.ipynb) | [08-context](08-context.md) |
| [all_rag_techniques/contextual_chunk_headers.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/contextual_chunk_headers.ipynb) | [04-chunking](04-chunking.md) |
| [all_rag_techniques/contextual_compression.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/contextual_compression.ipynb) | [08-context](08-context.md) |
| [all_rag_techniques/crag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/crag.ipynb) | [10-corrective](10-corrective.md) |
| [all_rag_techniques/dartboard.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/dartboard.ipynb) | [06-fusion-reranking](06-fusion-reranking.md) |
| [all_rag_techniques/document_augmentation.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/document_augmentation.ipynb) | [07-query-enhancement](07-query-enhancement.md) |
| [all_rag_techniques/document_parsing_with_mistral_ocr.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/document_parsing_with_mistral_ocr.ipynb) | [03-ingestion](03-ingestion.md) |
| [all_rag_techniques/explainable_retrieval.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/explainable_retrieval.ipynb) | [08-context](08-context.md) |
| [all_rag_techniques/fusion_retrieval.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/fusion_retrieval.ipynb) | [06-fusion-reranking](06-fusion-reranking.md) |
| [all_rag_techniques/fusion_retrieval_with_llamaindex.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/fusion_retrieval_with_llamaindex.ipynb) | [06-fusion-reranking](06-fusion-reranking.md) |
| [all_rag_techniques/graph_rag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/graph_rag.ipynb) | [12-graph](12-graph.md) |
| [all_rag_techniques/graph_rag_local_attribution.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/graph_rag_local_attribution.ipynb) | [12-graph](12-graph.md) |
| [all_rag_techniques/graphrag_with_milvus_vectordb.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/graphrag_with_milvus_vectordb.ipynb) | [12-graph](12-graph.md) |
| [all_rag_techniques/hierarchical_indices.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/hierarchical_indices.ipynb) | [11-hierarchy-memory](11-hierarchy-memory.md) |
| [all_rag_techniques/json_rag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/json_rag.ipynb) | [03-ingestion](03-ingestion.md) |
| [all_rag_techniques/light_rag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/light_rag.ipynb) | [12-graph](12-graph.md) |
| [all_rag_techniques/local_rag_huggingface_faiss.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/local_rag_huggingface_faiss.ipynb) | [05-baseline](05-baseline.md) |
| [all_rag_techniques/memorag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/memorag.ipynb) | [11-hierarchy-memory](11-hierarchy-memory.md) |
| [all_rag_techniques/multi_faceted_filtering.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/multi_faceted_filtering.ipynb) | [06-fusion-reranking](06-fusion-reranking.md) |
| [all_rag_techniques/multi_model_rag_with_captioning.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/multi_model_rag_with_captioning.ipynb) | [13-multimodal-agent](13-multimodal-agent.md) |
| [all_rag_techniques/multi_model_rag_with_colpali.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/multi_model_rag_with_colpali.ipynb) | [13-multimodal-agent](13-multimodal-agent.md) |
| [all_rag_techniques/proposition_chunking.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/proposition_chunking.ipynb) | [04-chunking](04-chunking.md) |
| [all_rag_techniques/query_transformations.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/query_transformations.ipynb) | [07-query-enhancement](07-query-enhancement.md) |
| [all_rag_techniques/raptor.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/raptor.ipynb) | [11-hierarchy-memory](11-hierarchy-memory.md) |
| [all_rag_techniques/relevant_segment_extraction.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/relevant_segment_extraction.ipynb) | [08-context](08-context.md) |
| [all_rag_techniques/reliable_rag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/reliable_rag.ipynb) | [10-corrective](10-corrective.md) |
| [all_rag_techniques/reranking.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/reranking.ipynb) | [06-fusion-reranking](06-fusion-reranking.md) |
| [all_rag_techniques/reranking_with_llamaindex.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/reranking_with_llamaindex.ipynb) | [06-fusion-reranking](06-fusion-reranking.md) |
| [all_rag_techniques/retrieval_with_feedback_loop.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/retrieval_with_feedback_loop.ipynb) | [10-corrective](10-corrective.md) |
| [all_rag_techniques/self_rag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/self_rag.ipynb) | [10-corrective](10-corrective.md) |
| [all_rag_techniques/semantic_chunking.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/semantic_chunking.ipynb) | [04-chunking](04-chunking.md) |
| [all_rag_techniques/simple_csv_rag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_csv_rag.ipynb) | [03-ingestion](03-ingestion.md) |
| [all_rag_techniques/simple_csv_rag_with_llamaindex.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_csv_rag_with_llamaindex.ipynb) | [03-ingestion](03-ingestion.md) |
| [all_rag_techniques/simple_rag.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_rag.ipynb) | [05-baseline](05-baseline.md) |
| [all_rag_techniques/simple_rag_with_llamaindex.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques/simple_rag_with_llamaindex.ipynb) | [05-baseline](05-baseline.md) |
| [evaluation/define_evaluation_metrics.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/evaluation/define_evaluation_metrics.ipynb) | [09-evaluation](09-evaluation.md) |
| [evaluation/end-2-end_rag_evaluation.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/evaluation/end-2-end_rag_evaluation.ipynb) | [09-evaluation](09-evaluation.md) |
| [evaluation/evaluation_deep_eval.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/evaluation/evaluation_deep_eval.ipynb) | [09-evaluation](09-evaluation.md) |
| [evaluation/evaluation_grouse.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/evaluation/evaluation_grouse.ipynb) | [09-evaluation](09-evaluation.md) |
| [evaluation/open-rag-eval-example.ipynb](https://github.com/NirDiamant/RAG_Techniques/blob/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/evaluation/open-rag-eval-example.ipynb) | [09-evaluation](09-evaluation.md) |

## 21 份可运行脚本与独立仓库

目录 [all_rag_techniques_runnable_scripts/](https://github.com/NirDiamant/RAG_Techniques/tree/2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6/all_rag_techniques_runnable_scripts) 收录了 21 份命令行版 Python 脚本，均已在提交 `2fd7bd0f` 中完成 LangChain 1.x 与 `gpt-5.4-mini` 迁移并通过 `notebook-smoke.yml` 字节码编译检查：

`adaptive_retrieval.py`、`choose_chunk_size.py`、`context_enrichment_window_around_chunk.py`、`contextual_compression.py`、`crag.py`、`document_augmentation.py`、`explainable_retrieval.py`、`fusion_retrieval.py`、`graph_rag.py`、`hierarchical_indices.py`、`HyDe_Hypothetical_Document_Embedding.py`、`HyPE_Hypothetical_Prompt_Embeddings.py`、`light_rag.py`、`multi_faceted_filtering.py`、`query_transformations.py`、`raptor.py`、`reranking.py`、`retrieval_with_feedback_loop.py`、`self_rag.py`、`semantic_chunking.py`、`simple_rag.py`。

独立仓库 [Controllable RAG Agent](https://github.com/NirDiamant/Controllable-RAG-Agent) 不计入上述 47 份 Notebook，使用时需单独核对提交版本、依赖与许可条款。
