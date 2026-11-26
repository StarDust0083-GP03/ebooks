#!/usr/bin/env python3
"""Generate 6 theme-aware inline SVG diagrams for MCP 2027 Architecture Ebook.

Includes programmatic width/overflow checking so no label clips or overlaps
its container, and verifies zero em-dash/en-dash characters in labels.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "wiki" / "assets" / "diagrams"
FONT = "'IBM Plex Sans','Noto Sans CJK SC',system-ui,sans-serif"
MONO = "'IBM Plex Mono','SFMono-Regular',Consolas,monospace"
W = 660


def est_width(text: str, size: float, mono: bool = False) -> float:
    total = 0.0
    for ch in text:
        if ord(ch) > 127:
            total += size * 1.04
        elif mono:
            total += size * 0.62
        else:
            total += size * 0.57
    return total


def check_text(s: str, max_w: float, size: float, mono: bool = False) -> None:
    if "\u2014" in s or "\u2013" in s:
        raise ValueError(f"Disallowed dash in diagram text: {s!r}")
    w = est_width(s, size, mono=mono)
    if w > max_w:
        raise ValueError(f"Text overflow ({w:.1f} > {max_w:.1f}): {s!r}")


def t(x: float, y: float, s: str, size: float = 11, weight: int = 400,
      fill: str = "var(--ink)", anchor: str = "start", family: str = FONT,
      max_w: float | None = None) -> str:
    mono = (family == MONO)
    if max_w is not None:
        check_text(s, max_w, size, mono=mono)
    else:
        if "\u2014" in s or "\u2013" in s:
            raise ValueError(f"Disallowed dash in diagram text: {s!r}")
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>'
    )


def box(x: float, y: float, w: float, h: float, title: str, lines: list[str] | tuple[str, ...] = (),
        accent: str | None = None, title_size: float = 11.5, line_size: float = 10, mono_lines: bool = False) -> str:
    stroke = accent if accent else "var(--line)"
    sw = 1.5 if accent else 0.9
    inner_w = w - 20
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="var(--paper-2)" '
        f'stroke="{stroke}" stroke-width="{sw}"/>'
    ]
    out.append(t(x + 10, y + 20, title, title_size, 600, accent if accent else "var(--ink)", max_w=inner_w))
    for i, line in enumerate(lines):
        out.append(
            t(x + 10, y + 38 + i * 16, line, line_size, 400, "var(--muted)",
              family=MONO if mono_lines else FONT, max_w=inner_w)
        )
    return "".join(out)


def arrow(x1: float, y1: float, x2: float, y2: float, dashed: bool = False, color: str = "var(--muted)") -> str:
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="1.2"{dash} marker-end="url(#arrow)"/>'
    )


def svg(height: int, title: str, body: str) -> str:
    check_text(title, W * 2, 12)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img" aria-label="{escape(title)}">
<title>{escape(title)}</title>
<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/>
  </marker>
</defs>
{body}
</svg>'''


def stateless_routing() -> str:
    b = [
        t(12, 18, "旧架构 (2025): 有状态粘性会话与连接绑定", 12, 700, "var(--amber)", max_w=636),
        box(12, 28, 192, 74, "Client / Subagents", ["新开连接须重跑 initialize", "重拉 O(N x M) tools/list"], accent="var(--amber)"),
        arrow(204, 65, 232, 65),
        box(234, 28, 192, 74, "粘性负载均衡器", ["依赖 Mcp-Session-Id 路由", "GET SSE 常驻连接易超时"]),
        arrow(426, 65, 454, 65),
        box(456, 28, 192, 74, "单节点内存会话", ["实例重启即丢会话状态", "列表结果跨会话不可缓存"]),
        f'<line x1="12" y1="120" x2="648" y2="120" stroke="var(--line)" stroke-width="0.8" stroke-dasharray="4 4"/>',
        t(12, 142, "新内核 (2026-07-28): 无状态头镜像与可缓存列表", 12, 700, "var(--forest)", max_w=636),
        box(12, 152, 192, 92, "Client + ListCache", ["每请求携带 _meta 能力", "按 ttlMs/cacheScope 复用", "显式传递 basket_id 句柄"], accent="var(--forest)"),
        arrow(204, 198, 232, 198),
        box(234, 152, 192, 92, "标准 L7 网关 / CDN", ["读 Mcp-Method / Mcp-Name", "按 Mcp-Param-* 路由限流", "无粘性会话，任意轮询"], accent="var(--forest)"),
        arrow(426, 198, 454, 198),
        box(456, 152, 192, 92, "无状态副本 / Serverless", ["校对头与 Body (-32020)", "按 Token 校验句柄权限", "subscriptions/listen 按需推"]),
        t(12, 266, "核心变化: 去掉 Mcp-Session-Id 与 initialize，把状态显式化为业务句柄与可缓存元数据。", 10.5, 400, "var(--muted)", max_w=636),
    ]
    return svg(282, "2025 有状态粘性会话与 2026-07-28 无状态头路由对比", "".join(b))


def code_mode_vs_toolcall() -> str:
    b = [
        t(12, 18, "传统串行 Tool Calling (N+1 轮模型推理，中间数据全进上下文)", 11.5, 700, "var(--amber)", max_w=636),
        box(12, 28, 142, 62, "Turn 1: 模型", ["调 linear.listIssues", "注入 50 个 Schema"]),
        arrow(154, 59, 174, 59),
        box(176, 28, 146, 62, "20 条 Issue 原文", ["全部写回对话历史", "上下文迅速膨胀"], accent="var(--amber)"),
        arrow(322, 59, 342, 59),
        box(344, 28, 142, 62, "Turn 2..N: 循环", ["逐条查 Sentry 统计", "每步多一轮推理"]),
        arrow(486, 59, 506, 59),
        box(508, 28, 140, 62, "最终汇总", ["高延迟 + 高 Token", "中间载荷污染窗口"]),
        f'<line x1="12" y1="108" x2="648" y2="108" stroke="var(--line)" stroke-width="0.8" stroke-dasharray="4 4"/>',
        t(12, 130, "Code Mode (1 轮生成脚本，沙箱内存过滤 Join，沙箱外持有 OAuth)", 11.5, 700, "var(--forest)", max_w=636),
        box(12, 140, 174, 92, "Turn 1: 类型化桩代码", ["由 inputSchema 与", "outputSchema 自动生成", "模型一次写出异步脚本"], accent="var(--forest)"),
        arrow(186, 186, 212, 186),
        box(214, 140, 224, 92, "隔离执行沙箱 (无原始凭证)", ["Promise.all 并发调多 Server", "structuredContent 内存 Join", "OAuth 2.1 在沙箱外由宿主管"], accent="var(--forest)"),
        arrow(438, 186, 464, 186),
        box(466, 140, 182, 92, "精简结果回传模型", ["仅 P0 摘要进入上下文", "图像/音频独立块保留", "Token 降幅常超一个量级"]),
        t(12, 254, "关键前提: 服务端声明 outputSchema 并返回 structuredContent，模型才能在编码前获知返回结构。", 10.5, 400, "var(--muted)", max_w=636),
    ]
    return svg(270, "传统 Tool Calling 与 Code Mode 上下文和往返开销对比", "".join(b))


def mrtr_oauth_flow() -> str:
    b = [
        t(12, 18, "无粘性会话的 MRTR 追问与沙箱外 OAuth 2.1 授权边界", 12, 700, "var(--ink)", max_w=636),
        box(12, 32, 190, 74, "1. Host / MCP Client", ["持有 OAuth 2.1 Token", "(按 RFC 8707 绑定 aud)", "发起 tools/call (id=1)"], accent="var(--forest)"),
        arrow(202, 69, 232, 69),
        box(234, 32, 190, 74, "2. Replica A (无状态)", ["校验 Token 与 Scope", "需人工确认或外部授权", "签发 AEAD requestState"]),
        arrow(424, 69, 454, 69),
        box(456, 32, 192, 74, "3. InputRequiredResult", ["resultType: input_required", "inputRequests: elicitation", "附带不透明 requestState"], accent="var(--amber)"),
        arrow(552, 106, 552, 134),
        box(336, 136, 312, 76, "4. 人工确认或 URL Mode 带外授权", ["Form Mode: 宿主弹窗采集非敏感字段", "URL Mode: 跳浏览器直连服务端，密钥不经 LLM", "服务端核对 sub 身份，防钓鱼与会话劫持"], accent="var(--amber)"),
        arrow(336, 174, 306, 174),
        box(12, 136, 292, 76, "5. 重试落地到任意 Replica B (id=2)", ["原样回传 requestState 与 inputResponses", "Replica B 验签: principal + TTL + paramsDigest", "无需粘性路由即可返回 resultType: complete"], accent="var(--forest)"),
        t(12, 234, "安全红线: 沙箱内代码绝不接触原始 API Key; URL Mode 敏感凭证绝不回流到 MCP Client 或模型上下文。", 10.5, 400, "var(--muted)", max_w=636),
    ]
    return svg(250, "MRTR 无状态多往返追问与沙箱外 OAuth 2.1 授权流程", "".join(b))


def skills_sync_lifecycle() -> str:
    b = [
        t(12, 18, "io.modelcontextprotocol/skills 渐进披露与 SHA-256 完整性校验", 12, 700, "var(--ink)", max_w=636),
        box(12, 32, 192, 78, "阶段 1: 连接期轻量发现", ["调用 skills/list (带 ttlMs)", "仅将 name + description", "注入系统指令，零全文膨胀"], accent="var(--forest)"),
        arrow(204, 71, 232, 71),
        box(234, 32, 192, 78, "阶段 2: 锚定清单与审批", ["按账号返回定制 Playbook", "列出文件 URI/size/sha256", "用户审批严格绑定整份清单"]),
        arrow(426, 71, 454, 71),
        box(456, 32, 192, 78, "阶段 3: 按需读取验签", ["调用 resources/read", "校验字节数 + SHA-256", "核对 frontmatter 一致"]),
        arrow(552, 110, 552, 138),
        box(336, 140, 312, 78, "阶段 4: 远端变更与热更新", ["服务端更新 SKILL.md -> 摘要变化", "本地缓存未命中或校验报错，立即拒绝旧内容", "刷新 skills/get，撤销旧审批并重新确认"], accent="var(--amber)"),
        arrow(336, 179, 306, 179),
        box(12, 140, 292, 78, "本地缓存与来源隔离", ["按 (serverId, uri, sha256) 落盘缓存", "排除出本地文件系统技能自动发现路径", "跨 Server 读取须双方显式逐次授权"], accent="var(--forest)"),
        t(12, 240, "未列举技能可通过 skills/get(uri) 直接按需加载; 单个 Skill 建议不超过 512 文件或 16 MiB。", 10.5, 400, "var(--muted)", max_w=636),
    ]
    return svg(256, "Skills over MCP 渐进披露与 SHA-256 摘要更新流程", "".join(b))


def events_tasks_loop() -> str:
    b = [
        t(12, 18, "MCP Events 三种投递模式与 MCP Tasks 长程异步编排", 12, 700, "var(--ink)", max_w=636),
        box(12, 34, 196, 54, "Webhook (events/subscribe)", ["HTTPS + whsec_ HMAC 签名", "适合远程常驻与离线唤醒"], accent="var(--forest)"),
        box(12, 98, 196, 54, "Push (events/stream)", ["长连接流 + heartbeatMs 心跳", "适合 stdio 与在线实时日志"]),
        box(12, 162, 196, 54, "Poll (events/poll)", ["客户端按 nextPollMs 拉取", "适合 NAT 后 CLI / 无状态服"]),
        arrow(208, 61, 242, 114),
        arrow(208, 125, 242, 125),
        arrow(208, 189, 242, 136),
        box(244, 68, 188, 114, "Client SDK 事件消费层", ["持久化 cursor 断点续传", "truncated: true 感知缺口", "按 (订阅, eventId) 去重", "同轨迹续跑 / 隔离新轨迹", "载荷视同不可信输入"], accent="var(--amber)"),
        arrow(432, 125, 460, 125),
        box(462, 34, 186, 182, "Agent + MCP Tasks", ["唤醒 Agent 发起排查", "长任务返回 resultType:task", "128 位高熵 taskId 轮询", "遇阻转 input_required", "经 tasks/update 补输入", "终态 immutable 回写结果", "tasks/cancel 协作取消"], accent="var(--forest)"),
        t(12, 240, "职责边界: 监听外部未知时刻发生的事用 Events; 追踪自己发起的长耗时作业用 Tasks。", 10.5, 400, "var(--muted)", max_w=636),
    ]
    return svg(256, "MCP Events 三种投递模式与 MCP Tasks 异步编排回路", "".join(b))


def harness_swarm_security() -> str:
    b = [
        t(12, 18, "把 Harness 暴露为 MCP Server 的多代理协作与四重安全边界", 12, 700, "var(--ink)", max_w=636),
        box(12, 34, 304, 116, "Harness-as-MCP-Server 编排平面", [
            "子代理执行 -> MCP Tasks (异步句柄 + 轮询)",
            "共享 Swarm Board -> 类型化 Tools (发发现/认领)",
            "协作者唤醒 -> MCP Events (任务终态或新进展)",
            "设计铁律: 常规 SaaS 暴露工具，而非单一 ask_agent",
        ], accent="var(--forest)"),
        arrow(316, 92, 342, 92),
        box(344, 34, 304, 116, "纵深安全四重攻击面与防线", [
            "1. Untrusted Webhook: 验 HMAC + 防 SSRF + 隔离",
            "2. Code Mode 沙箱: 无磁盘凭证，OAuth 由宿主管",
            "3. MCP Apps iframe: 受限沙箱 + CSP + 审计 RPC",
            "4. 本地安装 SEP-1024: 完整命令展示 + 显式同意",
        ], accent="var(--amber)"),
        box(12, 168, 636, 62, "2027 尚未完全闭合的三大工程缺口", [
            "发现层: 摆脱多注册表重复提交 -> Server Cards + /.well-known/ai-catalog.json + 自动安全扫描",
            "可观测性: 服务端看不到完整对话 -> 规范化 telemetry.intent 与反馈工具，兼顾最小上下文隔离原则",
        ]),
        t(12, 250, "同一套原语向下接业务系统、向上做多代理协作，但每新增一种扩展面就必须配套对应信任边界。", 10.5, 400, "var(--muted)", max_w=636),
    ]
    return svg(266, "Harness 作为 MCP Server 的协作模型与四重安全边界", "".join(b))


DIAGRAMS = {
    "stateless-routing": stateless_routing,
    "code-mode-vs-toolcall": code_mode_vs_toolcall,
    "mrtr-oauth-flow": mrtr_oauth_flow,
    "skills-sync-lifecycle": skills_sync_lifecycle,
    "events-tasks-loop": events_tasks_loop,
    "harness-swarm-security": harness_swarm_security,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        if old.stem not in DIAGRAMS:
            old.unlink()
    for name, fn in DIAGRAMS.items():
        path = OUT / f"{name}.svg"
        content = fn()
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)}")


if __name__ == "__main__":
    main()
