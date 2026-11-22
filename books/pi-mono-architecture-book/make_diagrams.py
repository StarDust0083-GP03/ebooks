"""生成本书的静态主题自适应 SVG 架构图，无外部渲染器。"""
from pathlib import Path
from html import escape

OUT = Path(__file__).parent / 'diagrams'
OUT.mkdir(exist_ok=True)

# 主题自适应变量，同时保留独立打开、PDF 与 EPUB 的安全回退色
INK = 'var(--ink, #20201d)'
MUTED = 'var(--muted, #5e5d57)'
PAPER = 'var(--paper, #faf9f5)'
CARD = 'var(--paper-2, #f4f1e8)'
ACCENT = 'var(--brand, #1B365D)'
LINE = 'var(--line, #c4bfb0)'


def approx_width(text: str, font_size: float) -> float:
    """粗略估算中英文混排显示宽度，用于防止节点标签溢出。"""
    w = 0.0
    for ch in text:
        w += font_size * (1.02 if ord(ch) > 127 else 0.58)
    return w


def diagram(slug, title, desc, nodes, edges, height=480, labels=(), zones=()):
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 {height}" role="img" aria-labelledby="{slug}-title {slug}-desc" style="font-family:\'Noto Sans CJK SC\',system-ui,-apple-system,sans-serif">',
        f'<title id="{slug}-title">{escape(title)}</title>',
        f'<desc id="{slug}-desc">{escape(desc)}</desc>',
        f'<defs><marker id="{slug}-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="{ACCENT}"/></marker></defs>',
        f'<rect width="960" height="{height}" rx="6" fill="{PAPER}"/>',
    ]
    for x, y, w, h, name in zones:
        parts += [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{CARD}" fill-opacity="0.45" stroke="{LINE}" stroke-dasharray="6 5" stroke-width="1.5"/>',
            f'<text x="{x+16}" y="{y+26}" font-size="16" font-weight="500" fill="{MUTED}">{escape(name)}</text>',
        ]
    for path, dashed in edges:
        dash = ' stroke-dasharray="6 5"' if dashed else ''
        parts.append(f'<path d="{path}" fill="none" stroke="{ACCENT}" stroke-width="2"{dash} marker-end="url(#{slug}-arrow)"/>')
    for x, y, w, h, title_lines, sub in nodes:
        assert x >= 10 and x + w <= 950, f'{slug}: node out of x bounds ({x}, {w})'
        assert y >= 10 and y + h <= height - 10, f'{slug}: node out of y bounds ({y}, {h}, {height})'
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{CARD}" stroke="{ACCENT}" stroke-width="1.5"/>')
        title_size = 17 if len(title_lines) > 1 else 19
        for i, line in enumerate(title_lines):
            assert approx_width(line, title_size) <= w - 12, f'{slug}: title too wide for box: {line!r} in w={w}'
            if sub:
                base_y = y + (26 if len(title_lines) > 1 else 34)
            else:
                base_y = y + h / 2 + 6 - (len(title_lines) - 1) * 11
            parts.append(f'<text x="{x+w/2}" y="{base_y+i*22}" text-anchor="middle" font-size="{title_size}" font-weight="500" fill="{INK}">{escape(line)}</text>')
        if sub:
            sub_size = 14
            assert approx_width(sub, sub_size) <= w - 12, f'{slug}: sub too wide for box: {sub!r} in w={w}'
            parts.append(f'<text x="{x+w/2}" y="{y+h-15}" text-anchor="middle" font-size="{sub_size}" fill="{MUTED}">{escape(sub)}</text>')
    for x, y, label in labels:
        assert x + approx_width(label, 16) <= 955, f'{slug}: label may clip right edge: {label!r}'
        parts.append(f'<text x="{x}" y="{y}" font-size="16" fill="{MUTED}">{escape(label)}</text>')
    parts.append('</svg>')
    (OUT / f'{slug}.svg').write_text(''.join(parts))


def n(x, y, title, sub='', w=240, h=86):
    return (x, y, w, h, title.split('|'), sub)


diagram(
    'old-stack',
    'v0.9.4 的职责分层',
    '编码应用装配有状态 Agent 与 transport，ai 包内的循环调用模型和工具，SessionManager 保存线性日志。',
    [
        n(360, 28, '编码应用', 'coding-agent/main.ts'),
        n(360, 154, '有状态 Agent', 'agent/src/agent.ts'),
        n(360, 280, 'Transport → 工具循环', 'loop 位于 ai 包'),
        n(40, 280, '工具执行', '校验后顺序执行'),
        n(680, 280, '模型提供商', '统一流式接口'),
        n(680, 28, 'SessionManager', '线性 JSONL / 复制分支'),
    ],
    [
        ('M480 114 V154', False),
        ('M480 240 V280', False),
        ('M360 323 H280', False),
        ('M600 323 H680', False),
        ('M600 71 H680', False),
    ],
    height=436,
    labels=[(40, 410, '旧版已有参数校验、事件流和分支复制；尚无内置 Code Mode 扩展体系。')],
)

diagram(
    'current-stack',
    'v1.6.0 普通 CLI 的运行时地图',
    '交互入口进入 AgentSession，后者协同树形会话、Agent 循环和扩展工具，Code Mode 与 MCP 挂在工具编排层。',
    [
        n(350, 24, 'TUI / JSON / RPC', '或应用内 SDK', w=260),
        n(350, 148, 'AgentSession', '上下文、队列、扩展生命周期', w=260),
        n(36, 148, 'SessionManager', '活动分支、压缩、投影', w=250),
        n(674, 148, '资源与模型运行时', '设置、skills、providers', w=250),
        n(110, 276, 'Agent 循环', 'packages/agent', w=250),
        n(600, 276, '工具执行管线', 'pi-schema 校验与扩展钩子', w=250),
        n(110, 404, '模型提供商', 'packages/ai（含流式解析）', w=250),
        n(600, 404, 'Code Mode / MCP', '内置扩展及独立沙箱库', w=250),
    ],
    [
        ('M480 110 V148', False),
        ('M350 191 H286', False),
        ('M610 191 H674', False),
        ('M420 234 V248 Q420 256 412 256 H243 Q235 256 235 264 V276', False),
        ('M540 234 V248 Q540 256 548 256 H717 Q725 256 725 264 V276', False),
        ('M235 362 V404', False),
        ('M725 362 V404', False),
    ],
    height=524,
)

# 时序图：时间向下，水平消息表示跨层调用
slug = 'request-sequence'
parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 630" role="img" aria-labelledby="{slug}-title {slug}-desc" style="font-family:\'Noto Sans CJK SC\',system-ui,-apple-system,sans-serif">',
    f'<title id="{slug}-title">一次 Code Mode 请求时序</title>',
    f'<desc id="{slug}-desc">模型请求脚本，脚本经宿主工具管线调用真实工具，结果先回给脚本筛选，再向模型返回摘要。</desc>',
    f'<defs><marker id="{slug}-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="{ACCENT}"/></marker></defs>',
    f'<rect width="960" height="630" rx="6" fill="{PAPER}"/>',
]
for x, name in [(110, '模型'), (355, 'AgentSession'), (605, 'QuickJS 脚本'), (850, '工具管线')]:
    parts += [
        f'<rect x="{x-88}" y="24" width="176" height="54" rx="6" fill="{CARD}" stroke="{ACCENT}" stroke-width="1.5"/>',
        f'<text x="{x}" y="58" text-anchor="middle" font-size="18" font-weight="500" fill="{INK}">{name}</text>',
        f'<path d="M{x} 78 V604" stroke="{LINE}" stroke-dasharray="5 5" stroke-width="1.5" fill="none"/>',
    ]
for x1, x2, y, label, dashed in [
    (110, 355, 138, '请求 codemode', False),
    (355, 605, 202, '启动 worker / VM', False),
    (605, 850, 266, 'tools.read()', False),
    (850, 605, 330, '原始工具结果', True),
    (605, 355, 394, 'text(选定字段)', True),
    (355, 110, 458, '外层工具结果', True),
    (110, 355, 538, '下一轮响应', False),
]:
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    parts += [
        f'<path d="M{x1} {y} H{x2}" stroke="{ACCENT}" stroke-width="2"{dash} fill="none" marker-end="url(#{slug}-arrow)"/>',
        f'<text x="{(x1+x2)/2}" y="{y-11}" text-anchor="middle" font-size="16" fill="{INK}">{label}</text>',
    ]
parts.append('</svg>')
(OUT / f'{slug}.svg').write_text(''.join(parts))

diagram(
    'information-flow',
    '把信息筛选移到执行侧',
    '普通工具结果直接进入模型上下文；Code Mode 在执行侧完成组合与筛选，只交回选中输出。',
    [
        n(30, 74, '模型选择工具', w=182, h=76),
        n(270, 74, '真实工具', w=182, h=76),
        n(510, 74, '原始结果', w=182, h=76),
        n(750, 74, '模型筛选', w=182, h=76),
        n(20, 294, '模型写脚本', w=160, h=76),
        n(210, 294, '脚本发调用', w=160, h=76),
        n(400, 294, '真实工具', w=160, h=76),
        n(590, 294, '脚本筛选', w=160, h=76),
        n(780, 294, '模型读摘要', w=160, h=76),
    ],
    [
        ('M212 112 H270', False),
        ('M452 112 H510', False),
        ('M692 112 H750', False),
        ('M180 332 H210', False),
        ('M370 332 H400', False),
        ('M560 332 H590', True),
        ('M750 332 H780', False),
    ],
    height=444,
    labels=[
        (30, 40, '普通调用：原始结果直接跨回模型，由模型自行筛选'),
        (30, 256, 'Code Mode：原始数据留在执行侧，只有主动输出进入模型'),
        (30, 418, '两条路径都支持并行调用；差异集中在控制流与数据过滤发生的位置。'),
    ],
)

diagram(
    'sandbox-boundary',
    '脚本如何跨过宿主桥',
    '脚本在 worker 的 QuickJS/WASM 中运行，工具调用经消息桥回到主线程，再走参数校验与扩展钩子。',
    [
        n(64, 102, '工具适配器', 'ctx.executeTool', w=244),
        n(64, 296, '校验与扩展钩子', 'pi-schema 与真实工具', w=244),
        n(392, 102, 'worker 消息桥', 'JSON 请求与结果', w=216),
        n(672, 102, 'QuickJS / WASM', '脚本、prelude、受限全局', w=244),
        n(672, 296, '脚本输出', 'text / image / return', w=244),
    ],
    [
        ('M672 145 H608', False),
        ('M392 145 H308', False),
        ('M186 188 V296', False),
        ('M794 188 V296', False),
    ],
    height=470,
    labels=[(40, 444, 'VM 没有文件系统与网络权限，但注入的 bash 依然继承宿主进程权限。')],
    zones=[(36, 40, 300, 376, 'Pi 主线程'), (368, 40, 566, 376, 'worker 与独立 VM')],
)

diagram(
    'discovery',
    '工具发现的三条路径',
    '已注册工具可直接声明给模型、经脚本发现调用，或通过 tool_search 按需声明；hidden 工具不可达。',
    [
        n(340, 28, '工具注册与 exposure', '按策略控制可见性与调用面', w=280),
        n(36, 188, 'direct', '默认向模型声明', w=240),
        n(348, 188, 'codemode', '脚本发现后调用', w=264),
        n(684, 188, 'deferred', '按需加载声明', w=240),
        n(36, 344, '普通工具调用', '直接进入模型工具表', w=240),
        n(348, 344, 'searchTools / describe', '结果先返回脚本', w=264),
        n(684, 344, 'tool_search', '加载后直接调用', w=240),
    ],
    [
        ('M420 114 V138 Q420 146 412 146 H164 Q156 146 156 154 V188', False),
        ('M480 114 V188', False),
        ('M540 114 V138 Q540 146 548 146 H796 Q804 146 804 154 V188', False),
        ('M156 274 V344', False),
        ('M480 274 V344', False),
        ('M804 274 V344', False),
    ],
    height=492,
    labels=[(40, 468, 'MCP 的 codemode 与 deferred 均可被脚本调用；hidden 在两条间接路径中同样不可达。')],
)

diagram(
    'branch-store',
    '状态沿分支重放，外部文件独立演化',
    '从早期节点分叉时不继承另一条分支上的 store 写入，但文件系统副作用脱离会话分支单独存在。',
    [
        n(40, 174, '共同历史', 'store: inspected = 1'),
        n(360, 62, '分支 A 成功', 'store: inspected = 2'),
        n(680, 62, '继续 A', 'load 得到 2'),
        n(360, 284, '分支 B', '从共同历史分叉'),
        n(680, 284, '继续 B', 'load 得到 1'),
    ],
    [
        ('M280 205 H312 Q320 205 320 197 V113 Q320 105 328 105 H360', False),
        ('M280 237 H300 Q308 237 308 245 V319 Q308 327 316 327 H360', False),
        ('M600 105 H680', False),
        ('M600 327 H680', False),
    ],
    height=456,
    labels=[(40, 426, '分支 A 写出的文件在切回分支 B 后保持原样；脚本失败跳过 store 提交，也不撤销已写入文件。')],
)

diagram(
    'deployment',
    '同一脚本 API，三种计算部署位置',
    'Node/Bun 使用 worker thread，Cloudflare 可把 VM 放到独立 Worker 的 Durable Object，inline 模式在调用线程运行。',
    [
        n(40, 60, 'Node / Bun 宿主', '工具管线与 deadline', w=300),
        n(620, 60, 'worker thread', 'QuickJS VM（默认 256 MiB）', w=300),
        n(40, 218, 'Cloudflare 会话侧', '工具与调用记录', w=300),
        n(620, 218, '独立部署的 Worker', 'Durable Object 内 VM（32 MiB）', w=300),
        n(40, 376, 'Inline 宿主', '接受同步计算阻塞调用线程', w=300),
        n(620, 376, '同一线程的 VM', 'interrupt budget 约束', w=300),
    ],
    [
        ('M340 103 H620', False),
        ('M340 261 H620', False),
        ('M340 419 H620', False),
    ],
    height=506,
    labels=[(415, 84, '线程消息'), (410, 242, 'Workers RPC'), (420, 400, '直接调用')],
)

diagram(
    'durable-replay',
    '确定性重放机制，脱离内存快照',
    '首次运行先将工具结果写入 journal 再交付脚本；重启后在相同代码与确定性条件下按序重放结果，再继续执行。',
    [
        n(30, 68, '相同代码与环境', 'seed、虚拟时间、VM 指纹', w=264),
        n(348, 68, '首次运行', '发出嵌套工具任务', w=264),
        n(666, 68, '先记账再交付', '结果顺序写入 journal', w=264),
        n(30, 294, '进程重启', '恢复 Harness 与任务', w=264),
        n(348, 294, '重新运行脚本', '按序喂回已记录结果', w=264),
        n(666, 294, '关联任务并继续', '未完成调用服从 replay 策略', w=264),
    ],
    [
        ('M294 111 H348', False),
        ('M612 111 H666', False),
        ('M294 337 H348', False),
        ('M612 337 H666', False),
        ('M798 154 V256 Q798 264 790 264 H488 Q480 264 480 272 V294', True),
    ],
    height=468,
    labels=[(30, 434, '外部副作用仍需服务端幂等；普通 CLI 的单次脚本执行不含这套持久重放保证。')],
)

diagram(
    'architecture-comparison',
    'v0.9.4 与 v1.6.0 普通 CLI 的分层对照',
    'v0.9.4 的循环位于 ai 包；v1.6.0 由 AgentSession 管理会话，agent 包拥有循环，ai 专注协议接入与流式解析，pi-schema 承担统一校验。',
    [
        n(64, 78, '编码应用', '旧版 main / TUI', w=336),
        n(64, 232, 'Agent + Transport', '状态与传输抽象', w=336),
        n(64, 386, 'ai：循环与模型接入', '工具循环位于 ai 包内', w=336),
        n(560, 78, 'AgentSession / 资源 / 扩展', '树形会话、诊断去重与上下文投影', w=336),
        n(560, 232, 'agent：Agent 与工具循环', '默认并行与通用嵌套契约', w=336),
        n(560, 386, 'ai + pi-schema', '多提供商、增量解析、闭包校验', w=336),
    ],
    [
        ('M232 164 V232', False),
        ('M232 318 V386', False),
        ('M728 164 V232', False),
        ('M728 318 V386', False),
    ],
    height=544,
    labels=[
        (64, 42, 'v0.9.4 架构分层'),
        (560, 42, 'v1.6.0 普通 CLI 分层'),
        (64, 518, 'Code Mode 挂在工具编排层；Durable、env、chord 属于独立运行链。'),
    ],
)

diagram(
    'cli-package-dependencies',
    'v1.6.0 普通 CLI 的内部包依赖',
    'coding-agent 直接依赖 agent-core、ai、tui、codemode、mcp；ai 与 agent-core 共享 pi-schema，ai 依赖 telemetry。',
    [
        n(36, 236, 'coding-agent', '普通 CLI / SDK', w=220, h=116),
        n(370, 28, 'ai', '模型协议与流式解析', w=220, h=82),
        n(370, 144, 'agent-core', 'Agent 与工具循环', w=220, h=82),
        n(370, 260, 'tui', '只读缓存终端渲染', w=220, h=82),
        n(370, 376, 'codemode', '独立 QuickJS 沙箱', w=220, h=82),
        n(370, 492, 'mcp', '独立 MCP 客户端', w=220, h=82),
        n(700, 28, 'telemetry', '观测契约', w=216, h=82),
        n(700, 144, 'pi-schema', '零 eval 闭包校验', w=216, h=82),
    ],
    [
        ('M256 254 H298 Q306 254 306 246 V77 Q306 69 314 69 H370', False),
        ('M256 274 H314 Q322 274 322 266 V193 Q322 185 330 185 H370', False),
        ('M256 296 H370', False),
        ('M256 318 H322 Q330 318 330 326 V409 Q330 417 338 417 H370', False),
        ('M256 336 H306 Q314 336 314 344 V525 Q314 533 322 533 H370', False),
        ('M480 144 V110', False),
        ('M590 69 H700', False),
        ('M590 88 H640 Q648 88 648 96 V164 Q648 172 656 172 H700', False),
        ('M590 192 H700', False),
    ],
    height=636,
    labels=[(36, 610, '实线表示 package.json 声明的直接内部依赖；coding-agent 不依赖 durable、env 或 chord。')],
)

diagram(
    'durable-package-dependencies',
    'Durable 与远程执行环境的内部包关系',
    'env 依赖 durable 与 chord；durable 依赖 ai、codemode、chord、pi-schema 与 telemetry，独立于普通 CLI。',
    [
        n(350, 28, 'env', '远程 ExecutionEnv / Rust daemon', w=260),
        n(350, 174, 'durable', '实验性持久任务 Harness', w=260),
        n(36, 336, 'ai', '模型接入', w=196, h=86),
        n(264, 336, 'codemode', '确定性 QuickJS', w=196, h=86),
        n(492, 336, 'chord', '文档状态与 RPC', w=196, h=86),
        n(720, 336, 'pi-schema|telemetry', '校验与观测', w=204, h=86),
    ],
    [
        ('M480 114 V174', False),
        ('M610 71 H644 Q652 71 652 79 V292 Q652 300 644 300 H598 Q590 300 590 308 V336', False),
        ('M400 260 V284 Q400 292 392 292 H142 Q134 292 134 300 V336', False),
        ('M446 260 V296 Q446 304 438 304 H370 Q362 304 362 312 V336', False),
        ('M514 260 V296 Q514 304 522 304 H576 Q584 304 584 312 V336', False),
        ('M560 260 V284 Q560 292 568 292 H814 Q822 292 822 300 V336', False),
    ],
    height=492,
    labels=[(36, 464, '依据 v1.6.0 各包 package.json。普通 CLI 不会自动继承 Durable 的持久重放保证。')],
)

diagram(
    'streaming-and-render-cache',
    'v1.5.0 至 v1.6.0 流式解析与 TUI 只读渲染缓存链路',
    '大型工具调用经 StreamingJsonParser 增量解析并按帧合并更新；TUI Container 对未变子树返回同一只读数组引用，避免长会话滚动逐层拷贝。',
    [
        n(48, 86, 'Provider Delta 流', '增量到达的参数片段', w=258),
        n(351, 86, 'StreamingJsonParser', '线性 append 与拷贝信用', w=258),
        n(654, 86, 'ToolExecutionComponent', 'argsPending 按帧合并渲染', w=258),
        n(48, 294, '子组件 render(width)', '返回 readonly string[]', w=258),
        n(351, 294, 'renderChildLines()', '比对宽度、子引用与行内容', w=258),
        n(654, 294, 'Container 缓存复用', '未变返回原引用；改动前先拷贝', w=258),
    ],
    [
        ('M306 129 H351', False),
        ('M609 129 H654', False),
        ('M783 172 V216 Q783 224 775 224 H185 Q177 224 177 232 V294', True),
        ('M306 337 H351', False),
        ('M609 337 H654', False),
    ],
    height=468,
    labels=[
        (40, 442, '流式解析消除 O(N^2) 重解析；Container 引用复用避免长会话全屏滚动打满单核 CPU。'),
    ],
    zones=[
        (32, 34, 896, 162, '流式工具参数解析链路（packages/ai + coding-agent，v1.5.0）'),
        (32, 242, 896, 162, 'TUI 只读渲染与祖先缓存链路（packages/tui，v1.6.0）'),
    ],
)

print(f'Generated {len(list(OUT.glob("*.svg")))} theme-aware SVG diagrams with zero clipping.')
