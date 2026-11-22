#!/usr/bin/env python3
"""Generate the 6 inline SVG diagrams used by the Infinity architecture ebook.

All SVGs use CSS theme variables (--ink, --paper, --paper-2, --muted, --line,
--amber, --forest) so they adapt cleanly to light/dark web themes, A5 print PDF,
and EPUB 3 without requiring JavaScript.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "wiki" / "assets" / "diagrams"
FONT = "'IBM Plex Sans','Noto Sans CJK SC',system-ui,sans-serif"
MONO = "'IBM Plex Mono','SFMono-Regular',Consolas,monospace"
W = 640


def t(
    x: float,
    y: float,
    s: str,
    size: float = 10,
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
    accent: str | None = None,
    title_size: float = 11,
    line_size: float = 9.5,
    mono_title: bool = False,
) -> str:
    stroke = accent if accent else "var(--line)"
    sw = "1.5" if accent else "0.9"
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
        f'fill="var(--paper-2)" stroke="{stroke}" stroke-width="{sw}"/>'
    ]
    out.append(
        t(
            x + 10,
            y + 19,
            title,
            title_size,
            600,
            accent if accent else "var(--ink)",
            family=MONO if mono_title else FONT,
        )
    )
    for i, line in enumerate(lines):
        out.append(t(x + 10, y + 36 + i * 16, line, line_size, 400, "var(--muted)"))
    return "".join(out)


def arrow(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    dashed: bool = False,
    color: str = "var(--muted)",
) -> str:
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    marker = "arrow-amber" if color == "var(--amber)" else (
        "arrow-forest" if color == "var(--forest)" else "arrow"
    )
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="1.2"{dash} marker-end="url(#{marker})"/>'
    )


def svg(height: int, title: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
        f'width="{W}" height="{height}" role="img" aria-label="{escape(title)}">\n'
        f"<title>{escape(title)}</title>\n"
        "<defs>"
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker>'
        '<marker id="arrow-amber" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--amber)"/></marker>'
        '<marker id="arrow-forest" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--forest)"/></marker>'
        "</defs>\n"
        f"{body}\n"
        "</svg>\n"
    )


def blocking_vs_slice() -> str:
    b: list[str] = []
    b.append(t(10, 18, "传统阻塞模型：等待期间进程、栈与连接全程常驻", 10.5, 600, "var(--amber)"))
    b.append(
        '<rect x="10" y="28" width="620" height="88" rx="4" fill="none" '
        'stroke="var(--amber)" stroke-width="1" stroke-dasharray="4 3"/>'
    )
    b.append(box(22, 42, 138, 60, "LLM 轮次 1", ["决定调用 CI 构建", "占用常驻任务栈"]))
    b.append(arrow(160, 72, 192, 72, color="var(--amber)"))
    b.append(
        box(
            194,
            42,
            246,
            60,
            "阻塞等待工具返回 (20 分钟+)",
            ["stdio/HTTP 会话不能断", "Lambda 超时、空闲内存无法回收"],
            accent="var(--amber)",
        )
    )
    b.append(arrow(440, 72, 472, 72, color="var(--amber)"))
    b.append(box(474, 42, 144, 60, "LLM 轮次 2", ["继续消费结果", "期间始终按时长计费"]))

    b.append(t(10, 146, "Infinity 无阻塞切片模型：派发即让出，等待期间纯数据休眠", 10.5, 600, "var(--forest)"))
    b.append(
        '<rect x="10" y="156" width="620" height="148" rx="4" fill="none" '
        'stroke="var(--forest)" stroke-width="1.2"/>'
    )
    b.append(
        box(
            22,
            170,
            174,
            72,
            "切片 1 (Slice)",
            ["加载历史 -> 模型流式输出", "先 sync() 落盘再 POST", "收到 200 ACK 立即退出"],
            accent="var(--forest)",
        )
    )
    b.append(arrow(196, 206, 226, 206, color="var(--forest)"))
    b.append(
        box(
            228,
            170,
            184,
            72,
            "休眠期 (0 驻留任务)",
            ["仅存历史与状态表", "无进程、无栈、无长连接", "本地退驱动 / Lambda 缩零"],
        )
    )
    b.append(arrow(412, 206, 442, 206, dashed=True, color="var(--forest)"))
    b.append(
        box(
            444,
            170,
            174,
            72,
            "切片 2 (新唤醒)",
            ["RAP 回调 POST -> 入队", "按 group_id 重载历史", "去重匹配后继续下一轮"],
            accent="var(--forest)",
        )
    )
    b.append(
        t(
            22,
            268,
            "单线程串行与多线程并发由队列层 group_id (SQS FIFO / 本地 mpsc 路由) 保障，运行时无需分布式锁。",
            9.5,
            400,
            "var(--muted)",
        )
    )
    b.append(
        t(
            22,
            288,
            "实测基准 (agent_scale.rs): 80,000 个空闲 Agent 共占约 8.4 GB RSS，约 102 KiB/Agent。",
            9.5,
            600,
            "var(--forest)",
        )
    )
    return svg(316, "阻塞式 Agent 与 Infinity 无阻塞切片对比", "".join(b))


def crate_topology() -> str:
    b: list[str] = [
        t(10, 18, "21 个 Workspace Crate 的五层依赖版图 (自顶向下调用)", 10.5, 600, "var(--ink)")
    ]
    layers = [
        (
            "交互前端与部署外壳 (5 Crates)",
            "infinity-agent-cli (TUI/ACP) · infinity-daemon · infinity-agent-lambda · slack-bot · slack-dataflow",
            "面向终端、桌面 Web、Zed/ACP、AWS Lambda 与 Slack 的运行宿主",
            "var(--forest)",
        ),
        (
            "核心切片运行时 (2 Crates)",
            "infinity-agent-core (event_processor / HistoryManager / local router & driver) · infinity-mdtests",
            "全部状态机、三阶段缓冲、线程树、位置索引压缩与单线程 Actor 调度",
            "var(--amber)",
        ),
        (
            "工具桥接与标准 RAP 服务 (6 Crates)",
            "rap-client · infinity-rap-bridge · infinity-mcp-bridge · steering-server · github-poller · test-servers",
            "统一回调转换 prepare_callback、MCP JSON-RPC 适配、规则扫描与 GitHub 订阅轮询",
            None,
        ),
        (
            "堆叠沙箱子系统 (3 Crates)",
            "sandbox-core (SandboxBackend/ModeProvider) · sandbox-local (jj/git + bwrap) · sandbox-remote (EFS)",
            "按线程隔离工作区、命令流式输出、OS 写隔离、审批弹窗与 git bundle 跨机迁移",
            None,
        ),
        (
            "底层线缆协议与模型供应 (5 Crates)",
            "rap-protocol (类型化 ID) · infinity-protocol (ThreadRef) · provider-protocol (ErrorClass) · bedrock · rig",
            "跨进程与跨网络稳定契约：strkind 强类型 ID、Unix Socket 供应器与结构化错误分类",
            "var(--forest)",
        ),
    ]
    for i, (title, crates, desc, accent) in enumerate(layers):
        y = 30 + i * 72
        b.append(box(10, y, 620, 58, title, [crates, desc], accent=accent, title_size=10.5, line_size=9.2))
        if i < len(layers) - 1:
            b.append(arrow(320, y + 58, 320, y + 72, color="var(--line)"))
    b.append(
        t(
            10,
            402,
            "辅助 TS 子项目: agent/ (AWS CDK 部署栈) · infinity-ui/ (共享 React 组件) · infinity-web/ (Vite 桌面端)",
            9.3,
            400,
            "var(--muted)",
        )
    )
    return svg(416, "Infinity 21 个 Crate 的五层依赖版图", "".join(b))


def history_three_phase() -> str:
    b: list[str] = [
        t(10, 18, "HistoryManager 三阶段未落盘状态、轮次缓冲与溢出恢复", 10.5, 600, "var(--ink)")
    ]
    b.append(
        box(
            10,
            34,
            184,
            82,
            "1. unvalidated_items",
            ["刚入内存的新输入", "用户文本 / 工具结果 / 合成事件", "sync() 跳过，防毒消息落盘"],
            accent="var(--amber)",
            mono_title=True,
            title_size=10,
        )
    )
    b.append(arrow(194, 75, 226, 75, color="var(--forest)"))
    b.append(t(210, 66, "首输出", 8.8, 600, "var(--forest)", "middle"))

    b.append(
        box(
            228,
            34,
            184,
            82,
            "2. pending_items",
            ["mark_inputs_model_validated", "已证安全输入 + 已刷轮次", "断言: 无未验证项才可提交"],
            accent="var(--forest)",
            mono_title=True,
            title_size=10,
        )
    )
    b.append(arrow(412, 75, 444, 75, color="var(--forest)"))
    b.append(t(428, 66, "sync()", 8.8, 600, "var(--forest)", "middle", family=MONO))

    b.append(
        box(
            446,
            34,
            184,
            82,
            "3. 持久化存储",
            ["ConversationStore 追加消息", "StateStore 写非幂等去重 ID", "落盘屏障后才派发异步工具"],
            title_size=10,
        )
    )

    b.append(
        box(
            228,
            152,
            184,
            72,
            "turn_buffer (流式缓冲)",
            ["合并连续 Text 块", "正常结束: flush_turn()", "中途故障: discard_turn()"],
            mono_title=True,
            title_size=9.8,
        )
    )
    b.append(arrow(320, 152, 320, 116, color="var(--muted)"))
    b.append(t(328, 138, "flush_turn", 8.8, 400, "var(--muted)", family=MONO))

    b.append(arrow(102, 116, 102, 152, color="var(--amber)"))
    b.append(t(110, 138, "ContextOverflow", 8.8, 600, "var(--amber)", family=MONO))
    b.append(
        box(
            10,
            152,
            184,
            72,
            "recover_from_overflow",
            ["仅 1 条可替换项: 改占位符重试", "含用户输入或多条: 结算中断", "丢弃未验证用户输入并停止"],
            accent="var(--amber)",
            mono_title=True,
            title_size=9.6,
        )
    )

    b.append(arrow(538, 116, 538, 152, color="var(--forest)"))
    b.append(t(546, 138, "屏障之后", 8.8, 600, "var(--forest)"))
    b.append(
        box(
            446,
            152,
            184,
            72,
            "execute_action()",
            ["POST /invoke 异步派发", "失败则回注错误结果", "避免线程永久挂起等待"],
            accent="var(--forest)",
            mono_title=True,
            title_size=9.8,
        )
    )

    b.append(
        t(
            10,
            248,
            "顺序不变量: pending_items 始终位于 unvalidated_items 之前；普通工具结果靠尾扫 match_tool_result 去重。",
            9.3,
            400,
            "var(--muted)",
        )
    )
    return svg(262, "HistoryManager 三阶段未落盘状态与溢出恢复", "".join(b))


def rap_lifecycle() -> str:
    b: list[str] = [
        t(10, 18, "RAP 响应式工具协议全链路：断连回调、订阅分流与 MCP 兼容", 10.5, 600, "var(--ink)")
    ]
    b.append(
        box(
            10,
            34,
            196,
            116,
            "Agent Runtime",
            [
                "1. GET /.well-known/rap-toolset",
                "2. POST /invoke (带 callback_url)",
                "3. 收到 2xx 即让出并休眠",
                "4. RapNotifier 生命周期通知:",
                "   /close_thread /cancel /migrate",
            ],
            accent="var(--forest)",
            title_size=10.5,
        )
    )
    b.append(
        box(
            242,
            34,
            188,
            116,
            "独立 RAP 工具服务",
            [
                "立即返回 HTTP 200 ACK",
                "后台执行长任务或维护订阅",
                "如 sandbox-local / github-poller",
                "完成后主动 POST callback_url",
                "不绑定 Agent 进程存活周期",
            ],
            accent="var(--amber)",
            title_size=10.5,
        )
    )
    b.append(
        box(
            462,
            34,
            168,
            116,
            "MCP 兼容桥接",
            [
                "infinity-mcp-bridge",
                "懒初始化 Stdio / HTTP",
                "JSON-RPC 2.0 + SSE",
                "维护 mcp-session-id",
                "对核心暴露统一 Tool",
            ],
            title_size=10.5,
        )
    )

    b.append(arrow(206, 68, 242, 68, color="var(--forest)"))
    b.append(t(224, 60, "POST", 8.5, 600, "var(--forest)", "middle", family=MONO))
    b.append(arrow(242, 102, 206, 102, dashed=True, color="var(--amber)"))
    b.append(t(224, 116, "回调", 8.5, 600, "var(--amber)", "middle"))
    b.append(arrow(430, 88, 462, 88, dashed=True, color="var(--muted)"))

    b.append(arrow(336, 150, 336, 178, color="var(--amber)"))
    b.append(
        t(
            346,
            168,
            "prepare_callback (Daemon 与 Lambda 共用)",
            9.2,
            600,
            "var(--amber)",
        )
    )
    b.append(
        box(
            10,
            180,
            620,
            102,
            "五类 RapCallback 回调的统一分流",
            [
                "tool_result        -> InputMessage (支持多模态 content + display_as + subscription 标记)",
                "subscription_event -> 默认自动派生隔离事件子线程；associative: true 内联注入；final: true 清理订阅",
                "user_choice / oauth -> 经 PrepareResult 透传给 TUI/Web/Slack，不污染模型对话历史",
                "view_update        -> 旁路显示通道 (如 diff 面板)，Daemon 转发前端，Lambda 接收后直接丢弃",
            ],
            title_size=10,
            line_size=9.2,
        )
    )
    return svg(296, "RAP 响应式工具协议全链路与五类回调分流", "".join(b))


def driver_deferral_state() -> str:
    b: list[str] = [
        t(10, 18, "本地单线程 LocalSet 路由器、延迟队列与抢占状态机", 10.5, 600, "var(--ink)")
    ]
    b.append(
        box(
            10,
            34,
            286,
            100,
            "route_loop (单线程 LocalSet)",
            [
                "所有线程驱动直接托管于 FuturesUnordered",
                "准入检查: 非用户文本查 thread_exists 与",
                "is_thread_stopped (存储故障时 fail-open)",
                "异常经 log_panic 隔离，不拖垮其他线程",
            ],
            accent="var(--forest)",
            title_size=10,
        )
    )
    b.append(arrow(296, 84, 334, 84, color="var(--forest)"))
    b.append(t(315, 74, "拉起", 8.8, 600, "var(--forest)", "middle"))

    b.append(
        box(
            336,
            34,
            294,
            100,
            "drive_thread (每活跃线程 1 个 Future)",
            [
                "批量排空同线程通道，调用 filter_deferrable",
                "流式执行中若收到用户文本: cancel() 中断",
                "附加 <interrupt> 前缀并通知 RAP 取消工具",
                "Token > 75% 上下文窗时触发自动压缩子线程",
            ],
            accent="var(--amber)",
            title_size=10,
        )
    )

    b.append(arrow(483, 134, 483, 162, color="var(--muted)"))
    b.append(
        box(
            10,
            164,
            296,
            88,
            "DeferQueue 延迟策略 (本地 vs Lambda)",
            [
                "尾部有未答非被动工具 (!is_passive()):",
                "  暂存订阅事件、子线程报告与父线程消息",
                "sleep 家族标记 is_passive() == true 可打断",
                "Lambda 默认 NoDeferral，直接按中断处理",
            ],
            title_size=10,
        )
    )
    b.append(
        box(
            326,
            164,
            304,
            88,
            "check_before_idle (同步原子退出)",
            [
                "与路由共享 LocalSet，检查期间不让出调度",
                "有待答非 close_thread 工具 -> AwaitInput",
                "仅剩活跃订阅或完全空闲 -> Exit 释放内存",
                "DriverGuard::drop 发出 Idle 生命周期事件",
            ],
            accent="var(--forest)",
            title_size=10,
        )
    )
    b.append(arrow(306, 208, 326, 208, dashed=True, color="var(--muted)"))

    b.append(
        t(
            10,
            274,
            "客户端通过 SubscribeHandle 的 oneshot ACK 确认重放 ReplaySnapshot 安装完成，避免丢失并发事件。",
            9.3,
            400,
            "var(--muted)",
        )
    )
    return svg(288, "本地单线程路由器与线程驱动状态机", "".join(b))


def sandbox_isolation() -> str:
    b: list[str] = [
        t(10, 18, "堆叠沙箱工作区与操作系统级写隔离边界", 10.5, 600, "var(--ink)")
    ]
    b.append(
        box(
            10,
            34,
            620,
            48,
            "宿主仓库工作目录 (默认只读，永不直接改写)",
            ["沙箱落在 {repo}/.infinity/.sandboxes/，并注入 GIT_CEILING_DIRECTORIES 防止 git 越界识别外层仓"],
            accent="var(--forest)",
            title_size=10,
        )
    )
    b.append(arrow(320, 82, 320, 108, color="var(--forest)"))

    b.append(
        box(
            195,
            110,
            250,
            48,
            "父线程沙箱 sandbox-{root}",
            ["jj workspace add / git worktree add"],
            accent="var(--amber)",
            title_size=10,
        )
    )
    b.append(arrow(260, 158, 162, 188, color="var(--muted)"))
    b.append(arrow(380, 158, 478, 188, color="var(--muted)"))
    b.append(t(156, 174, "base_thread_id 堆叠", 8.8, 400, "var(--muted)", "middle"))
    b.append(t(484, 174, "并行独立工作区", 8.8, 400, "var(--muted)", "middle"))

    b.append(
        box(
            26,
            190,
            266,
            56,
            "子线程 A: sandbox-{c1}",
            ["独立修改单测，互不覆盖", "完成后 squash_sandbox 合并回父书签"],
            title_size=10,
        )
    )
    b.append(
        box(
            348,
            190,
            266,
            56,
            "子线程 B: sandbox-{c2}",
            ["独立修改文档，互不覆盖", "完成后 squash_sandbox 合并回父书签"],
            title_size=10,
        )
    )

    b.append(
        box(
            10,
            266,
            620,
            92,
            "OS 写隔离层 (sandbox-local) 与越权审批",
            [
                "Linux:  bwrap --ro-bind / / --bind <tmp> --bind <sandbox> --dev /dev --proc /proc (缺少时 build.rs 编译)",
                "macOS:  sandbox-exec (allow default) (deny file-write*) + 仅放行沙箱、TMPDIR、sccache 与 /dev/null",
                "提权写: 请求 write-orig 或 write:/path 时沿 thread_ancestors 查授权，未授权发 RAP UserChoice 确认",
                "边界声明: 读文件系统与网络默认开放，目标防止误改宿主工作区，并非对抗恶意代码的虚拟化容器",
            ],
            accent="var(--amber)",
            title_size=10,
            line_size=9.2,
        )
    )
    return svg(372, "堆叠沙箱与操作系统级写隔离边界", "".join(b))


DIAGRAMS = {
    "blocking-vs-slice": blocking_vs_slice,
    "crate-topology": crate_topology,
    "history-three-phase": history_three_phase,
    "rap-lifecycle": rap_lifecycle,
    "driver-deferral-state": driver_deferral_state,
    "sandbox-isolation": sandbox_isolation,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        if old.stem not in DIAGRAMS:
            old.unlink()
    for name, fn in DIAGRAMS.items():
        content = fn()
        assert "\u2014" not in content and "\u2013" not in content, f"em/en-dash in {name}"
        path = OUT / f"{name}.svg"
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)}")


if __name__ == "__main__":
    main()
