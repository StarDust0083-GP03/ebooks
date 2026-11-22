#!/usr/bin/env python3
"""Generate and refresh OpenSandbox v1.3.0-rc.2 architecture SVGs."""

from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "book/diagrams"

COMMON_STYLE = (
    'text{font-family:"Noto Sans CJK SC",system-ui,sans-serif;fill:#252820}'
    '.name{font-size:20px;font-weight:650}'
    '.label{font-size:16px;fill:#29564c;font-weight:500}'
    '.sub{font-size:15px;fill:#586354}'
    '.warn{font-size:15px;fill:#8c4a1c;font-weight:550}'
    '.node{fill:#faf7ef;stroke:#a5ad9e;stroke-width:1.4}'
    '.active{fill:#edf2e9;stroke:#29564c;stroke-width:2}'
    '.amber{fill:#f9f1e4;stroke:#b86b2a;stroke-width:1.6}'
    '.edge{fill:none;stroke:#29564c;stroke-width:1.6}'
    '.return{fill:none;stroke:#72846b;stroke-width:1.5;stroke-dasharray:6 5}'
    '.warnwire{fill:none;stroke:#b86b2a;stroke-width:1.6;stroke-dasharray:5 4}'
)

LIFECYCLE_SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1060" height="980" viewBox="0 0 1060 980" role="img" aria-labelledby="lifecycle-title lifecycle-desc">
<title id="lifecycle-title">BatchSandbox：运行、Completed 终态与暂停循环</title>
<desc id="lifecycle-desc">BatchSandbox 正常退出进入粘滞 Completed 终态并映射为 Terminated；暂停或恢复失败保留可重试状态。</desc>
<style>{COMMON_STYLE}</style>
<defs>
<marker id="lifecycle-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#29564c"/></marker>
<marker id="lifecycle-warn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#b86b2a"/></marker>
</defs>
<rect width="1060" height="980" fill="#faf7ef"/>
<text x="40" y="36" class="sub">Kubernetes BatchSandbox（v1.3.0-rc.2）· 粘滞 Completed 终态与可重试暂停循环</text>

<path class="edge" d="M230 130 H320" marker-end="url(#lifecycle-arrow)"/>
<text x="275" y="114" class="label" text-anchor="middle">就绪</text>

<path class="edge" d="M550 118 H680" marker-end="url(#lifecycle-arrow)"/>
<text x="615" y="102" class="label" text-anchor="middle">exit 0 正常退出</text>

<path class="edge" d="M550 152 H592 Q600 152 600 160 V332 Q600 340 608 340 H680" marker-end="url(#lifecycle-arrow)"/>
<text x="614" y="282" class="label">异常退出</text>

<path class="edge" d="M435 170 V306" marker-end="url(#lifecycle-arrow)"/>
<text x="450" y="246" class="label">请求暂停</text>

<path class="return" d="M320 348 H278 Q270 348 270 340 V222 a8 8 0 0 1 0 -16 V148 Q270 140 278 140 H320" marker-end="url(#lifecycle-arrow)"/>
<text x="82" y="286" class="sub">PauseFailed 但仍可用</text>

<path class="edge" d="M435 390 V526" marker-end="url(#lifecycle-arrow)"/>
<text x="450" y="446" class="label">镜像推送成功</text>
<text x="450" y="472" class="sub">释放 Pod 与池槽位</text>

<path class="edge" d="M550 568 H660" marker-end="url(#lifecycle-arrow)"/>
<text x="605" y="550" class="label" text-anchor="middle">恢复</text>

<path class="return" d="M790 610 V668 Q790 676 782 676 H443 Q435 676 435 668 V610" marker-end="url(#lifecycle-arrow)"/>
<text x="612" y="656" class="sub" text-anchor="middle">ResumeFailed：仍可重试（RV 校验防覆盖）</text>

<path class="edge" d="M880 610 V738 Q880 746 872 746 H88 Q80 746 80 738 V218 Q80 210 88 210 H352 Q360 210 360 202 V170" marker-end="url(#lifecycle-arrow)"/>
<text x="96" y="726" class="sub">恢复就绪：sandboxId 不变；必须重新解析 endpoint</text>

<g data-box="1" id="s-pending"><rect class="node" x="40" y="88" width="190" height="84" rx="6"/>
<text x="135" y="122" class="name" text-anchor="middle">Pending</text>
<text x="135" y="150" class="sub" text-anchor="middle">创建与分配中</text></g>

<g data-box="1" id="s-running"><rect class="active" x="320" y="88" width="230" height="84" rx="6"/>
<text x="435" y="122" class="name" text-anchor="middle">Running</text>
<text x="435" y="150" class="sub" text-anchor="middle">CRD phase = Succeed</text></g>

<g data-box="1" id="s-completed"><rect class="amber" x="680" y="88" width="330" height="100" rx="6"/>
<text x="845" y="120" class="name" text-anchor="middle">Terminated（Completed）</text>
<text x="845" y="146" class="sub" text-anchor="middle">粘滞终态 · SANDBOX_COMPLETED</text>
<text x="845" y="172" class="warn" text-anchor="middle">拒绝 pause / resume，跳过 Pod 替换</text></g>

<g data-box="1" id="s-failed"><rect class="node" x="680" y="300" width="330" height="88" rx="6"/>
<text x="845" y="334" class="name" text-anchor="middle">Failed</text>
<text x="845" y="362" class="sub" text-anchor="middle">不可恢复运行时失败</text></g>

<g data-box="1" id="s-pausing"><rect class="node" x="320" y="306" width="230" height="84" rx="6"/>
<text x="435" y="340" class="name" text-anchor="middle">Pausing</text>
<text x="435" y="368" class="sub" text-anchor="middle">正在提交 rootfs（full-only）</text></g>

<g data-box="1" id="s-paused"><rect class="active" x="320" y="526" width="230" height="84" rx="6"/>
<text x="435" y="560" class="name" text-anchor="middle">Paused</text>
<text x="435" y="588" class="sub" text-anchor="middle">暂停完成，已释放算力</text></g>

<g data-box="1" id="s-resuming"><rect class="node" x="660" y="526" width="320" height="84" rx="6"/>
<text x="820" y="560" class="name" text-anchor="middle">Resuming</text>
<text x="820" y="588" class="sub" text-anchor="middle">按最新快照镜像重建 Pod</text></g>

<line x1="40" y1="812" x2="1020" y2="812" stroke="#d9d2c3"/>
<text x="40" y="846" class="sub">1. v1.3.0-rc.2 新增粘滞 Completed 相位，修复主容器 exit 0 后从 Succeed 回落到 Pending 的状态悬空。</text>
<text x="40" y="878" class="sub">2. PauseFailed / ResumeFailed 属于 Condition 而非终态；状态写入带 resourceVersion 前置条件防止陈旧视图覆盖。</text>
<text x="40" y="910" class="sub">3. DELETE 204 只表示接受删除，清理后 GET 404 不等于确认进程终止；到期删除带 UID 与 resourceVersion 校验。</text>
</svg>
'''

EXECD_INIT_SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="860" viewBox="0 0 1100 860" role="img" aria-labelledby="execd-init-title execd-init-desc">
<title id="execd-init-title">OSEP-0018：execd-as-init 默认拓扑与进程降权链</title>
<desc id="execd-init-desc">对比经典后台启动与 EXECD_INIT=1 默认模式，并展示 opensandbox-launcher 在 fork 到 execve 之间的顺序化降权步骤。</desc>
<style>{COMMON_STYLE}</style>
<defs>
<marker id="ei-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#29564c"/></marker>
<marker id="ei-warn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#b86b2a"/></marker>
</defs>
<rect width="1100" height="860" fill="#faf7ef"/>
<text x="40" y="36" class="sub">OSEP-0018（v1.3.0 默认开启）：runtime.execd_run_as_init = true → 注入 EXECD_INIT=1</text>

<!-- Left panel: Legacy mode -->
<rect x="40" y="64" width="460" height="430" rx="8" fill="#f5f1e6" stroke="#d9d2c3" stroke-width="1.2"/>
<text x="64" y="98" class="name">经典拓扑（execd_run_as_init = false）</text>
<text x="64" y="124" class="sub">v1.2.1 及更早版本的默认模式</text>

<path class="edge" d="M270 220 V268" marker-end="url(#ei-arrow)"/>
<text x="284" y="250" class="sub">后台拉起 &amp; wait</text>

<path class="edge" d="M160 356 V396" marker-end="url(#ei-arrow)"/>
<path class="edge" d="M380 356 V396" marker-end="url(#ei-arrow)"/>

<g data-box="1" id="leg-boot"><rect class="node" x="90" y="148" width="360" height="72" rx="6"/>
<text x="270" y="180" class="name" text-anchor="middle">bootstrap.sh（PID 1）</text>
<text x="270" y="204" class="sub" text-anchor="middle">后台启动 execd，再运行 entrypoint</text></g>

<g data-box="1" id="leg-execd"><rect class="node" x="64" y="272" width="192" height="84" rx="6"/>
<text x="160" y="306" class="name" text-anchor="middle">execd 守护进程</text>
<text x="160" y="334" class="sub" text-anchor="middle">仅管 /command 子树</text></g>

<g data-box="1" id="leg-entry"><rect class="amber" x="284" y="272" width="192" height="84" rx="6"/>
<text x="380" y="306" class="name" text-anchor="middle">用户 entrypoint</text>
<text x="380" y="334" class="warn" text-anchor="middle">不受降权地板包裹</text></g>

<g data-box="1" id="leg-cmd"><rect class="node" x="64" y="400" width="192" height="68" rx="6"/>
<text x="160" y="438" class="sub" text-anchor="middle">API 命令与 PTY</text></g>

<g data-box="1" id="leg-drift"><rect class="amber" x="284" y="400" width="192" height="68" rx="6"/>
<text x="380" y="428" class="warn" text-anchor="middle">孤儿易变僵尸进程</text>
<text x="380" y="452" class="sub" text-anchor="middle">能力上报 degraded</text></g>

<!-- Right panel: New default init mode -->
<rect x="530" y="64" width="530" height="430" rx="8" fill="#edf2e9" stroke="#29564c" stroke-width="1.6"/>
<text x="554" y="98" class="name">默认拓扑（EXECD_INIT=1，execd --init）</text>
<text x="554" y="124" class="sub">bootstrap.sh 末尾执行 exec execd --init -- &lt;entrypoint&gt;</text>

<path class="edge" d="M795 256 V296" marker-end="url(#ei-arrow)"/>
<text x="808" y="282" class="label">统一 waitid reaper</text>

<path class="edge" d="M675 372 V408" marker-end="url(#ei-arrow)"/>
<path class="edge" d="M915 372 V408" marker-end="url(#ei-arrow)"/>

<g data-box="1" id="init-execd"><rect class="active" x="560" y="148" width="470" height="108" rx="6"/>
<text x="795" y="180" class="name" text-anchor="middle">execd --init（容器 PID 1 / FSB subreaper）</text>
<text x="795" y="208" class="sub" text-anchor="middle">单一 waitid 回收孤儿 · 转发 SIGTERM/HUP/USR1/USR2/WINCH</text>
<text x="795" y="234" class="sub" text-anchor="middle">non-dumpable 保护 /proc/1/environ · 透传退出码</text></g>

<g data-box="1" id="init-launcher"><rect class="node" x="560" y="300" width="470" height="72" rx="6"/>
<text x="795" y="330" class="name" text-anchor="middle">opensandbox-launcher（可选加固地板）</text>
<text x="795" y="356" class="sub" text-anchor="middle">由 EXECD_ISOLATION_CONFIG（TOML）控制，全量覆盖用户进程</text></g>

<g data-box="1" id="init-entry"><rect class="node" x="560" y="412" width="225" height="60" rx="6"/>
<text x="672" y="447" class="sub" text-anchor="middle">受控用户 entrypoint</text></g>

<g data-box="1" id="init-cmd"><rect class="node" x="805" y="412" width="225" height="60" rx="6"/>
<text x="917" y="447" class="sub" text-anchor="middle">/command、会话与 PTY</text></g>

<!-- Bottom panel: Launcher hardening chain -->
<rect x="40" y="524" width="1020" height="218" rx="8" fill="#faf7ef" stroke="#a5ad9e" stroke-width="1.4"/>
<text x="64" y="558" class="name">opensandbox-launcher 顺序敏感降权链（fork 之后、execve 之前）</text>
<text x="64" y="584" class="sub">每层按序收敛且 fail-open；缺内核特性（如 FSB guest 缺 Landlock）上报 unsupported 而不阻断启动</text>

<path class="edge" d="M196 654 H222" marker-end="url(#ei-arrow)"/>
<path class="edge" d="M362 654 H388" marker-end="url(#ei-arrow)"/>
<path class="edge" d="M528 654 H554" marker-end="url(#ei-arrow)"/>
<path class="edge" d="M694 654 H720" marker-end="url(#ei-arrow)"/>
<path class="edge" d="M860 654 H886" marker-end="url(#ei-arrow)"/>

<g data-box="1" id="st-1"><rect class="node" x="64" y="616" width="132" height="76" rx="6"/>
<text x="130" y="646" class="label" text-anchor="middle">1. 剥离凭据</text>
<text x="130" y="672" class="sub" text-anchor="middle">清 execd env</text></g>

<g data-box="1" id="st-2"><rect class="node" x="226" y="616" width="136" height="76" rx="6"/>
<text x="294" y="646" class="label" text-anchor="middle">2. 裁剪能力</text>
<text x="294" y="672" class="sub" text-anchor="middle">bounding set</text></g>

<g data-box="1" id="st-3"><rect class="node" x="392" y="616" width="136" height="76" rx="6"/>
<text x="460" y="646" class="label" text-anchor="middle">3. 禁止提权</text>
<text x="460" y="672" class="sub" text-anchor="middle">no_new_privs</text></g>

<g data-box="1" id="st-4"><rect class="node" x="558" y="616" width="136" height="76" rx="6"/>
<text x="626" y="646" class="label" text-anchor="middle">4. 设身份与能力</text>
<text x="626" y="672" class="sub" text-anchor="middle">UID / ambient</text></g>

<g data-box="1" id="st-5"><rect class="node" x="724" y="616" width="136" height="76" rx="6"/>
<text x="792" y="646" class="label" text-anchor="middle">5. 文件系统边界</text>
<text x="792" y="672" class="sub" text-anchor="middle">Landlock 规则</text></g>

<g data-box="1" id="st-6"><rect class="active" x="890" y="616" width="146" height="76" rx="6"/>
<text x="963" y="646" class="label" text-anchor="middle">6. seccomp 收口</text>
<text x="963" y="672" class="sub" text-anchor="middle">execve 用户程序</text></g>

<line x1="40" y1="772" x2="1060" y2="772" stroke="#d9d2c3"/>
<text x="40" y="804" class="sub">注：PID 1 信号盾使沙箱内 kill -9 1 无效，但 SIGTERM 故意不屏蔽并转发给 entrypoint，供 K8s Restart 回收策略使用。</text>
<text x="40" y="834" class="sub">源码入口：components/execd/pkg/runtime/initmode_linux.go、hardening_linux.go、docs/guides/privilege-reduction.md</text>
</svg>
'''

SNAPSHOT_MODES_SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="840" viewBox="0 0 1100 840" role="img" aria-labelledby="snap-title snap-desc">
<title id="snap-title">快照模式路由：full 与 rootfsOnly 在三种后端的落点</title>
<desc id="snap-desc">展示 create_snapshot(mode=full|rootfsOnly) 在 Docker、Kubernetes 与 Fast Sandbox（S3 原生和 OCI）中的校验门禁、制品内容与恢复语义差异。</desc>
<style>{COMMON_STYLE}</style>
<defs>
<marker id="sm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#29564c"/></marker>
<marker id="sm-warn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#b86b2a"/></marker>
</defs>
<rect width="1100" height="840" fill="#faf7ef"/>
<text x="40" y="36" class="sub">v1.3.0-rc.2 公开快照 API：create_snapshot(mode="full" | "rootfsOnly") 全路径对照</text>

<!-- Top API box -->
<g data-box="1" id="api-req"><rect class="active" x="280" y="64" width="540" height="80" rx="6"/>
<text x="550" y="96" class="name" text-anchor="middle">POST /v1/sandboxes/{{id}}/snapshots</text>
<text x="550" y="124" class="sub" text-anchor="middle">CreateSnapshotRequest.mode 默认 "full"；五大 SDK 均已透传</text></g>

<!-- Branch lines from top box -->
<path class="edge" d="M400 144 V184 H195 V218" marker-end="url(#sm-arrow)"/>
<path class="edge" d="M550 144 V218" marker-end="url(#sm-arrow)"/>
<path class="edge" d="M700 144 V184 H875 V218" marker-end="url(#sm-arrow)"/>

<!-- Col 1: Docker / K8s -->
<g data-box="1" id="col-dk"><rect class="node" x="40" y="222" width="310" height="128" rx="6"/>
<text x="195" y="256" class="name" text-anchor="middle">Docker / Kubernetes 后端</text>
<text x="195" y="286" class="sub" text-anchor="middle">mode = "full"：commit 为 OCI 镜像</text>
<text x="195" y="314" class="warn" text-anchor="middle">mode = "rootfsOnly"：预检拒绝 409</text>
<text x="195" y="338" class="sub" text-anchor="middle">UNSUPPORTED_RUNTIME</text></g>

<!-- Col 2: FSB full -->
<g data-box="1" id="col-fsb-full"><rect class="node" x="395" y="222" width="310" height="128" rx="6"/>
<text x="550" y="256" class="name" text-anchor="middle">Fast Sandbox · mode = "full"</text>
<text x="550" y="286" class="sub" text-anchor="middle">rootfs + vmstate + guest 内存</text>
<text x="550" y="314" class="sub" text-anchor="middle">暂停/恢复检查点始终保持 full-only</text>
<text x="550" y="338" class="sub" text-anchor="middle">同架构与同 VMM 版本（1.16.1/1.17.0）</text></g>

<!-- Col 3: FSB rootfsOnly -->
<g data-box="1" id="col-fsb-rfs"><rect class="active" x="750" y="222" width="310" height="128" rx="6"/>
<text x="905" y="256" class="name" text-anchor="middle">Fast Sandbox · rootfsOnly</text>
<text x="905" y="286" class="sub" text-anchor="middle">同时覆盖原生 S3 与 OCI/OverlayBD</text>
<text x="905" y="314" class="sub" text-anchor="middle">服务端去掉 OCI 预检，交由 FastPath</text>
<text x="905" y="338" class="sub" text-anchor="middle">节点自推导资质并 fail-closed 复核</text></g>

<!-- Arrows down to second row -->
<path class="edge" d="M195 350 V426" marker-end="url(#sm-arrow)"/>
<text x="210" y="394" class="sub">仅文件系统</text>

<path class="edge" d="M550 350 V426" marker-end="url(#sm-arrow)"/>
<text x="565" y="394" class="label">完整内存恢复</text>

<path class="edge" d="M840 350 V426" marker-end="url(#sm-arrow)"/>
<text x="772" y="394" class="label" text-anchor="end">节点合格</text>

<path class="warnwire" d="M970 350 V426" marker-end="url(#sm-warn)"/>
<text x="984" y="394" class="warn">不合格</text>

<!-- Second row boxes -->
<g data-box="1" id="res-dk"><rect class="node" x="40" y="430" width="310" height="132" rx="6"/>
<text x="195" y="464" class="name" text-anchor="middle">恢复行为（容器）</text>
<text x="195" y="494" class="sub" text-anchor="middle">Docker：本地 OCI 镜像，新容器启动</text>
<text x="195" y="520" class="sub" text-anchor="middle">K8s：拉取推送镜像，重建 Pod</text>
<text x="195" y="546" class="sub" text-anchor="middle">均不保留进程内存现场</text></g>

<g data-box="1" id="res-full"><rect class="node" x="395" y="430" width="310" height="132" rx="6"/>
<text x="550" y="464" class="name" text-anchor="middle">恢复行为（Full）</text>
<text x="550" y="494" class="sub" text-anchor="middle">恢复 vmstate 与内存页</text>
<text x="550" y="520" class="sub" text-anchor="middle">运行中进程继续执行，校准墙钟</text>
<text x="550" y="546" class="sub" text-anchor="middle">运行时恢复数十毫秒级，制品体积较大</text></g>

<g data-box="1" id="res-rfs-ok"><rect class="active" x="726" y="430" width="194" height="132" rx="6"/>
<text x="823" y="464" class="name" text-anchor="middle">冷启动恢复</text>
<text x="823" y="494" class="sub" text-anchor="middle">KB 级可写层制品</text>
<text x="823" y="520" class="sub" text-anchor="middle">丢弃 RAM 与进程</text>
<text x="823" y="546" class="sub" text-anchor="middle">重跑 entrypoint（秒级）</text></g>

<g data-box="1" id="res-rfs-fail"><rect class="amber" x="936" y="430" width="124" height="132" rx="6"/>
<text x="998" y="476" class="warn" text-anchor="middle">快照失败</text>
<text x="998" y="506" class="sub" text-anchor="middle">绝不静默</text>
<text x="998" y="532" class="sub" text-anchor="middle">降级 full</text></g>

<!-- Bottom comparison bar -->
<rect x="40" y="596" width="1020" height="114" rx="6" fill="#f5f1e6" stroke="#d9d2c3" stroke-width="1.2"/>
<text x="64" y="630" class="name">关键约束与兼容性边界</text>
<text x="64" y="660" class="sub">· 原生 S3 模板需在构建期注入 guest-control 并校验 freeze/thaw，旧模板捕获 rootfsOnly 会 fail-closed 失败。</text>
<text x="64" y="688" class="sub">· Fast Sandbox 恢复与崩溃重启后会校准 guest 墙钟（Firecracker 无 RTC），避免 TLS 证书和令牌误判过期。</text>

<line x1="40" y1="744" x2="1060" y2="744" stroke="#d9d2c3"/>
<text x="40" y="776" class="sub">源码入口：server/opensandbox_server/services/fast_sandbox/snapshot_runtime.py、api/schema.py、oseps/0028</text>
<text x="40" y="806" class="sub">注：OSEP-0028 正在落地 RootfsOnly 创建时经 resourceLimits 覆写 CPU/内存的能力，当前作为提案与实现进行中。</text>
</svg>
'''

EGRESS_VAULT_SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="860" viewBox="0 0 1100 860" role="img" aria-labelledby="ev-title ev-desc">
<title id="ev-title">出向网络两层执行、Credential Vault Exchange 与 Subject 状态机</title>
<desc id="ev-desc">展示 DNS 代理、nftables 包过滤、平台规则优先级、Vault Exchange 动态凭据解析及 Fast Sandbox subject 状态机。</desc>
<style>{COMMON_STYLE}</style>
<defs>
<marker id="ev-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#29564c"/></marker>
<marker id="ev-warn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 1 L9 5 L0 9" fill="#b86b2a"/></marker>
</defs>
<rect width="1100" height="860" fill="#faf7ef"/>
<text x="40" y="36" class="sub">出向安全三层：DNS 过滤 → nftables 包级默认拒绝 → 可选透明 MITM 与动态 Vault Exchange</text>

<!-- Row 1: Sandbox -> DNS & nftables -> MITM -> External -->
<path class="edge" d="M220 156 H286" marker-end="url(#ev-arrow)"/>
<text x="253" y="140" class="label" text-anchor="middle">出向包</text>

<path class="edge" d="M550 156 H616" marker-end="url(#ev-arrow)"/>
<text x="583" y="140" class="label" text-anchor="middle">80/443</text>

<path class="edge" d="M870 156 H926" marker-end="url(#ev-arrow)"/>
<text x="898" y="140" class="label" text-anchor="middle">已认证</text>

<!-- Vault Exchange connection -->
<path class="edge" d="M745 240 V318" marker-end="url(#ev-arrow)"/>
<text x="760" y="286" class="label">UDS / 受信 HTTPS 动态换取</text>

<!-- Platform rules connection -->
<path class="return" d="M418 318 V240" marker-end="url(#ev-arrow)"/>
<text x="434" y="286" class="sub">优先级压制用户策略</text>

<g data-box="1" id="ev-sbx"><rect class="node" x="40" y="88" width="180" height="148" rx="6"/>
<text x="130" y="124" class="name" text-anchor="middle">沙箱工作负载</text>
<text x="130" y="156" class="sub" text-anchor="middle">无 NET_ADMIN 权限</text>
<text x="130" y="184" class="sub" text-anchor="middle">仅持假密钥/占位符</text>
<text x="130" y="212" class="sub" text-anchor="middle">不接触真实凭据</text></g>

<g data-box="1" id="ev-l12"><rect class="active" x="290" y="88" width="260" height="148" rx="6"/>
<text x="420" y="124" class="name" text-anchor="middle">DNS 代理 + nftables</text>
<text x="420" y="156" class="sub" text-anchor="middle">尾点归一化（pypi.org.）</text>
<text x="420" y="184" class="sub" text-anchor="middle">DNS 学得 IP 带有限租约</text>
<text x="420" y="212" class="sub" text-anchor="middle">默认丢弃未标记出向包</text></g>

<g data-box="1" id="ev-mitm"><rect class="active" x="620" y="88" width="250" height="148" rx="6"/>
<text x="745" y="124" class="name" text-anchor="middle">透明 MITM 代理</text>
<text x="745" y="156" class="sub" text-anchor="middle">仅在绑定匹配时注入</text>
<text x="745" y="184" class="sub" text-anchor="middle">路径歧义守卫（403/断开）</text>
<text x="745" y="212" class="sub" text-anchor="middle">异常一律 fail-closed</text></g>

<g data-box="1" id="ev-ext"><rect class="node" x="930" y="88" width="130" height="148" rx="6"/>
<text x="995" y="146" class="name" text-anchor="middle">上游服务</text>
<text x="995" y="178" class="sub" text-anchor="middle">模型 API</text>
<text x="995" y="204" class="sub" text-anchor="middle">制品库</text></g>

<!-- Row 2: Platform Rules + Vault Exchange -->
<g data-box="1" id="ev-rules"><rect class="node" x="40" y="322" width="520" height="150" rx="6"/>
<text x="60" y="354" class="name">规则求值顺序与平台覆写</text>
<text x="60" y="384" class="label">1. deny.always → 2. allow.always → 3. ports.cap</text>
<text x="60" y="414" class="sub">4. 用户 networkPolicy（首条命中） → 5. defaultAction</text>
<text x="60" y="444" class="sub">每分钟热重载，校验失败保留旧规则；FSB 支持 TCP 端口规则</text></g>

<g data-box="1" id="ev-vault"><rect class="amber" x="590" y="322" width="470" height="150" rx="6"/>
<text x="610" y="354" class="name">Credential Vault Exchange（OSEP-0025）</text>
<text x="610" y="384" class="sub">· 按请求实时解析动态凭据，Vault 不落盘、不缓存明文</text>
<text x="610" y="414" class="sub">· 透传出向头给解析器（≤64 头、单值 ≤8 KiB，超限 407）</text>
<text x="610" y="444" class="warn">· 支持 SigV4 / ACS 重签；调用方 Ed25519 鉴权仍待实现</text></g>

<!-- Row 3: Fast Sandbox Subject State Machine -->
<rect x="40" y="504" width="1020" height="232" rx="8" fill="#f5f1e6" stroke="#d9d2c3" stroke-width="1.2"/>
<text x="64" y="538" class="name">Fast Sandbox 共享 Egress · Subject 状态机与实验性 Credential-Bound 排水围栏</text>
<text x="64" y="564" class="sub">每 Fastlet 一个共享 egress 进程，按 attachment 源 IP 分发，策略可以迟到但绝不提前放行</text>

<path class="edge" d="M224 632 H330" marker-end="url(#ev-arrow)"/>
<text x="277" y="616" class="label" text-anchor="middle">SET_BINDING</text>

<path class="edge" d="M526 632 H706" marker-end="url(#ev-arrow)"/>
<text x="616" y="616" class="label" text-anchor="middle">data-plane-ready</text>
<text x="616" y="656" class="sub" text-anchor="middle">Hook 等 ≤50 ms 确认</text>

<path class="warnwire" d="M865 676 V706 H144 V676" marker-end="url(#ev-warn)"/>
<text x="504" y="698" class="warn" text-anchor="middle">REMOVE_BINDING / Unload / Suspend：排水围栏 + 拆连接（未确认前禁止复用源 IP）</text>

<g data-box="1" id="sub-abs"><rect class="node" x="64" y="592" width="160" height="80" rx="6"/>
<text x="144" y="626" class="name" text-anchor="middle">absent</text>
<text x="144" y="652" class="sub" text-anchor="middle">槽位未注册</text></g>

<g data-box="1" id="sub-den"><rect class="amber" x="334" y="592" width="192" height="80" rx="6"/>
<text x="430" y="626" class="name" text-anchor="middle">denying</text>
<text x="430" y="652" class="warn" text-anchor="middle">deny-first 阻断出向</text></g>

<g data-box="1" id="sub-act"><rect class="active" x="710" y="592" width="310" height="80" rx="6"/>
<text x="865" y="626" class="name" text-anchor="middle">active</text>
<text x="865" y="652" class="sub" text-anchor="middle">不可变 dispatch 快照 · 禁 HTTP/2</text></g>

<line x1="40" y1="770" x2="1060" y2="770" stroke="#d9d2c3"/>
<text x="40" y="802" class="sub">注：credential-bound 拦截模式（OSEP-0023）当前仍为实验开关（默认 all），不可直接用于生产。</text>
<text x="40" y="832" class="sub">源码入口：components/egress/pkg/policy/、fastsandbox_actions.go、oseps/0023、oseps/0025</text>
</svg>
'''

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "lifecycle.svg": LIFECYCLE_SVG,
        "execd-init.svg": EXECD_INIT_SVG,
        "snapshot-modes.svg": SNAPSHOT_MODES_SVG,
        "egress-vault.svg": EGRESS_VAULT_SVG,
    }
    for name, content in files.items():
        ET.fromstring(content)  # ensure valid XML
        (OUT / name).write_text(content, encoding="utf-8")
        print(f"Wrote {OUT / name}")

if __name__ == "__main__":
    main()
