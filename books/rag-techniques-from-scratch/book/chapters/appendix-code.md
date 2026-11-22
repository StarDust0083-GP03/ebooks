# 附录 C：配套教学实现与样例数据

本附录收录全书可离线运行的零依赖教学代码与语料快照（Python 3.10+ 标准库，无需第三方依赖或 API Key）。完整 20 项单元测试位于仓库 `examples/test_mini_rag.py`，在根目录运行：

```bash
python3 examples/mini_rag.py
python3 examples/evaluate.py
python3 -m unittest discover -s examples -v
```

## 教学语料快照（`examples/documents.jsonl`）

```json
{"id":"callback-v2:0","doc_id":"callback","version":"v2","position":0,"text":"支付回调处理顺序：先验签，再核对商户号、订单号、金额和币种，核对成功后才更新支付状态。","source":"教学支付规范 v2，回调流程第1段","scope":"public","active":true}
{"id":"callback-v2:1","doc_id":"callback","version":"v2","position":1,"text":"支付回调幂等以渠道流水号和事件类型为键。重复通知不重复发货；状态更新成功后发送业务通知，通知失败进入重试队列。","source":"教学支付规范 v2，回调流程第2段","scope":"public","active":true}
{"id":"callback-v1:0","doc_id":"callback","version":"v1","position":0,"text":"支付回调可以先更新订单状态，再异步验签。支付回调处理以订单号作为幂等键。","source":"教学支付规范 v1，已废弃反例","scope":"public","active":false}
{"id":"refund-v2:0","doc_id":"refund","version":"v2","position":0,"text":"退款失败后保留退款单原状态和失败原因。先查询渠道结果，再决定是否重试，不得仅凭超时再次发起新退款。","source":"教学退款规范 v2，第1段","scope":"public","active":true}
{"id":"refund-v2:1","doc_id":"refund","version":"v2","position":1,"text":"退款申请单使用独立幂等键。人工处理必须保留审批记录，系统不自动提高操作者权限。","source":"教学退款规范 v2，第2段","scope":"public","active":true}
{"id":"reconcile-v1:0","doc_id":"reconcile","version":"v1","position":0,"text":"对账任务比较渠道账单与内部支付流水，按渠道流水号关联，分别标记漏单、状态差异和金额差异。","source":"教学对账说明，第1段","scope":"public","active":true}
{"id":"owner-v1:0","doc_id":"owner","version":"v1","position":0,"text":"支付回调模块由结算组维护。结算组的技术负责人见团队职责表。","source":"教学模块目录，第1段","scope":"public","active":true}
{"id":"team-v1:0","doc_id":"team","version":"v1","position":0,"text":"结算组的技术负责人是陈禾，负责评审与故障协调；此记录不代表批准任何真实退款。","source":"教学团队职责表，第1段","scope":"public","active":true}
{"id":"alice-v1:0","doc_id":"alice-note","version":"v1","position":0,"text":"Alice 的私人支付排查笔记：临时商户信息仅本人可读。","source":"教学私人笔记","scope":"alice","active":true}
{"id":"bob-v1:0","doc_id":"bob-note","version":"v1","position":0,"text":"Bob 的私人支付排查笔记：支付回调验签和幂等的内部草稿，仅 Bob 可读。","source":"教学私人笔记","scope":"bob","active":true}
```

## 核心检索与纠错模型（`examples/mini_rag.py`）

```python
"""Offline retrieval lab: BM25, lexical cosine, RRF, block-aware splitting,
HyPE prompt proxies, CRAG bounded routing, and scoped context windows.

Original zero-dependency teaching implementation for Python 3.10+.
No semantic embeddings, model API, or natural-language answer generation.
"""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re


@dataclass(frozen=True)
class Chunk:
    id: str
    doc_id: str
    version: str
    position: int
    text: str
    source: str
    scope: str = "public"
    active: bool = True

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in
                   (self.id, self.doc_id, self.version, self.text, self.source, self.scope)):
            raise ValueError("chunk strings must be non-empty")
        if type(self.position) is not int or self.position < 0:
            raise ValueError("position must be a nonnegative integer")
        if type(self.active) is not bool:
            raise ValueError("active must be a boolean")


@dataclass(frozen=True)
class PromptProxy:
    proxy_id: str
    parent_id: str
    question: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in
                   (self.proxy_id, self.parent_id, self.question)):
            raise ValueError("proxy fields must be non-empty strings")


def tokenize(text):
    """English terms and overlapping Chinese bigrams; not a semantic encoder."""
    tokens = []
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
            if len(part) == 1:
                tokens.append(part)
        else:
            tokens.append(part)
    return tokens


def split_text(text, size=400, overlap=80):
    if type(size) is not int or type(overlap) is not int or not 0 <= overlap < size:
        raise ValueError("require integer 0 <= overlap < size")
    return [text[i:i + size] for i in range(0, len(text), size - overlap)]


def block_aware_split(blocks, max_chars=400):
    """Chunk structured OCR/layout blocks without detaching tables from captions.

    Each item in `blocks` is a `(block_type, text)` pair. A `table` block stays
    atomic (never sliced mid-row) and stays paired with an immediately preceding
    `caption` block even when the combined unit exceeds `max_chars`.
    """
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    units = []
    i = 0
    while i < len(blocks):
        btype, text = blocks[i]
        text = text.strip()
        if not text:
            i += 1
            continue
        if btype == "caption" and i + 1 < len(blocks) and blocks[i + 1][0] == "table":
            table_text = blocks[i + 1][1].strip()
            units.append(("table_group", f"{text}\n{table_text}"))
            i += 2
            continue
        units.append((btype, text))
        i += 1

    chunks, buf = [], []
    used = 0
    for utype, utext in units:
        if utype in ("table", "table_group"):
            if buf:
                chunks.append("\n\n".join(buf))
                buf, used = [], 0
            chunks.append(utext)
            continue
        cost = len(utext) + (2 if buf else 0)
        if buf and used + cost > max_chars:
            chunks.append("\n\n".join(buf))
            buf, used = [utext], len(utext)
        else:
            buf.append(utext)
            used += cost
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def load_chunks(path):
    return [Chunk(**json.loads(line)) for line in Path(path).read_text().splitlines()
            if line.strip()]


def rrf(rankings, constant=60):
    if constant <= 0:
        raise ValueError("RRF constant must be positive")
    scores = defaultdict(float)
    for ranking in rankings:
        seen = set()
        for rank, cid in enumerate(dict.fromkeys(ranking), start=1):
            if cid not in seen:
                scores[cid] += 1 / (constant + rank)
                seen.add(cid)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


def _score_corpus(query, items):
    """Compute BM25 and lexical cosine rankings over `(id, text)` pairs."""
    q = Counter(tokenize(query))
    if not q or not items:
        return [], []
    counts = {item_id: Counter(tokenize(text)) for item_id, text in items}
    lengths = {item_id: sum(tf.values()) for item_id, tf in counts.items()}
    average = sum(lengths.values()) / len(items) or 1
    df = Counter(t for tf in counts.values() for t in tf)
    bm25, cosine = [], []
    qnorm = math.sqrt(sum(v * v for v in q.values()))
    for item_id, _ in items:
        tf = counts[item_id]
        score = 0.0
        for term in q:
            n = tf[term]
            if n:
                idf = math.log(1 + (len(items) - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * n * 2.5 / (n + 1.5 * (0.25 + 0.75 * lengths[item_id] / average))
        if score > 0:
            bm25.append((item_id, score))
        norm = math.sqrt(sum(v * v for v in tf.values()))
        similarity = sum(q[t] * tf[t] for t in q) / (qnorm * norm) if norm else 0
        if similarity > 0:
            cosine.append((item_id, similarity))
    bm25.sort(key=lambda x: (-x[1], x[0]))
    cosine.sort(key=lambda x: (-x[1], x[0]))
    return bm25, cosine


class Retriever:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        if len({c.id for c in self.chunks}) != len(self.chunks):
            raise ValueError("duplicate chunk ID")
        locations = [(c.doc_id, c.version, c.position) for c in self.chunks]
        if len(set(locations)) != len(locations):
            raise ValueError("duplicate document/version/position")

    def visible(self, user):
        if not isinstance(user, str) or not user.strip() or user == "public":
            raise ValueError("user must be a non-empty demo identity, not 'public'")
        # Demo identity comes from CLI, not authentication. Do not expose as an API.
        return [c for c in self.chunks if c.active and c.scope in ("public", user)]

    def search(self, query, user="alice", mode="fusion", k=3):
        if type(k) is not int or not 1 <= k <= 100:
            raise ValueError("k must be 1..100")
        if mode not in ("bm25", "cosine", "fusion"):
            raise ValueError("unknown mode")
        if not isinstance(query, str) or len(query) > 2000:
            raise ValueError("query must be a string of at most 2000 characters")
        docs = self.visible(user)  # Filter before statistics, scoring, and model input.
        bm25, cosine = _score_corpus(query, [(c.id, c.text) for c in docs])
        if not bm25 and not cosine:
            return []
        ranked = {"bm25": bm25, "cosine": cosine}.get(mode)
        if ranked is None:
            ranked = rrf([[cid for cid, _ in bm25], [cid for cid, _ in cosine]])
        by_id = {c.id: c for c in docs}
        return [(by_id[cid], score) for cid, score in ranked[:k]]

    def expand(self, hits, user="alice", radius=1):
        if type(radius) is not int or not 0 <= radius <= 5:
            raise ValueError("radius must be 0..5")
        visible = self.visible(user)
        by_id = {c.id: c for c in visible}
        result = []
        seen = set()
        for candidate, _ in hits:
            center = by_id.get(candidate.id)
            if center is None:
                continue
            neighbors = sorted((c for c in visible if c.doc_id == center.doc_id
                                and c.version == center.version
                                and abs(c.position - center.position) <= radius),
                               key=lambda c: c.position)
            for chunk in neighbors:
                if chunk.id not in seen:
                    seen.add(chunk.id)
                    result.append(chunk)
        return result


class PromptIndex:
    """Offline HyPE-style proxy index: match query against index-time questions,
    then map back to authorized parent chunks with per-parent deduplication.
    """

    def __init__(self, retriever, proxies):
        self.retriever = retriever
        self.proxies = list(proxies)
        if len({p.proxy_id for p in self.proxies}) != len(self.proxies):
            raise ValueError("duplicate proxy ID")
        known = {c.id for c in retriever.chunks}
        for p in self.proxies:
            if p.parent_id not in known:
                raise ValueError(f"unknown parent chunk: {p.parent_id}")

    def search(self, query, user="alice", k=3):
        if type(k) is not int or not 1 <= k <= 100:
            raise ValueError("k must be 1..100")
        visible = {c.id: c for c in self.retriever.visible(user)}
        allowed_proxies = [p for p in self.proxies if p.parent_id in visible]
        bm25, cosine = _score_corpus(query, [(p.proxy_id, p.question) for p in allowed_proxies])
        if not bm25 and not cosine:
            return []
        ranked = rrf([[pid for pid, _ in bm25], [pid for pid, _ in cosine]])
        by_pid = {p.proxy_id: p for p in allowed_proxies}
        parent_hits, seen = [], set()
        for pid, score in ranked:
            parent_id = by_pid[pid].parent_id
            if parent_id not in seen:
                seen.add(parent_id)
                parent_hits.append((visible[parent_id], score))
                if len(parent_hits) >= k:
                    break
        return parent_hits


def crag_route(retriever, query, user="alice", rewrites=(),
               min_score=0.015, strong_score=0.030, max_attempts=2, k=2):
    """Bounded corrective retrieval state machine for internal specifications.

    States:
    - correct: top score >= strong_score -> deliver evidence directly
    - ambiguous: min_score <= top score < strong_score -> retry with next rewrite
    - incorrect: top score < min_score or attempts exhausted -> refuse without guessing
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if not 0 < min_score <= strong_score:
        raise ValueError("require 0 < min_score <= strong_score")
    candidates = [query, *rewrites][:max_attempts]
    trace = []
    best_hits = []
    best_top = 0.0
    for idx, q in enumerate(candidates, start=1):
        hits = retriever.search(q, user=user, mode="fusion", k=k)
        top = hits[0][1] if hits else 0.0
        state = "correct" if top >= strong_score else ("ambiguous" if top >= min_score else "incorrect")
        trace.append({"attempt": idx, "query": q, "state": state,
                      "top_score": round(top, 6), "ids": [c.id for c, _ in hits]})
        if top > best_top:
            best_top, best_hits = top, hits
        if state == "correct":
            return {"status": "evidence_ready", "verdict": "correct",
                    "hits": hits, "trace": trace}
    if best_top >= min_score:
        return {"status": "evidence_ready", "verdict": "ambiguous",
                "hits": best_hits, "trace": trace}
    return {"status": "insufficient_evidence", "verdict": "incorrect",
            "hits": [], "trace": trace}


def pack_context(chunks, budget=1200):
    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a nonnegative character count")
    blocks, selected, seen = [], [], set()
    used = 0
    for chunk in chunks:
        if chunk.id in seen:
            continue
        seen.add(chunk.id)
        block = f"[{chunk.id}] {chunk.source}\n{chunk.text}"
        cost = len(block) + (2 if blocks else 0)
        if used + cost <= budget:
            blocks.append(block)
            selected.append(chunk.id)
            used += cost
    return "\n\n".join(blocks), selected


def retrieval_metrics(expected, actual):
    gold, ranked = set(expected), list(dict.fromkeys(actual))
    if not gold:
        raise ValueError("gold evidence must not be empty")
    recall = len(gold.intersection(ranked)) / len(gold)
    reciprocal = next((1 / i for i, cid in enumerate(ranked, 1) if cid in gold), 0.0)
    return {"recall": recall, "mrr": reciprocal, "complete": gold.issubset(ranked)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="支付回调验签和幂等如何处理")
    parser.add_argument("--user", default="alice")
    parser.add_argument("--mode", choices=["bm25", "cosine", "fusion"], default="fusion")
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--window", type=int, default=0)
    parser.add_argument("--budget", type=int, default=1200)
    parser.add_argument("--data", type=Path, default=Path(__file__).with_name("documents.jsonl"))
    args = parser.parse_args()
    retriever = Retriever(load_chunks(args.data))
    hits = retriever.search(args.query, args.user, args.mode, args.k)
    expanded = retriever.expand(hits, args.user, args.window)
    context, ids = pack_context(expanded, args.budget)
    print(json.dumps({"mode": args.mode, "retrieved": [c.id for c, _ in hits],
                      "injected": ids, "context_characters": len(context)}, ensure_ascii=False, indent=2))
    print(context or "没有可交付证据。此示例不生成答案。")


if __name__ == "__main__":
    main()
```

## 固定题集对照评估（`examples/evaluate.py`）

```python
"""Fixed teaching questions. This is not a production benchmark."""
import json
from pathlib import Path
from mini_rag import Retriever, load_chunks, pack_context, retrieval_metrics

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("回调处理顺序", ["callback-v2:0"]),
    ("支付回调验签和幂等如何处理", ["callback-v2:0", "callback-v2:1"]),
    ("重复通知如何处理", ["callback-v2:1"]),
    ("退款失败后如何处理", ["refund-v2:0"]),
    ("退款申请单的幂等键", ["refund-v2:1"]),
    ("回调模块技术负责人是谁", ["owner-v1:0", "team-v1:0"]),
]


def run():
    retriever = Retriever(load_chunks(ROOT / "examples/documents.jsonl"))
    report = []
    for mode, k, radius in [("bm25", 2, 0), ("cosine", 2, 0),
                            ("fusion", 2, 0), ("fusion", 1, 1)]:
        rows = []
        for query, expected in CASES:
            hits = retriever.search(query, user="alice", mode=mode, k=k)
            chunks = retriever.expand(hits, user="alice", radius=radius)
            context, injected = pack_context(chunks, 1200)
            candidates = [c.id for c, _ in hits]
            rows.append({"query": query, "gold": expected, "retrieved": candidates,
                         "injected": injected, "context_characters": len(context),
                         "retrieval": retrieval_metrics(expected, candidates),
                         "delivery": retrieval_metrics(expected, injected)})
        summary = {
            "candidate_recall": sum(r["retrieval"]["recall"] for r in rows) / len(rows),
            "delivery_recall": sum(r["delivery"]["recall"] for r in rows) / len(rows),
            "complete_rate": sum(r["delivery"]["complete"] for r in rows) / len(rows),
            "mrr": sum(r["retrieval"]["mrr"] for r in rows) / len(rows),
        }
        result = {"mode": mode, "k": k, "radius": radius, "summary": summary, "cases": rows}
        report.append(result)
        print(f"{mode:7} k={k} radius={radius} " + json.dumps(summary, ensure_ascii=False))
    path = ROOT / "sources/offline-evaluation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"scope": "6 fixed teaching questions; no LLM", "runs": report},
                               ensure_ascii=False, indent=2) + "\n")
    print("Saved sources/offline-evaluation.json")


if __name__ == "__main__":
    run()
```
