#!/usr/bin/env python3
"""Generate 6 theme-aware inline SVG diagrams for the RAG engineering ebook.

Each diagram uses a 540pt viewBox so labels stay sharp on screen and in A5 print,
uses a unique SVG marker ID so multiple diagrams never collide in single-file HTML,
and runs a text-width bounds check on every rendered label.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "wiki" / "assets" / "diagrams"
FONT = "'IBM Plex Sans','Noto Sans CJK SC',system-ui,sans-serif"
MONO = "'IBM Plex Mono','SFMono-Regular',Consolas,monospace"
W = 540


def est_width(text: str, size: float) -> float:
    w = 0.0
    for ch in text:
        if ord(ch) > 127:
            w += size * 1.04
        elif ch in "il1.,:;!|' ":
            w += size * 0.38
        else:
            w += size * 0.62
    return w


def check_bounds(x: float, text: str, size: float, anchor: str, max_x: float = W - 6, min_x: float = 6) -> None:
    tw = est_width(text, size)
    if anchor == "start":
        left, right = x, x + tw
    elif anchor == "middle":
        left, right = x - tw / 2, x + tw / 2
    else:
        left, right = x - tw, x
    if left < min_x - 2 or right > max_x + 2:
        raise ValueError(
            f"Text overflow ({left:.1f}..{right:.1f} vs {min_x}..{max_x}): {text!r}"
        )


def t(x, y, s, size=9.5, weight=400, fill="var(--ink)", anchor="start", family=FONT, max_x=W - 6, min_x=6):
    check_bounds(x, s, size, anchor, max_x=max_x, min_x=min_x)
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>'
    )


def box(x, y, w, h, title, lines=(), accent=False, forest=False, title_size=10.5):
    stroke = "var(--amber)" if accent else ("var(--forest)" if forest else "var(--line)")
    sw = 1.4 if (accent or forest) else 0.85
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="var(--paper-2)" '
        f'stroke="{stroke}" stroke-width="{sw}"/>'
    ]
    out.append(t(x + 10, y + 17, title, title_size, 600, max_x=x + w - 8, min_x=x + 4))
    for i, line in enumerate(lines):
        out.append(t(x + 10, y + 33 + i * 14, line, 9, 400, "var(--muted)", max_x=x + w - 8, min_x=x + 4))
    return "".join(out)


def badge(x, y, label, color="var(--forest)", w=66):
    check_bounds(x + w / 2, label, 8.6, "middle", max_x=x + w - 2, min_x=x + 2)
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="17" rx="8.5" fill="none" stroke="{color}" stroke-width="0.95"/>'
        + f'<text x="{x + w / 2}" y="{y + 11.8}" font-family="{FONT}" font-size="8.6" '
        + f'font-weight="600" fill="{color}" text-anchor="middle">{escape(label)}</text>'
    )


def arrow(x1, y1, x2, y2, marker_id, dashed=False, color="var(--muted)"):
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.15"'
        f'{dash} marker-end="url(#{marker_id})"/>'
    )


def svg(name, height, title, body):
    mid = f"arr-{name}"
    body = body.replace("__ARROW__", mid)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
        f'width="{W}" height="{height}" role="img" aria-label="{escape(title)}">\n'
        f'<title>{escape(title)}</title>\n'
        f'<defs><marker id="{mid}" viewBox="0 0 10 10" refX="8.5" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,1 L10,5 L0,9 z" fill="var(--muted)"/></marker></defs>\n'
        f'{body}\n</svg>'
    )


def pipeline_chain():
    b = [
        t(10, 14, "写入链（离线构建与原子发布）", 9.5, 600, "var(--forest)"),
        box(10, 22, 118, 54, "原文与版本", ["PDF / CSV / JSON", "doc_id + version"], forest=False),
        arrow(128, 49, 144, 49, "__ARROW__"),
        box(144, 22, 122, 54, "解析与切分", ["OCR 4.1 块结构", "保留来源定位"]),
        arrow(266, 49, 282, 49, "__ARROW__"),
        box(282, 22, 116, 54, "多表示索引", ["倒排 / 向量", "代理问题与摘要"]),
        arrow(398, 49, 414, 49, "__ARROW__"),
        box(414, 22, 116, 54, "完整快照发布", ["校验后再切指针", "旧快照随时回退"], forest=True),
        f'<line x1="10" y1="92" x2="530" y2="92" stroke="var(--line)" stroke-width="0.7" stroke-dasharray="3 3"/>',
        arrow(472, 76, 472, 112, "__ARROW__", dashed=True),
        t(466, 106, "只读固定快照", 8.5, 400, "var(--muted)", anchor="end"),
        t(10, 108, "查询链（先授权，再召回，只交付注入证据）", 9.5, 600, "var(--amber)"),
        box(10, 116, 118, 60, "1. 权限与视图", ["服务端会话身份", "active + scope"], accent=True),
        arrow(128, 146, 144, 146, "__ARROW__"),
        box(144, 116, 122, 60, "2. 召回与融合", ["BM25 + 向量", "按 ID 对齐 RRF"]),
        arrow(266, 146, 282, 146, "__ARROW__"),
        box(282, 116, 116, 60, "3. 扩展与打包", ["同文档同版邻块", "按预算计入标签"]),
        arrow(398, 146, 414, 146, "__ARROW__"),
        box(414, 116, 116, 60, "4. 回答与引用", ["仅引用 injected", "证据不足即拒答"], forest=True),
        t(10, 198, "红琥珀框为访问边界：未授权与废弃版本必须在计分前剔除，不能交给模型兜底。", 9, 400, "var(--amber)"),
        t(10, 214, "日志同步记录 retrieved 与 injected 两组 ID，用来区分漏召回与预算截断。", 8.8, 400, "var(--muted)"),
    ]
    return svg("pipeline-chain", 226, "写入链与查询链的职责边界", "".join(b))


def chunking_boundary():
    b = [
        t(10, 14, "方式 A：纯字符切分（拆散规则与表格）", 9.5, 600, "var(--amber)"),
        box(10, 22, 162, 54, "Chunk 1（被截断）", ["先验签，再核对金额", "表 1：支付事件对照"], accent=True),
        box(182, 22, 168, 54, "Chunk 2（丢失表头）", ["| pay.succeeded | 核对", "回调幂等以渠道流水号..."], accent=True),
        box(360, 22, 170, 54, "典型故障", ["表格与标题拆在两块", "验签和幂等只召回一半"], accent=True),
        f'<line x1="10" y1="90" x2="530" y2="90" stroke="var(--line)" stroke-width="0.7"/>',
        t(10, 108, "方式 B：结构感知切分 + 小块召回 + 边界邻域扩展", 9.5, 600, "var(--forest)"),
        box(10, 116, 162, 60, "表组原子块", ["caption + table 绑定", "OCR 4.1 blocks 标签"], forest=True),
        box(182, 116, 168, 60, "小块精确召回", ["callback-v2:0（验签）", "带章节检索标题"], forest=True),
        arrow(350, 146, 366, 146, "__ARROW__"),
        box(366, 116, 164, 60, "受控邻域补全", ["同 doc_id + 同 version", "再次校验 ACL 与预算"], forest=True),
        t(10, 196, "块是召回单位，不必等同阅读单位；跨权限或跨版本的相邻块绝不拼进同一窗口。", 8.8, 400, "var(--muted)"),
    ]
    return svg("chunking-boundary", 208, "字符切分失真与结构感知邻域扩展对比", "".join(b))


def hybrid_rrf_rerank():
    b = [
        t(10, 14, "多路召回 → 按稳定 ID 融合 → 有限候选池重排", 9.5, 600, "var(--ink)"),
        box(10, 26, 126, 52, "BM25 词面召回", ["擅长接口名与错误码", "返回 ID 排序表 A"]),
        box(10, 90, 126, 52, "稠密向量召回", ["擅长同义与口语问法", "返回 ID 排序表 B"]),
        arrow(136, 52, 162, 74, "__ARROW__"),
        arrow(136, 116, 162, 92, "__ARROW__"),
        box(162, 42, 154, 86, "按 chunk.id 融合", ["每路先去重再计名次", "score += 1 / (c + rank)", "禁止按数组下标相加"], accent=True),
        arrow(316, 85, 336, 85, "__ARROW__"),
        box(336, 42, 102, 86, "重排器", ["Cross-Encoder", "或结构化 LLM", "仅重排候选池"]),
        arrow(438, 85, 456, 85, "__ARROW__"),
        box(456, 42, 74, 86, "交付", ["Top-k", "预算打包", "证据完整"], forest=True),
        t(10, 164, "关键边界：重排无法补回从未进入候选池的证据；多条件问题不能只留单条最高分。", 8.8, 400, "var(--amber)"),
        t(10, 180, "静态核对提示：上游 fusion_retrieval.ipynb 存在按数组位置合并风险，上线务必按 ID 对齐。", 8.6, 400, "var(--muted)"),
    ]
    return svg("hybrid-rrf-rerank", 192, "混合检索、RRF 身份对齐与重排链路", "".join(b))


def query_routing():
    b = [
        t(10, 14, "用户口语问法如何对齐到规范术语（三条路径对比）", 9.5, 600, "var(--ink)"),
        box(10, 26, 118, 122, "原始用户问题", ["同一个通知来了", "两次还会不会", "重复发货？", "保留原始限定条件"]),
        arrow(128, 46, 152, 46, "__ARROW__"),
        arrow(128, 88, 152, 88, "__ARROW__"),
        arrow(128, 130, 152, 130, "__ARROW__"),
        box(152, 24, 198, 42, "1. 查询改写与拆解（查询时）", ["补充术语并拆子问题，双路按 ID 合并"]),
        box(152, 72, 198, 42, "2. HyDE 假设文档（查询时）", ["先写假设段落再搜原文，严禁当证据"], accent=True),
        box(152, 120, 198, 42, "3. HyPE 问题代理（入库时）", ["入库预生问题向量，命中回映射父块"], forest=True),
        arrow(350, 45, 376, 78, "__ARROW__"),
        arrow(350, 93, 376, 93, "__ARROW__"),
        arrow(350, 141, 376, 108, "__ARROW__"),
        box(376, 56, 154, 72, "真实授权原文", ["callback-v2:0", "callback-v2:1", "按 parent_id 去重"], forest=True),
        t(10, 182, "HyDE 消耗查询时延迟；HyPE 把生成成本前移到入库，文档更新时代理必须同步重建。", 8.8, 400, "var(--muted)"),
    ]
    return svg("query-routing", 194, "查询改写、HyDE 与 HyPE 的对齐路径对比", "".join(b))


def crag_state_machine():
    b = [
        t(10, 14, "纠错检索状态机（内部规范默认禁止无依据自由发挥）", 9.5, 600, "var(--ink)"),
        box(10, 28, 122, 62, "1. 检索与质检", ["authorized_scope", "相关性 + 充分性"]),
        arrow(132, 59, 156, 40, "__ARROW__"),
        arrow(132, 59, 156, 96, "__ARROW__"),
        arrow(132, 59, 156, 152, "__ARROW__"),
        box(156, 16, 154, 46, "correct（证据充分）", ["进入打包与引用校验"], forest=True),
        box(156, 72, 154, 46, "ambiguous（证据偏弱）", ["受控改写重试（限次数）"], accent=True),
        box(156, 128, 154, 46, "incorrect（未命中）", ["不凭参数知识硬猜"], accent=True),
        arrow(310, 39, 344, 39, "__ARROW__"),
        box(344, 16, 186, 46, "返回 evidence_ready", ["逐句绑定 injected_ids"], forest=True),
        arrow(310, 95, 344, 95, "__ARROW__"),
        box(344, 72, 186, 46, "attempt < max_attempts", ["重新检索；权限范围不变"]),
        arrow(437, 118, 437, 128, "__ARROW__", dashed=True),
        arrow(310, 151, 344, 151, "__ARROW__"),
        box(344, 128, 186, 46, "insufficient_evidence", ["明确拒答，不默认上网泄密"], accent=True),
        t(10, 194, "与开放问答不同：内部规则助手遇到无证据时，少答优于用模型常识或公网结果硬凑。", 8.8, 400, "var(--amber)"),
    ]
    return svg("crag-state-machine", 206, "CRAG 与受控纠错检索状态机", "".join(b))


def production_loop():
    b = [
        t(10, 14, "生产运行回路：权限、版本、评估与升级门禁", 9.5, 600, "var(--ink)"),
        box(10, 26, 162, 66, "全路径访问控制", ["会话认证非请求自报", "检索 / 邻域 / 代理 / 缓存", "统一继承 scope 与版本"], accent=True),
        arrow(172, 59, 192, 59, "__ARROW__"),
        box(192, 26, 162, 66, "版本化快照与清理", ["新索引校验后原子切换", "删文同步清向量与摘要", "缓存键绑定版本与权限"], forest=True),
        arrow(354, 59, 374, 59, "__ARROW__"),
        box(374, 26, 156, 66, "超时预算与故障隔离", ["总时限向下分配阶段", "区分无资料与依赖故障", "注入文本视为不可信数据"]),
        arrow(452, 92, 452, 116, "__ARROW__"),
        box(192, 116, 338, 60, "固定题集回归 + 升级烟雾门禁", ["追踪 candidate vs delivery Recall 与完整性", "模型停服 / 框架大版本升级先过离线回归与语法 CI"], forest=True),
        arrow(192, 146, 91, 146, "__ARROW__", dashed=True),
        arrow(91, 146, 91, 92, "__ARROW__", dashed=True),
        t(18, 134, "失败样本回流", 8.6, 600, "var(--muted)"),
        t(18, 148, "定位再改一层", 8.6, 400, "var(--muted)"),
        t(10, 196, "上线后的故障多半不在检索公式，而在权限漏检、派生残留、模型停服或超时级联。", 8.8, 400, "var(--muted)"),
    ]
    return svg("production-loop", 208, "生产环境权限、版本快照与评估回归回路", "".join(b))


DIAGRAMS = {
    "pipeline-chain": pipeline_chain,
    "chunking-boundary": chunking_boundary,
    "hybrid-rrf-rerank": hybrid_rrf_rerank,
    "query-routing": query_routing,
    "crag-state-machine": crag_state_machine,
    "production-loop": production_loop,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in DIAGRAMS.items():
        content = fn()
        path = OUT / f"{name}.svg"
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)} ({len(content)} bytes)")


if __name__ == "__main__":
    main()
