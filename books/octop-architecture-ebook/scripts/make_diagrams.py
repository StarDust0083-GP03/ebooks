#!/usr/bin/env python3
"""Generate the 6 inline SVG diagrams used by the Octop ebook.

The viewBox width is 420pt (matching the A5 print column) so labels stay
legible in print and scale cleanly on screen. All colors reference CSS custom
properties so light/dark themes work without rebuilding.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "wiki" / "assets" / "diagrams"
FONT = "'IBM Plex Sans','Noto Sans CJK SC',system-ui,sans-serif"
MONO = "'IBM Plex Mono','SFMono-Regular',Consolas,monospace"
W = 420


def t(
    x: float,
    y: float,
    s: str,
    size: float = 8.8,
    weight: int = 400,
    fill: str = "var(--ink)",
    anchor: str = "start",
    family: str = FONT,
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>'
    )


def box(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    lines: tuple[str, ...] | list[str] = (),
    *,
    accent: bool = False,
    forest: bool = False,
    title_size: float = 10,
) -> str:
    stroke = "var(--amber)" if accent else ("var(--forest)" if forest else "var(--line)")
    sw = 1.3 if (accent or forest) else 0.8
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2.5" '
        f'fill="var(--paper-2)" stroke="{stroke}" stroke-width="{sw}"/>'
    ]
    out.append(t(x + 9, y + 16, title, title_size, 600))
    for i, line in enumerate(lines):
        out.append(t(x + 9, y + 30 + i * 12.5, line, 8.4, 400, "var(--muted)"))
    return "".join(out)


def badge(x: float, y: float, label: str, color: str = "var(--forest)", w: float = 56) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="15" rx="7.5" fill="none" '
        f'stroke="{color}" stroke-width="0.9"/>'
        + t(x + w / 2, y + 10.7, label, 8.2, 600, color, "middle")
    )


def arrow(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    dashed: bool = False,
    color: str = "var(--muted)",
) -> str:
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="1.1"{dash} marker-end="url(#arrow)"/>'
    )


def svg(height: int, title: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
        f'width="{W}" height="{height}" role="img" aria-label="{escape(title)}">\n'
        f"<title>{escape(title)}</title>\n"
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker></defs>\n'
        f"{body}\n"
        "</svg>"
    )


def host_layers() -> str:
    b = [t(8, 11, "依赖严格向内流动：传输层调域层，域层绝不反向引用 API/CLI", 8.2, 600, "var(--muted)")]
    rows = [
        ("对外入口", "React Dashboard · Click CLI · IM 通道 · Cron", "HTTP/WS/stdio", False, False),
        ("API 适配层", "FastAPI routers · JWT / MFA / Setup 门禁", "仅校验与映射", False, False),
        ("宿主领域层 (infra/)", "AgentManager · GlobalProcessor · Gateway · CronManager", "核心编排", True, False),
        ("四大独立运行库", "octop-harness · octop-gateway · octop-memory · octop-browser", "PyPI 固定版本", False, True),
        ("数据与执行平面", "SQLite/PG 控制面 · 知识库/技能包 · BackendWorkspace", "按类分治", False, False),
    ]
    for i, (title, detail, tag, accent, forest) in enumerate(rows):
        y = 22 + i * 58
        b.append(box(8, y, 332, 44, title, [detail], accent=accent, forest=forest))
        color = "var(--amber)" if accent else ("var(--forest)" if forest else "var(--muted)")
        b.append(badge(346, y + 14, tag, color, 66))
        if i < len(rows) - 1:
            b.append(arrow(174, y + 44, 174, y + 58, color="var(--line)"))
    b.append(t(8, 322, "唯一允许同时 import infra/server 与 api/app 的模块：src/octop/launch.py", 8.4, 400, "var(--amber)"))
    return svg(334, "Octop 五层模块化单体边界", "".join(b))


def identity_boundary() -> str:
    b = [t(8, 11, "共享专家只开放使用权，不开放私人线程与配置权", 8.2, 600, "var(--muted)")]
    # Two users on left
    b.append(box(8, 22, 114, 48, "用户 A (owner)", ["user_id = 7", "可读写配置与对话"], forest=True, title_size=9.5))
    b.append(box(8, 86, 114, 48, "用户 B (shared)", ["user_id = 9", "可用专家，不可改配置"], title_size=9.5))
    # Shared agent in center
    b.append(box(148, 48, 112, 60, "共享专家", ["agent_id = research", "owner = 7, shared = 1", "require_agent_row()"], accent=True, title_size=9.5))
    b.append(arrow(122, 46, 148, 66))
    b.append(arrow(122, 110, 148, 90))
    # Threads on right
    b.append(box(286, 20, 126, 48, "私有线程 thread-A", ["user=7, contributed=0", "仅 owner 可见"], forest=True, title_size=9.2))
    b.append(box(286, 78, 126, 48, "共享对话 thread-S", ["user=7, contributed=1", "thread_visible_to() 放行"], accent=True, title_size=9.2))
    b.append(box(286, 136, 126, 48, "用户 B 线程 thread-B", ["user=9, contributed=0", "独立 session_key 绑定"], title_size=9.2))
    b.append(arrow(260, 68, 286, 44))
    b.append(arrow(260, 78, 286, 102))
    b.append(arrow(260, 92, 286, 160))
    # Bottom rule bar
    b.append(f'<line x1="8" y1="198" x2="412" y2="198" stroke="var(--line)" stroke-width="0.7"/>')
    b.append(t(8, 214, "session_key 格式：<agent_id>:dashboard:<user_id>:dm（入口绑定可变）", 8.4, 400, "var(--ink)", family=MONO))
    b.append(t(8, 230, "鉴权顺序：1. JWT → 2. require_agent_row → 3. resolve_thread_id / thread_visible_to", 8.4, 400, "var(--amber)"))
    return svg(242, "四种标识与共享专家的线程隔离边界", "".join(b))


def storage_planes() -> str:
    b = [t(8, 11, "ADR 002 + ADR 003：驱动选定后，控制面与内容平面同步切换", 8.2, 600, "var(--muted)")]
    # Column headers
    b.append(t(12, 30, "数据类别", 8.6, 700, "var(--ink)"))
    b.append(t(116, 30, "SQLite 单机模式", 8.6, 700, "var(--forest)"))
    b.append(t(266, 30, "PostgreSQL 集群模式", 8.6, 700, "var(--amber)"))
    b.append('<line x1="8" y1="36" x2="412" y2="36" stroke="var(--ink)" stroke-width="1"/>')
    rows = [
        ("控制面与历史", "octop.db (WAL + IMMEDIATE)", "共享 PG（thread_messages）"),
        ("技能包源文件", "~/.octop/skill-packages/", "PG 文件表（ADR 003）"),
        ("知识库原文/向量", "knowledge/ + index.sqlite", "PG 文件表 + pgvector"),
        ("Checkpoint", "每专家 memory.sqlite", "共享表（孤儿清理仅统计）"),
        ("长期记忆", "本地 memory.sqlite", "同 DSN 按 namespace 隔离"),
        ("专家工作区", "本机目录（默认）", "S3 / COS / TOS / PG / Sandbox"),
    ]
    for i, (kind, sq, pg) in enumerate(rows):
        y = 42 + i * 32
        b.append(t(12, y + 16, kind, 8.6, 600, "var(--ink)"))
        b.append(t(116, y + 16, sq, 8.3, 400, "var(--muted)"))
        b.append(t(266, y + 16, pg, 8.3, 400, "var(--muted)"))
        b.append(f'<line x1="8" y1="{y + 26}" x2="412" y2="{y + 26}" stroke="var(--line)" stroke-width="0.6"/>')
    b.append(t(8, 250, "v1.0.3b5 起下线 history_v2，启动时由 retire_v2.py 单次迁回 thread_messages。", 8.4, 400, "var(--muted)"))
    b.append(t(8, 265, "v1.0.3b7 修复：gc_orphan_checkpoints 对 PG 永远只统计，绝不删其他智能体对话。", 8.4, 400, "var(--amber)"))
    return svg(278, "SQLite 与 PostgreSQL 六类数据平面分布", "".join(b))


def turn_pipeline() -> str:
    b = [t(8, 11, "一轮 WebSocket 对话在单进程宿主内的控制流与保护点", 8.2, 600, "var(--muted)")]
    steps = [
        ("1", "WS 接入与先订阅", "JWT 鉴权 → resolve_thread_id → 先 hub.subscribe 再 await steer", False),
        ("2", "统一入队", "prepare_dashboard_turn → InboundMessage → ChannelManager.enqueue", False),
        ("3", "路由与前置拦截", "GlobalProcessor：slash / HITL 恢复 / 过期模型回退 Auto / 知识检索", False),
        ("4", "运行图与中间件", "AgentManager.stream → ConsecutiveToolCall(40) · Failover · UI Offload", True),
        ("5", "记录与流式推送", "usage_tracker → history_tracker → 轨迹/产物去重 → WS / IM 投递", False),
        ("6", "断线与取消分离", "WS 断开仅注销订阅，turn 继续运行；显式 cancel 才发取消信号", True),
    ]
    for i, (num, title, desc, accent) in enumerate(steps):
        y = 22 + i * 48
        color = "var(--amber)" if accent else "var(--forest)"
        b.append(
            f'<circle cx="19" cy="{y + 19}" r="11" fill="var(--paper-2)" stroke="{color}" stroke-width="1"/>'
            + t(19, y + 22.3, num, 8.8, 700, color, "middle")
        )
        b.append(box(38, y, 374, 38, title, [desc], accent=accent, title_size=9.5))
        if i < len(steps) - 1:
            b.append(arrow(19, y + 30, 19, y + 48, color="var(--line)"))
    b.append(t(8, 322, "注意：连续相同工具调用超过上限会立即 jump_to=end，正常换工具或改参数会重置计数。", 8.3, 400, "var(--amber)"))
    return svg(334, "一次对话回合的六步处理链路", "".join(b))


def managed_acp_bridge() -> str:
    b = [t(8, 11, "两种跨边界能力：托管专家在本机拉外部进程，Bridge 把请求转发给对端实例", 8.1, 600, "var(--muted)")]
    # Top half: Managed Expert (ACP)
    b.append(box(8, 22, 404, 114, "托管专家（kind = managed · v1.0.3b6 / b7）", [], forest=True, title_size=9.8))
    b.append(box(18, 44, 116, 46, "Octop 宿主", ["session_modes.py", "ask / yolo 权限门"], title_size=9))
    b.append(box(156, 44, 116, 46, "外部 ACP Runner", ["OpenCode / Claude", "Codex / CodeBuddy"], accent=True, title_size=9))
    b.append(box(294, 44, 108, 46, "6 个回环 MCP", ["knowledge · agents", "files · cron · plugins"], title_size=8.8))
    b.append(arrow(134, 67, 156, 67))
    b.append(arrow(272, 67, 294, 67))
    b.append(t(18, 108, "通过 session/set_mode 下发模式；通过本地 HTTP 回环把 Octop 工具暴露给外部子进程。", 8.2, 400, "var(--muted)"))
    b.append(t(18, 123, "技能包按 runner 目录（.claude/.opencode/.codex/skills）建软链或 Junction。", 8.2, 400, "var(--muted)"))
    # Bottom half: Instance Bridge
    b.append(box(8, 150, 404, 104, "云端协同 Bridge（bridge:{conn_id}:{remote_agent_id}）", [], accent=True, title_size=9.8))
    b.append(box(18, 172, 116, 44, "本机 Octop", ["识别远端引用", "不复制专家与历史"], title_size=9))
    b.append(box(156, 172, 116, 44, "机机 WS 隧道", ["tunnel_policy.py", "默认拒绝未知路径"], forest=True, title_size=9))
    b.append(box(294, 172, 108, 44, "远端 Octop", ["远端登录态鉴权", "持有工作区与权威"], title_size=9))
    b.append(arrow(134, 194, 156, 194))
    b.append(arrow(272, 194, 294, 194))
    b.append(t(18, 234, "两边 user_id 不等价；远端资源可见性严格由对端连接账号决定，断桥绝不切本地同名专家。", 8.2, 400, "var(--muted)"))
    return svg(264, "托管专家 ACP 回环与 Bridge 跨实例桥接对比", "".join(b))


def cluster_lease() -> str:
    b = [t(8, 11, "v1.0.3b6 Helm 多副本：Cookie 粘滞管实时流，数据库同行租约管后台单活", 8.1, 600, "var(--muted)")]
    # Ingress
    b.append(box(130, 22, 160, 38, "反向代理 / Ingress", ["按 Cookie 粘滞（禁按来源 IP）"], accent=True, title_size=9.5))
    # Two replicas
    b.append(box(14, 82, 176, 74, "Octop 副本 A", ["内存专家 + 实时 WS 推送", "持有通道/Cron 租约（90s）", "独立本机盘：/data/.octop"], forest=True, title_size=9.5))
    b.append(box(230, 82, 176, 74, "Octop 副本 B", ["内存专家 + 实时 WS 推送", "每 25s 巡检过期租约接管", "独立本机盘（严禁 NFS 共写）"], title_size=9.5))
    b.append(arrow(182, 60, 102, 82))
    b.append(arrow(238, 60, 318, 82))
    # Shared PG
    b.append(box(14, 182, 392, 58, "共享 PostgreSQL 16 + pgvector", [
        "控制面 · 技能包源 · 知识库原文与向量 · 专家记忆 · 腾讯云凭证",
        "租约常量（infra/db/lease.py）：TTL 90s · 续约 30s · 巡检接管 25s · 退出主动释放",
    ], accent=True, title_size=9.5))
    b.append(arrow(102, 156, 156, 182))
    b.append(arrow(318, 156, 264, 182))
    b.append(t(8, 258, "工作区若留在容器临时盘会随 Pod 销毁；集群模式应配 PG / S3 / COS / TOS / OpenSandbox。", 8.2, 400, "var(--muted)"))
    return svg(270, "多副本集群的 Cookie 粘滞与数据库同行租约", "".join(b))


DIAGRAMS = {
    "host-layers": host_layers,
    "identity-boundary": identity_boundary,
    "storage-planes": storage_planes,
    "turn-pipeline": turn_pipeline,
    "managed-acp-bridge": managed_acp_bridge,
    "cluster-lease": cluster_lease,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in DIAGRAMS.items():
        path = OUT / f"{name}.svg"
        path.write_text(fn(), encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)}")


if __name__ == "__main__":
    main()
