#!/usr/bin/env python3
"""Generate the 6 theme-aware inline SVG diagrams for the Prime Agent ebook.

The viewBox width is 480pt so Chinese labels and code identifiers stay spacious
in A5 print and scale cleanly on desktop screens. Every diagram uses CSS custom
properties from reader.css (--ink, --muted, --paper-2, --line, --amber, --forest)
and runs an approximate text-width check to prevent clipped or overlapping labels.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "wiki" / "assets" / "diagrams"
FONT = "'IBM Plex Sans','Noto Sans CJK SC',system-ui,sans-serif"
MONO = "'IBM Plex Mono','SFMono-Regular',Consolas,monospace"
W = 480


def approx_text_width(s: str, size: float) -> float:
    """Conservative width estimate in pt for mixed CJK/ASCII strings."""
    width = 0.0
    for ch in s:
        if ord(ch) > 0x2E80:
            width += size * 1.02
        elif ch in " ilI1.,:;|'\"()[]":
            width += size * 0.36
        else:
            width += size * 0.58
    return width


def assert_fits(label: str, size: float, max_w: float, context: str) -> None:
    est = approx_text_width(label, size)
    if est > max_w:
        raise ValueError(
            f"Text overflow in {context}: {label!r} est={est:.1f}pt > max={max_w:.1f}pt"
        )


def t(x, y, s, size=9.2, weight=400, fill="var(--ink)", anchor="start", family=FONT, opacity=1):
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
        f'opacity="{opacity}">{escape(s)}</text>'
    )


def box(x, y, w, h, title, lines=(), accent=False, forest=False, title_size=10.5, mono_title=False):
    stroke = "var(--amber)" if accent else ("var(--forest)" if forest else "var(--line)")
    sw = 1.4 if (accent or forest) else 0.85
    assert_fits(title, title_size, w - 16, f"box title {title}")
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="var(--paper-2)" '
        f'stroke="{stroke}" stroke-width="{sw}"/>'
    ]
    out.append(t(x + 9, y + 17, title, title_size, 600, family=MONO if mono_title else FONT))
    for i, line in enumerate(lines):
        assert_fits(line, 8.8, w - 16, f"box line {line}")
        out.append(t(x + 9, y + 32 + i * 13, line, 8.8, 400, "var(--muted)"))
    return "".join(out)


def badge(x, y, label, color="var(--forest)", w=66):
    assert_fits(label, 8.4, w - 8, f"badge {label}")
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="16" rx="8" fill="none" '
        f'stroke="{color}" stroke-width="0.9"/>'
        + t(x + w / 2, y + 11.2, label, 8.4, 600, color, "middle")
    )


def arrow(x1, y1, x2, y2, dashed=False, color="var(--muted)"):
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="1.1"{dash} marker-end="url(#arrow)"/>'
    )


def poly_arrow(points: str, dashed=False, color="var(--muted)"):
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    return (
        f'<polyline points="{points}" fill="none" stroke="{color}" '
        f'stroke-width="1.1"{dash} marker-end="url(#arrow)"/>'
    )


def svg(height, title, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
        f'width="{W}" height="{height}" role="img" aria-label="{escape(title)}">\n'
        f"<title>{escape(title)}</title>\n"
        f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker></defs>\n'
        f"{body}\n"
        f"</svg>"
    )


def dual_layer_topology():
    """Diagram 1: 10 Rust Crates + Process Isolation + Python RLM Runtime."""
    b = [t(8, 12, "单向依赖（上层指向下层）与进程隔离边界", 8.6, 600, "var(--muted)")]
    # Row 1: Composition root & TUI client
    b.append(box(8, 22, 224, 48, "pa-cli (组合根)", ["prime-agent 二进制、参数解析、模式分发"], forest=True))
    b.append(box(248, 22, 224, 48, "pa-tui (终端界面)", ["仅依赖 pa-types，零引擎耦合，首帧无阻塞"], forest=True))
    b.append(arrow(232, 46, 248, 46, color="var(--muted)"))

    # Row 2: Supervisor + Per-session Worker process box
    b.append(
        '<rect x="8" y="88" width="464" height="128" rx="4" fill="none" '
        'stroke="var(--amber)" stroke-width="1.2" stroke-dasharray="5 3"/>'
    )
    b.append(t(16, 102, "pa-daemon 进程托管层（本地 Socket / 直接挂载 Ticket / ACP stdio）", 8.5, 600, "var(--amber)"))
    b.append(box(16, 110, 146, 94, "Supervisor 守护进程", ["监听 Unix Socket/Pipe", "管理 Worker 退避重启", "维护 RLM 账本与名册", "90s 热更新排空事务"], accent=True))
    b.append(box(176, 110, 142, 94, "Worker 进程 (每会话)", ["承载 pa-core 会话引擎", "append-only JSONL 落盘", "信箱 inbox.jsonl 管理", "事件流 50ms 合并广播"]))
    b.append(box(332, 110, 132, 94, "CPython REPL 内核", ["python -m rlm.repl", "单事件循环 + __main__", "bash/rlm/mcp/skills", "dill v2 快照恢复"], forest=True))
    b.append(arrow(162, 156, 176, 156))
    b.append(arrow(318, 156, 332, 156))
    b.append(arrow(120, 70, 88, 110))
    b.append(arrow(360, 70, 248, 110, dashed=True))

    # Row 3: Core libraries
    b.append(box(8, 234, 148, 52, "pa-core (会话引擎)", ["提示词、压缩、精炼、MCP", "工具注册、内核管理、门禁"]))
    b.append(box(166, 234, 148, 52, "pa-agent + pa-ai", ["Turn 循环、三层队列", "多供应商流式适配"]))
    b.append(box(324, 234, 148, 52, "pa-models + telemetry", ["模型目录缓存与回退", "异步无阻塞埋点上报"]))
    b.append(arrow(247, 204, 82, 234))

    # Row 4: Shared vocabulary leaf
    b.append(box(8, 302, 332, 42, "pa-types (唯一共享词表)", ["线协议帧、会话条目、AI 消息、目标与信箱类型，零工作区上游依赖"], mono_title=False))
    b.append(box(350, 302, 122, 42, "pa-perf (仅 CI)", ["PTY 基准与语料回放"]))
    b.append(arrow(82, 286, 120, 302))
    b.append(arrow(240, 286, 210, 302))
    b.append(arrow(398, 286, 290, 302))

    b.append(t(8, 362, "注：unsafe_code = forbid 覆盖全工作区；pa-tui 只消费 pa-types 帧，不链接 pa-core。", 8.5, 400, "var(--muted)"))
    return svg(374, "Prime Agent 十个 Rust Crate 与双层进程拓扑", "".join(b))


def rlm_repl_cycle():
    """Diagram 2: Protocol v3 REPL dual-channel I/O, host bridge, HeadTailBuffer."""
    b = [t(8, 12, "Protocol v3 换行分隔 JSON 协议与双通道隔离", 8.6, 600, "var(--muted)")]

    # Left: Rust Host KernelManager
    b.append(box(8, 24, 186, 178, "Rust 宿主 KernelManager", [
        "1. 发送 execute / interrupt",
        "   / snapshot / restore",
        "2. 接收 stdout / stderr /",
        "   result / display / done",
        "3. HeadTailBuffer 截断:",
        "   65,536 chars = 3/4 头",
        "   + 1/4 滚动尾",
        "4. 默认 60s 超时 (上限180s)",
        "   超时不响应则强制重启内核",
    ], accent=True))

    # Right: Python REPL Process
    b.append(box(274, 24, 198, 178, "CPython 进程 (rlm.repl)", [
        "• fd 0 读线程接收请求,",
        "  用户代码 stdin 重绑 /dev/null",
        "• 私有 dup(fd 1) 加锁写协议帧",
        "• _TaggedWriter 拦截 sys.stdout",
        "  ContextVar 归属 cell id",
        "• 原始 fd 1/2 重定向至 _Pump 管",
        "  输出 id: null (不误归属 cell)",
        "• 结束前写 \\xff<drain:uuid>\\xff",
        "  围栏刷新管道, 再发 done",
    ], forest=True))

    # Middle bidirectional protocol arrows
    b.append(arrow(194, 58, 274, 58, color="var(--amber)"))
    b.append(t(234, 52, "stdin 请求帧", 8.2, 600, "var(--amber)", "middle"))

    b.append(arrow(274, 96, 194, 96, color="var(--forest)"))
    b.append(t(234, 90, "dup(fd1) 事件帧", 8.2, 600, "var(--forest)", "middle"))

    b.append(arrow(274, 140, 194, 140, dashed=True, color="var(--muted)"))
    b.append(t(234, 134, "host_request", 8.2, 600, "var(--muted)", "middle", MONO))

    b.append(arrow(194, 174, 274, 174, dashed=True, color="var(--muted)"))
    b.append(t(234, 168, "host_reply(带外)", 8.2, 600, "var(--muted)", "middle"))

    # Bottom row: Out-of-band & Snapshot cards
    b.append(box(8, 222, 224, 72, "带外控制通道（不排执行队列）", [
        "interrupt: Unix SIGINT / await 取消",
        "host_reply: 唤醒 await host_request()",
        "bash_activity / mcp_status: 运行中可查",
    ]))
    b.append(box(248, 222, 224, 72, "dill v2 状态快照与恢复", [
        "单变量上限 16 MiB, 总量上限 256 MiB",
        "os.replace 原子落盘 + manifest.json",
        "文件句柄恢复为已关闭 stub (不重开截断)",
    ]))
    b.append(arrow(120, 202, 120, 222))
    b.append(arrow(360, 202, 360, 222))

    b.append(t(8, 312, "单次 display / host_request 严格校验 allow_nan=False 且载荷不超过 16 MiB。", 8.5, 400, "var(--muted)"))
    return svg(324, "Protocol v3 Python REPL 双通道与状态循环", "".join(b))


def inbox_subagent_flow():
    """Diagram 3: Agent Inbox 6 classes -> journal -> wake row -> rlm.inbox.read -> TUI card."""
    b = [t(8, 12, "机器通知不再直接塞满上下文：持久信箱与合并唤醒链路", 8.6, 600, "var(--muted)")]

    # Left column: 6 producers
    b.append(box(8, 24, 144, 170, "六类异步事件源", [
        "1. bash-done (后台命令)",
        "2. factory-progress (工厂)",
        "3. agent-message (核家族)",
        "4. child-exited (子完成)",
        "5. child-failed (子失败)",
        "6. heartbeat (周期心跳)",
        "   同 job 仅留最新一封",
    ]))

    # Middle column: SessionInbox + Journal
    b.append(box(174, 24, 150, 86, "SessionInbox 存储", [
        "inbox.jsonl 仅追加日志",
        "断写重放 + 单调递增 ID",
        "写失败显式降级内存态",
        "已回收 bash 自动撤回",
    ], accent=True))

    b.append(box(174, 126, 150, 68, "合并唤醒与提醒行", [
        "inbox:arrival 短通知行",
        "inbox:reminder 周期提醒",
        "只报数量, 不塞全量正文",
    ]))

    # Right column: Kernel read + TUI card
    b.append(box(346, 24, 126, 86, "rlm.inbox.read()", [
        "支持 4 个过滤字段:",
        "sender / class /",
        "sinceMs / beforeMs",
        "未知字段严格报错",
    ], forest=True))

    b.append(box(346, 126, 126, 68, "python_repl 卡片", [
        "emit 专有 MIME 帧",
        "逐条还原信箱消息",
        "清空后自动撤回提醒",
    ]))

    # Arrows
    b.append(arrow(152, 68, 174, 68))
    b.append(arrow(249, 110, 249, 126))
    b.append(arrow(324, 68, 346, 68))
    b.append(arrow(409, 110, 409, 126))
    b.append(poly_arrow("346,160 335,160 335,160 324,160", dashed=True, color="var(--amber)"))

    # Bottom security/routing strip
    b.append(box(8, 212, 464, 52, "家族通信与防伪造边界", [
        "agent_message.send 仅限 parent / sibling / child，优先 Worker 点对点直连，失败回退 Supervisor 转发",
        "inbox_notice 与 inbox:arrival/reminder 保留键由 Daemon 内部直插，客户端伪造输入直接拒绝",
    ]))

    b.append(t(8, 282, "全局开关 agentInbox.enabled: false 可回退直插模式；无 Daemon 的 -p 模式不启用信箱。", 8.5, 400, "var(--muted)"))
    return svg(294, "持久信箱 rlm.inbox 与核家族消息投递链路", "".join(b))


def prompt_continual_harness():
    """Diagram 4: Layered System Prompt + Harness Digest + /refine Continual Harness."""
    b = [t(8, 12, "前缀缓存不变与持续精炼并存：静态层、动态尾与独立摘要消息", 8.6, 600, "var(--muted)")]

    # Static prefix
    b.append(box(8, 24, 218, 112, "系统提示词 · 静态前缀 (可缓存)", [
        "1. core.md: 单工具 python_repl 与 API",
        "2. usage.md: 强制执行规约",
        "3. opinionated.md: 风格与协作原则",
        "4. model-prompts: 按模型匹配追加",
        "   (如 glm-5.3* -> bash 规则)",
    ], forest=True))
    b.append(badge(152, 28, "字节级稳定", "var(--forest)", 66))

    # Dynamic tail
    b.append(box(244, 24, 228, 112, "系统提示词 · 动态尾部 (按会话)", [
        "• packages: 预装包与 uv pip 提示",
        "• project-context: 发现的 AGENTS.md",
        "• skills-inventory: 可见技能清单",
        "• mcp-servers / environment",
        "• session-role (深度与父子角色)",
    ]))

    b.append(arrow(226, 80, 244, 80))

    # Harness Digest (separate user message!)
    b.append(box(8, 154, 464, 58, "[harness-digest] 独立用户消息（不污染静态系统前缀）", [
        "合并全局与会话本地 harness_state.json，按当前目标与近 4 条对话词项打分排序",
        "携带 stateFingerprint 指纹；状态未变时不重复追加，冷启动/恢复/压缩边界按需投递",
    ], accent=True))

    # Refine pipeline
    b.append(box(8, 232, 144, 72, "触发精炼", [
        "/refine 命令或",
        "await refine.run()",
        "或压缩后自动触发",
        "当前回合结束后执行",
    ]))
    b.append(box(168, 232, 144, 72, "小步证据更新", [
        "五类持久条目:",
        "prompt / memory /",
        "skill / subagent /",
        "factory (含版本号)",
    ]))
    b.append(box(328, 232, 144, 72, "审计与回滚保障", [
        "永不改写静态基础提示",
        "refinement_history.jsonl",
        "记录触发源、变更与证据",
        "支持按快照回退",
    ], forest=True))

    b.append(arrow(152, 268, 168, 268))
    b.append(arrow(312, 268, 328, 268))
    b.append(poly_arrow("400,232 400,212", dashed=True, color="var(--amber)"))

    b.append(t(8, 322, "prime-agent prompt 命令可打印完整分段拆解与 cached_prefix_len 字节边界。", 8.5, 400, "var(--muted)"))
    return svg(334, "分层系统提示词与 Continual Harness 精炼流程", "".join(b))


def context_compaction_offload():
    """Diagram 5: Linear History vs RLM Variable Offloading + Compaction Thresholds."""
    b = [t(8, 12, "为什么 RLM 长程不失速：变量卸载 + 带底线的安全压缩", 8.6, 600, "var(--muted)")]

    # Top comparison: Traditional vs RLM
    b.append(box(8, 24, 224, 84, "传统多工具线性历史", [
        "每次 read/grep/bash 原文回填上下文",
        "大输出反复参与后续每轮注意力计算",
        "上下文很快触顶，被迫高频粗粒度总结",
        "中间分析状态随摘要丢失，只能重读",
    ]))
    b.append(box(248, 24, 224, 84, "Prime Agent (RLM 变量卸载)", [
        "大文件/结果绑定到 REPL 命名变量",
        "仅向模型回传切片、统计或 Head-Tail 摘要",
        "压缩只折叠对话消息，REPL 内存继续存活",
        "超出 16 MiB 单变量在压缩时定向裁剪",
    ], forest=True))

    # Compaction threshold box
    b.append(box(8, 126, 464, 96, "自动压缩触发阈值公式 (pa-core/src/session_engine/compaction.rs)", [
        "1. 比例目标: requested = context_window * threshold_percent (默认 95%)",
        "2. 保留底线: retention_floor = keep_recent(20,000) + 2 * reserve(16,384) + 1",
        "   防止过低百分比导致每轮工具调用都反复重摘要",
        "3. 联合上限: combined = context_window - max_output - max(reserve, 4,096)",
        "4. 生效阈值: threshold = min(max(requested, retention_floor), combined)",
    ], accent=True))

    # Bottom row: Post-compaction flow
    b.append(box(8, 240, 146, 58, "压缩执行", [
        "保留近期 20k tokens",
        "写入 compactionSummary",
        "触发内核 >16MiB 裁剪",
    ]))
    b.append(box(167, 240, 146, 58, "冷边界重注", [
        "刷新 [harness-digest]",
        "发送 ipython_state 通知",
        "历史可用 session.messages",
    ]))
    b.append(box(326, 240, 146, 58, "目标与自主续跑", [
        "活跃 Goal 注入续跑行",
        "质量门禁检验工作树快照",
        "压缩后精炼顺延至空闲点",
    ]))

    b.append(arrow(240, 222, 81, 240))
    b.append(arrow(154, 269, 167, 269))
    b.append(arrow(313, 269, 326, 269))

    b.append(t(8, 316, "Token 估算采用保守 chars/4 向上取整；视频与图片块分别按固定估算值计入。", 8.5, 400, "var(--muted)"))
    return svg(328, "RLM 变量卸载与上下文自动压缩机制", "".join(b))


def mcp_security_boundary():
    """Diagram 6: MCP Host/Kernel Split & Security Trust Boundaries."""
    b = [t(8, 12, "MCP 宿主/内核切分与真实安全边界（进程隔离 ≠ 安全沙箱）", 8.6, 600, "var(--muted)")]

    # Host side
    b.append(box(8, 24, 224, 118, "Rust 宿主侧 (pa-core/src/mcp + skills)", [
        "• 凭据存储与 OAuth 2.0 (RFC 9728 + PKCE)",
        "• 本地回调端口 + 令牌刷新",
        "• 响应 mcp.config / mcp.refresh / login",
        "• 仓库级技能 (SourceScope::Project)",
        "  禁止自动导入内核，只作 Markdown 文档",
        "• 目录查询响应不返回原始凭据",
    ], forest=True))

    # Kernel side
    b.append(box(248, 24, 224, 118, "Python 内核侧 (rlm/mcp.py + 外部服务)", [
        "• 启动 stdio 或连接 Streamable HTTP MCP",
        "• 子进程仅透传 _SAFE_ENV 基础变量",
        "• tools/list 分页上限 25 页防卡死",
        "• 关闭超时 2.5s (严于宿主 5s 强杀线)",
        "• stderr 限 8 KiB / 40 行并正则脱敏",
        "• mcp.call_tool 原样透传实时结果",
    ]))

    b.append(arrow(232, 66, 248, 66, color="var(--forest)"))
    b.append(arrow(248, 102, 232, 102, dashed=True, color="var(--muted)"))

    # Warning / OS permission box
    b.append(box(8, 160, 464, 92, "操作系统权限现实（README.md 明确声明的边界）", [
        "1. Worker 与 Python 内核以当前用户权限运行模型生成的 Python 代码与 bash() 命令",
        "2. 进程隔离只解决生命周期回收与故障恢复，不构成恶意代码或提示词注入的安全沙箱",
        "3. 自主质量门禁的 Git 快照已禁用 fsmonitor/钩子/外部 diff，但 .gitattributes 过滤器仍无法关闭",
        "4. 处理不可信仓库或指令时，必须在外部容器、虚拟机或云端沙箱中隔离运行",
    ], accent=True))

    b.append(t(8, 270, "Rust 全工作区开启 unsafe_code = forbid；密钥不进埋点（pa-telemetry 仅发基础类型）。", 8.5, 400, "var(--muted)"))
    return svg(282, "MCP 桥接职责划分与安全信任边界", "".join(b))


DIAGRAMS = {
    "dual-layer-topology": dual_layer_topology,
    "rlm-repl-cycle": rlm_repl_cycle,
    "inbox-subagent-flow": inbox_subagent_flow,
    "prompt-continual-harness": prompt_continual_harness,
    "context-compaction-offload": context_compaction_offload,
    "mcp-security-boundary": mcp_security_boundary,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Clean any stale diagrams from template
    for old in OUT.glob("*.svg"):
        if old.stem not in DIAGRAMS:
            old.unlink()
    for name, fn in DIAGRAMS.items():
        path = OUT / f"{name}.svg"
        path.write_text(fn(), encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)}")


if __name__ == "__main__":
    main()
