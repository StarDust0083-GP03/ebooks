# 上游源码快照记录

- 仓库：`https://github.com/NirDiamant/RAG_Techniques`
- 固定提交：`2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6`
- 上游提交时间：`2026-11-02T00:36:12Z`
- 本地核对日期：`2026-11-22`
- 许可证：自定义非商业协议（见上游 `LICENSE`），商业使用受许可材料需原作者事先书面授权。

## 仓库规模实测

| 目录或类型 | 数量 | 说明 |
| --- | --- | --- |
| `all_rag_techniques/*.ipynb` | 42 | 技术与框架变体 Notebook |
| `evaluation/*.ipynb` | 5 | 评估示例 Notebook（另有 `evalute_rag.py` 脚本） |
| 合计 `.ipynb` 文件 | 47 | 详见 `sources/notebooks.json` 与附录 A |
| `all_rag_techniques_runnable_scripts/*.py` | 21 | 可运行独立脚本 |
| `.github/workflows/*.yml` | 3 | `local-test.yml`、`notebook-smoke.yml`、`qodo-request-changes.yml` |
| `tests/*.py` | 2 | `conftest.py`、`test_imports.py` |

## 相对上一快照（`8e9febb8` → `2fd7bd0f`）的核心变动

1. **Mistral OCR 4 升级至 OCR 4.1（#214，`2fd7bd0f`）**：`all_rag_techniques/document_parsing_with_mistral_ocr.ipynb` 更新到 Mistral OCR 4.1（`mistral-ocr-latest`），演示三段对照：默认 PyPDF 破坏表格布局；换成 OCR 4.1 恢复表格但字符切分器仍把表格与标题拆开；按 OCR 4.1 返回的 `blocks` 结构标签做块感知切分，保持表格与标题原子绑定。仓库自带三份预计算 JSON（`two_column_paper.json`、`scanned_page.json`、`erp_table_b1.json`），无密钥即可离线运行全部单元。
2. **LangChain 1.x 与 LlamaIndex 0.14 迁移（#212，`4fea9188`）**：
   - LangChain 1.x 将旧链和压缩检索器拆到 `langchain-classic`（如 `langchain_classic.chains.RetrievalQA`、`langchain_classic.retrievers.ContextualCompressionRetriever`），文档与提示词迁至 `langchain_core`，切分器独立为 `langchain_text_splitters`，废弃 `langchain_core.pydantic_v1` 改用标准 `pydantic`；自定义检索器改为实现 `_get_relevant_documents(self, query, *, run_manager=None)`，调用侧统一改为 `.invoke(query)`（同步修改了 `helper_functions.py` 第 143 行与 `reranking.ipynb`）；`crag.ipynb` 中 `DuckDuckGoSearchResults` 迁至 `langchain_community.tools` 并显式传入 `output_format="json"`。
   - LlamaIndex 0.14 将 `BM25Retriever` 从 `llama_index.legacy` 迁至 `llama_index.retrievers.bm25`；新版 `node.text` 变为只读，清洗文本改为调用 `node.set_content(...)`；由于 LlamaIndex 默认的 `gpt-3.5-turbo` 已于 `2026-10-23` 停服，Notebook 必须显式配置 `Settings.llm = OpenAI(model=LLM_MODEL)`。
3. **10 月 31 日全仓审计（#211，`aa49b841` 与 #209，`49134940`）**：
   - 模型升级：Notebook 与脚本中的旧模型（`gpt-3.5-turbo`、`gpt-4o-mini`、`gpt-4o`、`gpt-4`）集中迁移至 `LLM_MODEL = "gpt-5.4-mini"`（Notebook 代码单元共出现 33 处）与 `EVAL_MODEL = "gpt-5.4"`（6 处）。
   - DeepEval `GEval` 评委模型陷阱：`evaluation/evaluation_deep_eval.ipynb` 注释指出 `GEval` 按评委模型的 token logprobs 加权打分，DeepEval 只向白名单模型请求 logprobs（`gpt-5.4` 支持，`gpt-5.4-mini` 不支持），否则退化为粗糙整数分，简短正确答案可能被判 `0.0`。
   - HyPE 语法错误修复与无密钥 CI：`all_rag_techniques/HyPE_Hypothetical_Prompt_Embeddings.ipynb` 此前在 `FAISS(...)` 调用中漏写逗号；新增 `.github/workflows/notebook-smoke.yml`，用 `nbformat` 与 IPython `TransformerManager` 编译全部 47 份 Notebook 代码单元并字节码检查所有 `.py` 文件，无需 API Key 即可拦住语法错误。
4. **仍然保留的静态代码观察**：
   - `all_rag_techniques/fusion_retrieval.ipynb` 的 `fusion_retrieval()` 仍分别取 `all_docs = vectorstore.similarity_search("", ...)`、`bm25.get_scores(...)` 和按相关性排序的 `vector_results`，未按稳定文档 ID 显式对齐即按数组下标加权合并。
