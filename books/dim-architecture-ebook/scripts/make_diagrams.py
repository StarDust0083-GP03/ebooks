#!/usr/bin/env python3
"""Generate the 6 inline SVG architecture diagrams for dim-architecture-ebook.

The viewBox is sized to the A5 text column (420pt) so labels stay readable in
print; on screen the same SVG scales cleanly. Inline SVG keeps every figure
theme-aware via CSS custom properties, searchable, and valid in EPUB 3.
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
    size: float = 8.6,
    weight: int = 400,
    fill: str = "var(--ink)",
    anchor: str = "start",
    family: str = FONT,
    opacity: float = 1,
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
        f'opacity="{opacity}">{escape(s)}</text>'
    )


def box(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    lines: tuple[str, ...] | list[str] = (),
    accent: bool = False,
    forest: bool = False,
    title_size: float = 9.8,
) -> str:
    if forest:
        stroke = "var(--forest)"
        sw = 1.3
    elif accent:
        stroke = "var(--amber)"
        sw = 1.3
    else:
        stroke = "var(--line)"
        sw = 0.85
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2.5" '
        f'fill="var(--paper-2)" stroke="{stroke}" stroke-width="{sw}"/>'
    ]
    out.append(t(x + 8, y + 15.5, title, title_size, 600))
    for i, line in enumerate(lines):
        out.append(t(x + 8, y + 29 + i * 12.5, line, 8.2, 400, "var(--muted)"))
    return "".join(out)


def badge(
    x: float,
    y: float,
    label: str,
    color: str = "var(--forest)",
    w: float = 64,
) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="15" rx="7.5" fill="none" '
        f'stroke="{color}" stroke-width="0.9"/>'
        + t(x + w / 2, y + 10.7, label, 8.0, 600, color, "middle")
    )


def arrow(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    dashed: bool = False,
    color: str = "var(--muted)",
) -> str:
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="1.05"{dash} marker-end="url(#arrow)"/>'
    )


def svg(height: int, title: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
        f'width="{W}" height="{height}" role="img" aria-label="{escape(title)}">\n'
        f"<title>{escape(title)}</title>\n"
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker></defs>\n'
        f"{body}\n</svg>"
    )


def workspace_scopes() -> str:
    b = [t(8, 12, "28 个 Cargo 成员 · 依赖自上而下受 xtask-check 硬门禁约束", 8.2, 600, "var(--muted)")]
    # Top half: Crate layers
    layers = [
        ("协议与入口层", "dimcode · dim-cli · dim-acp · dim-client · web", "薄客户端 / 网关", False, False),
        ("宿主与业务装配层", "dim-app-server · aimux · codemode · goal · scheduler", "进程装配 / RPC v3", False, False),
        ("能力插件层", "dim-agent-capabilities（20 个分支 Capability）", "Loop / 权限 / 树", True, False),
        ("运行内核与契约层", "dim-agent-runtime（rutis 0.23）→ dim-agent-types", "总线 / 13 保留域", False, True),
    ]
    for i, (title, crates, note, accent, forest) in enumerate(layers):
        y = 22 + i * 48
        b.append(box(8, y, 294, 40, title, [crates], accent=accent, forest=forest, title_size=9.4))
        b.append(t(308, y + 23, note, 8.0, 400, "var(--muted)"))
        if i < len(layers) - 1:
            b.append(arrow(155, y + 40, 155, y + 48, color="var(--line)"))

    # Divider
    b.append('<line x1="8" y1="220" x2="412" y2="220" stroke="var(--line)" stroke-width="0.7" stroke-dasharray="3 3"/>')
    b.append(t(8, 236, "三层作用域树（ADR 002 / ADR 015）：按状态所有者放置部件", 8.2, 600, "var(--ink)"))

    scopes = [
        (8, 246, 404, 38, "Process 根作用域（58 个服务）", "SessionStore · Provider · Mcp · SkillsCatalog · Settings · PartRegistry", False, False),
        (26, 294, 386, 38, "Session 作用域（31 个服务，一个主体）", "TimelineTree · ToolRegistry · PromptRegistry · WorkspaceTools", False, True),
        (44, 342, 368, 38, "Branch 作用域（22 个服务，一条行为线）", "Timeline · Dispatcher · Loop · InputQueue · Permission · Approval", True, False),
    ]
    for x, y, w, h, title, desc, accent, forest in scopes:
        b.append(box(x, y, w, h, title, [desc], accent=accent, forest=forest, title_size=9.2))
    b.append(arrow(22, 284, 22, 313, color="var(--muted)"))
    b.append(arrow(40, 332, 40, 361, color="var(--muted)"))
    b.append(t(8, 396, "可见性：向父级注入服务；向下经 treeCommands；兄弟分支不直接互调。", 8.2, 400, "var(--amber)"))
    return svg(406, "工作区四层依赖与三层作用域树", "".join(b))


def time_bus_dispatch() -> str:
    b = [t(8, 12, "写入与分发分离：已提交（热窗口）不等于已发布（已落盘）", 8.2, 600, "var(--muted)")]
    steps = [
        ("1. append / append_batch_registered", "能力提交 EventDraft，核心禁止扩展写入 13 个 RESERVED_DOMAINS", False, False),
        ("2. before 同步变形链（提交锁内，仅 Rust）", "逐个变换草稿并记录 TraceStep；任意钩子报错则回退原草稿提交", True, False),
        ("3. 分配单调 sequence + 登记回调", "写锁内登记关联状态；登记失败整批中止，不留脏锚点", False, False),
        ("4. 热窗口暂存 + SQLite WAL 写入轮", "synchronous=FULL（macOS 开 fullfsync）；输出行按 20ms 节流合批", False, True),
        ("5. 推进发布水位 published()", "落盘返回后推进水位；外部读取与 Dispatcher 只消费已发布事件", False, True),
        ("6. Dispatcher 严格按序派发", "订阅方经 consume() 先暂存再重建，只处理高于截点的实时事实", True, False),
    ]
    for i, (title, desc, accent, forest) in enumerate(steps):
        y = 22 + i * 54
        b.append(box(8, y, 404, 42, title, [desc], accent=accent, forest=forest, title_size=9.3))
        if i < len(steps) - 1:
            b.append(arrow(210, y + 42, 210, y + 54, color="var(--muted)"))

    b.append(t(8, 356, "副作用发生在总线外：重启与重载按同一协议从截点重建，绝不重放外部动作。", 8.2, 400, "var(--forest)"))
    return svg(368, "时间总线写入、落盘与按序派发链路", "".join(b))


def branch_tree_cap() -> str:
    b = [t(8, 12, "起点 main 恒可开线；默认分支沿 branch/default 移动并保护其祖先链", 8.2, 600, "var(--muted)")]

    # Root node
    b.append(box(128, 24, 164, 42, "main（起点 / Root）", ["恒可开工具线 · 永不结束"], forest=True, title_size=9.3))

    # Children level 1
    b.append(arrow(175, 66, 92, 92, color="var(--forest)"))
    b.append(arrow(210, 66, 210, 92, color="var(--muted)"))
    b.append(arrow(245, 66, 336, 92, color="var(--muted)", dashed=True))

    b.append(box(8, 92, 140, 48, "分支 A（工具线）", ["受保护祖先链", "不计入配额 · 禁止 end"], forest=True, title_size=9.0))
    b.append(box(156, 92, 114, 48, "分支 C（工具线）", ["非保护路径", "占 1 个工具线名额"], accent=True, title_size=9.0))
    b.append(box(278, 92, 134, 48, "插件线（Goal 评估）", ["source=host", "不可选主线 · 不占名额"], title_size=9.0))

    # Child level 2 under A: current default branch
    b.append(arrow(78, 140, 78, 166, color="var(--forest)"))
    b.append(box(8, 166, 176, 50, "分支 B（当前默认分支）", ["可开工具线 · 省略 branchId 路由至此", "切回 main 后 A 与 B 一起恢复计额"], forest=True, title_size=9.0))

    # Child level 2 under B: newly opened tool line
    b.append(arrow(184, 191, 224, 191, color="var(--amber)"))
    b.append(box(224, 166, 188, 50, "分支 D（B 开的工具线）", ["新开子线不在保护链上", "预留时在树锁内扣 1 个配额"], accent=True, title_size=9.0))

    # Keyed state box at bottom
    b.append('<line x1="8" y1="230" x2="412" y2="230" stroke="var(--line)" stroke-width="0.7" stroke-dasharray="3 3"/>')
    b.append(t(8, 245, "三条独立键控状态块（压缩后各自独立重放，互不覆盖）", 8.2, 600, "var(--ink)"))
    keys = [
        (8, 254, 128, 46, "branch:line", ["本线身份与主线状态", "切换默认分支时重申"]),
        (146, 254, 128, 46, "branch:roster", ["本线开启的活跃工具线", "子线继承时自动退役"]),
        (284, 254, 128, 46, "branch:parent", ["父线最新 head 与读游标", "只传位置不传父线正文"]),
    ]
    for x, y, w, h, title, lines in keys:
        b.append(box(x, y, w, h, title, lines, title_size=8.8))

    b.append(t(8, 316, "默认上限 DEFAULT_MAX_OPEN_LINES = 4；权威事实 branch/default 只写在 main 上。", 8.1, 400, "var(--amber)"))
    return svg(328, "分支时间线、保护路径配额与三键状态", "".join(b))


def rutis_data_face() -> str:
    b = [t(8, 12, "rutis 0.23.0（协议 5）：每个插件占 1 行进程配置，服务面统一为数据面", 8.2, 600, "var(--muted)")]

    # Left: Rust host
    b.append(box(8, 24, 178, 124, "Rust 宿主内核", [
        "1. 进程根与共享目录（111 服务）",
        "2. 75 个跨语言 data 数据面",
        "3. 36 个 internal(reason) 私有面",
        "4. Session / Branch = 普通插件",
        "   （ADR 015 mark_scope）",
        "5. xtask types 生成 .d.ts 契约",
    ], forest=True, title_size=9.3))

    # Right top: Node / TS
    b.append(box(226, 24, 186, 56, "Node / TypeScript 运行时", [
        "@arcships/rutis 0.23 · Cordis 4",
        "workflow / light-ocr / 自写插件",
        "通过 partRegistry 登记定义",
    ], accent=True, title_size=9.1))

    # Right bottom: Python
    b.append(box(226, 92, 186, 56, "Python 运行时（py:<module>）", [
        "独立进程会话 · isolate 表解析",
        "相同 inject / use / provide",
        "与 Rust/TS 互调同一数据面",
    ], title_size=9.1))

    # Bidirectional arrows
    b.append(arrow(186, 48, 226, 48, color="var(--amber)"))
    b.append(arrow(226, 62, 186, 62, color="var(--amber)"))
    b.append(arrow(186, 114, 226, 114, color="var(--muted)"))
    b.append(arrow(226, 128, 186, 128, color="var(--muted)"))

    # Data Face rules strip
    b.append(box(8, 162, 404, 72, "数据面契约（HostDispatch at host_key(name) + 共享目录项）", [
        "参数、返回值、错误均为 serde 数据（{code, message}）；句柄与 &mut 不跨语言",
        "对象拆成 id + 操作（execHost / fsHost / store / timelineStore / toolRegistry）",
        "单帧 16 MiB 上限，超长走 0.23 chunk 分片（单片 8 MiB，默认上限 256 MiB）",
        "禁止手写第二层适配逻辑：Rust 本地绑定与跨语言线协议共用同一所有者实现",
    ], accent=True, title_size=9.2))

    b.append(t(8, 250, "注意：before 回调在提交锁内同步运行，禁止跨进程调用，只限进程内 Rust 注册。", 8.1, 400, "var(--muted)"))
    return svg(262, "rutis 跨语言运行时与数据面边界", "".join(b))


def approval_permission() -> str:
    b = [t(8, 12, "ADR 003 / 009 / 010 / 012 / 018：三条职责边界互不越权", 8.2, 600, "var(--muted)")]

    # Lane 1: Agent tool decision
    b.append(box(8, 24, 260, 128, "路径 A：模型决策触发的工具调用", [
        "1. Permission 评估三预设（ADR 018）：",
        "   read-only / workspace-write / full-access",
        "2. 叠加固定文件事实（FileAccessFacts）",
        "   + SessionGrants（仅在 Ask 放行，不压 Deny）",
        "   + CommandRules（deny 永远胜出）+ Hooks 建议",
        "3. 需询问 → Approval 写 approval/request",
        "   首条有效 approval/respond 胜出；中断写 cancelled",
    ], forest=True, title_size=9.2))

    # Lane 2: HostPlatform enforcement
    b.append(box(278, 24, 134, 128, "执行隔离（可选）", [
        "HostPlatform 只执行",
        "不再二次否决准入",
        "（ADR 009）",
        "沙箱默认关闭",
        "（ADR 010 opt-in）",
        "macOS seatbelt",
        "Linux bwrap",
    ], accent=True, title_size=9.1))
    b.append(arrow(268, 86, 278, 86, color="var(--forest)"))

    # Lane 3: Configured commands (ADR 003)
    b.append(box(8, 166, 404, 62, "路径 B：配置驱动的外部执行（ADR 003：不走工具审批）", [
        "覆盖对象：MCP 服务器进程、Hook 命令、Workflow 定义、插件包",
        "规则：项目目录（.mcp.json / .dimcode）来源默认禁用，只有用户本地配置可启用",
        "安全策略不写入 Timeline；回放、恢复与分叉绝不恢复或放大既有权限",
    ], title_size=9.2))

    b.append(t(8, 244, "exec 不接受模型传入 sandbox/network 参数；模型只能接受环境，不能自选沙箱。", 8.1, 400, "var(--amber)"))
    return svg(256, "工具审批、文件事实、配置启用与执行隔离边界", "".join(b))


def compaction_replay() -> str:
    b = [t(8, 12, "持久摘要事实与可丢弃快照分离：加速失败永不丢失已完成摘要", 8.2, 600, "var(--muted)")]

    b.append(box(8, 24, 192, 56, "1. context/prepare", [
        "固定本次 through 截点与历史前缀",
        "预算超限时在准备内触发压缩",
        "单飞追踪，支持 stop/steer 取消",
    ], title_size=9.1))

    b.append(arrow(200, 52, 220, 52, color="var(--muted)"))

    b.append(box(220, 24, 192, 56, "2. context/compaction（权威）", [
        "随原子批次落盘并发布",
        "含 cursor、summary 与基线",
        "不可变事实，不内嵌恢复快照",
    ], forest=True, title_size=9.1))

    b.append(arrow(316, 80, 316, 102, color="var(--forest)"))

    b.append(box(8, 102, 404, 56, "3. RecoverySnapshot（可丢弃派生缓存 · timeline_snapshots）", [
        "解释器 dim/context-projection/v4 编码 ProjectionCheckpoint + SHA-256 摘要",
        "锚定已发布的 completed 压缩事件，记录 resume 事件位置与此前折叠状态",
        "构建或保存失败只打告警，不回滚摘要事实，也不重跑模型压缩",
    ], accent=True, title_size=9.1))

    # Branching on read
    b.append(arrow(110, 158, 110, 182, color="var(--forest)"))
    b.append(arrow(310, 158, 310, 182, color="var(--amber)", dashed=True))

    b.append(box(8, 182, 194, 58, "校验通过：增量续读", [
        "验证锚点、摘要与 resume 位置",
        "从 resume 起分页折叠后续事实",
        "跳过压缩前大段历史与请求正文",
    ], forest=True, title_size=9.1))

    b.append(box(218, 182, 194, 58, "缺失 / 损坏 / 越界：安全回退", [
        "自动忽略异常快照，不吞存储错误",
        "从祖先链起点完整重放事实",
        "仍采用已提交的 compaction 摘要",
    ], accent=True, title_size=9.1))

    b.append(t(8, 256, "上下文输入只有两条合法通道：已采用的输入（input/consumed）与工具结果。", 8.1, 400, "var(--muted)"))
    return svg(268, "上下文压缩、SHA-256 恢复快照与回退重放", "".join(b))


DIAGRAMS = {
    "workspace-scopes": workspace_scopes,
    "time-bus-dispatch": time_bus_dispatch,
    "branch-tree-cap": branch_tree_cap,
    "rutis-data-face": rutis_data_face,
    "approval-permission": approval_permission,
    "compaction-replay": compaction_replay,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Remove any stale template SVGs not in DIAGRAMS
    for old in OUT.glob("*.svg"):
        if old.stem not in DIAGRAMS:
            old.unlink()
    for name, fn in DIAGRAMS.items():
        path = OUT / f"{name}.svg"
        path.write_text(fn(), encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)}")


if __name__ == "__main__":
    main()
