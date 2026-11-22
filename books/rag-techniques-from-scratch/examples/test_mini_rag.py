import unittest
from pathlib import Path
from mini_rag import (
    Chunk,
    PromptIndex,
    PromptProxy,
    Retriever,
    block_aware_split,
    crag_route,
    load_chunks,
    pack_context,
    retrieval_metrics,
    rrf,
    split_text,
    tokenize,
)


def chunk(cid, doc="doc", version="v2", position=0, scope="public", active=True, text="支付回调验签"):
    return Chunk(cid, doc, version, position, text, "教学来源", scope, active)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.r = Retriever(load_chunks(Path(__file__).with_name("documents.jsonl")))

    def test_bm25_finds_evidence(self):
        self.assertEqual(self.r.search("回调处理顺序", mode="bm25", k=1)[0][0].id, "callback-v2:0")

    def test_chinese_tokenization(self):
        self.assertIn("验签", tokenize("支付回调验签"))
        self.assertIn("callback_v2", tokenize("callback_v2"))

    def test_filter_happens_before_scoring(self):
        for mode in ("bm25", "cosine", "fusion"):
            hits = self.r.search("支付回调验签和幂等", user="alice", mode=mode, k=100)
            self.assertNotIn("bob-v1:0", [c.id for c, _ in hits])
            self.assertNotIn("callback-v1:0", [c.id for c, _ in hits])
            visible_only = Retriever(self.r.visible("alice"))
            self.assertEqual(hits, visible_only.search("支付回调验签和幂等", "alice", mode, 100))

    def test_windows_keep_version_document_and_acl(self):
        r = Retriever([chunk("x", position=0), chunk("next", position=1),
                       chunk("old", version="v1", position=1),
                       chunk("other", doc="other", position=1),
                       chunk("secret", position=2, scope="bob")])
        result = r.expand([(r.chunks[0], 1)], "alice", 2)
        self.assertEqual([c.id for c in result], ["x", "next"])

    def test_unauthorized_center_cannot_expand(self):
        private = next(c for c in self.r.chunks if c.scope == "bob")
        self.assertEqual(self.r.expand([(private, 1)], "alice"), [])

    def test_rrf_joins_by_id(self):
        ranking = rrf([["a", "b"], ["b", "a"]])
        self.assertAlmostEqual(ranking[0][1], ranking[1][1])
        self.assertEqual([cid for cid, _ in ranking], ["a", "b"])
        self.assertEqual(rrf([["a", "a", "b"]]), rrf([["a", "b"]]))

    def test_pack_counts_headers_and_separators(self):
        docs = self.r.visible("alice")
        context, ids = pack_context(docs, budget=160)
        self.assertLessEqual(len(context), 160)
        self.assertTrue(ids)
        self.assertEqual(pack_context(docs, 0), ("", []))
        self.assertEqual(pack_context([docs[0], docs[0]], 1000)[1], [docs[0].id])

    def test_no_match_is_empty(self):
        self.assertEqual(self.r.search("xyzunfindable"), [])
        self.assertEqual(self.r.search(""), [])

    def test_invalid_parameters(self):
        for size, overlap in ((0, 0), (2, 2), (2, -1)):
            with self.assertRaises(ValueError):
                split_text("abcdef", size, overlap)
        for kwargs in ({"k": 0}, {"mode": "unknown"}, {"user": ""}, {"user": "public"}):
            with self.assertRaises(ValueError):
                self.r.search("支付", **kwargs)
        with self.assertRaises(ValueError):
            self.r.expand([], radius=-1)
        with self.assertRaises(ValueError):
            pack_context([], -1)
        with self.assertRaises(ValueError):
            rrf([["a"]], constant=0)

    def test_duplicate_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            Retriever([chunk("same"), chunk("same", doc="another")])
        with self.assertRaises(ValueError):
            Retriever([chunk("a"), chunk("b")])

    def test_split_offsets(self):
        self.assertEqual(split_text("abcdefghij", 4, 1), ["abcd", "defg", "ghij", "j"])

    def test_metrics_need_all_evidence(self):
        result = retrieval_metrics(["a", "b"], ["a", "c"])
        self.assertEqual(result, {"recall": 0.5, "mrr": 1.0, "complete": False})
        self.assertEqual(retrieval_metrics(["a"], ["a", "a"])["recall"], 1.0)

    def test_block_aware_split_keeps_table_with_caption(self):
        blocks = [
            ("heading", "支付事件对照"),
            ("paragraph", "以下列出各类回调事件的确认要求。"),
            ("caption", "表 1：支付回调事件与确认规则"),
            ("table", "| 事件 | 确认方式 |\n| --- | --- |\n| pay.succeeded | 验签并核对金额 |\n| refund.failed | 保留原状态并查渠道 |"),
            ("paragraph", "表后补充说明。"),
        ]
        chunks = block_aware_split(blocks, max_chars=40)
        table_chunks = [c for c in chunks if "表 1：支付回调事件与确认规则" in c]
        self.assertEqual(len(table_chunks), 1)
        self.assertIn("pay.succeeded", table_chunks[0])
        self.assertIn("refund.failed", table_chunks[0])

    def test_block_aware_split_rejects_nonpositive_limit(self):
        with self.assertRaises(ValueError):
            block_aware_split([("paragraph", "abc")], max_chars=0)

    def test_prompt_index_maps_colloquial_query_to_parent_and_dedups(self):
        proxies = [
            PromptProxy("p1", "callback-v2:1", "同一个通知来了两次会不会重复发货"),
            PromptProxy("p2", "callback-v2:1", "同一笔支付消息推送两遍怎么办"),
            PromptProxy("p3", "refund-v2:0", "退款超时能不能直接重发"),
        ]
        idx = PromptIndex(self.r, proxies)
        hits = idx.search("同一个通知来了两次怎么办", user="alice", k=3)
        self.assertEqual([c.id for c, _ in hits], ["callback-v2:1"])

    def test_prompt_index_enforces_acl_and_active_view_before_scoring(self):
        proxies = [
            PromptProxy("p-old", "callback-v1:0", "能不能先改状态再异步验签"),
            PromptProxy("p-bob", "bob-v1:0", "Bob 的内部回调排查草稿在哪"),
            PromptProxy("p-ok", "callback-v2:0", "回调先验签还是先改状态"),
        ]
        idx = PromptIndex(self.r, proxies)
        alice_ids = [c.id for c, _ in idx.search("Bob 的内部回调排查草稿在哪", user="alice", k=3)]
        self.assertNotIn("bob-v1:0", alice_ids)
        self.assertEqual(idx.search("排查草稿", user="alice", k=3), [])
        self.assertEqual([c.id for c, _ in idx.search("排查草稿", user="bob", k=3)], ["bob-v1:0"])
        hits = idx.search("先改状态还是先验签", user="alice", k=3)
        self.assertEqual([c.id for c, _ in hits], ["callback-v2:0"])

    def test_prompt_index_rejects_unknown_parent_or_duplicate_id(self):
        with self.assertRaises(ValueError):
            PromptIndex(self.r, [PromptProxy("p1", "missing:0", "问题")])
        with self.assertRaises(ValueError):
            PromptIndex(self.r, [
                PromptProxy("dup", "callback-v2:0", "问题一"),
                PromptProxy("dup", "callback-v2:1", "问题二"),
            ])

    def test_crag_route_accepts_strong_hit_in_one_attempt(self):
        res = crag_route(self.r, "支付回调验签和幂等如何处理", user="alice",
                         rewrites=["冗余改写"], max_attempts=2)
        self.assertEqual(res["status"], "evidence_ready")
        self.assertEqual(res["verdict"], "correct")
        self.assertEqual(len(res["trace"]), 1)
        self.assertEqual([c.id for c, _ in res["hits"]], ["callback-v2:0", "callback-v2:1"])

    def test_crag_route_retries_on_miss_and_recovers_with_rewrite(self):
        res = crag_route(self.r, "xyzunfindable", user="alice",
                         rewrites=["重复通知如何处理"], max_attempts=2)
        self.assertEqual(res["status"], "evidence_ready")
        self.assertEqual(res["verdict"], "correct")
        self.assertEqual(len(res["trace"]), 2)
        self.assertEqual(res["trace"][0]["state"], "incorrect")
        self.assertEqual(res["hits"][0][0].id, "callback-v2:1")

    def test_crag_route_refuses_when_budget_exhausted_without_evidence(self):
        res = crag_route(self.r, "xyzunfindable", user="alice",
                         rewrites=["stillmissing", "neverreached"], max_attempts=2)
        self.assertEqual(res["status"], "insufficient_evidence")
        self.assertEqual(res["verdict"], "incorrect")
        self.assertEqual(res["hits"], [])
        self.assertEqual(len(res["trace"]), 2)


if __name__ == "__main__":
    unittest.main()
