# 契约层：四份 OpenAPI 与它们没写的东西

接手一套多语言平台时，最容易踩的坑是把某一个 SDK 的当前行为当成协议承诺。昨天用 Python SDK 调通的字段，换到 Go SDK 或另一套后端就报错，根源往往出在分不清哪些语义写进了契约、哪些只是某个实现的现状。

OpenSandbox 把 `specs/` 下的四份 OpenAPI 文件定为唯一公开契约，并用 `docs/api/implementation-compatibility.md` 把契约覆盖不到或各后端做不到的地方单独摊开。读懂这一层，后面看控制面和数据面才不会把刻意留下的差异误判成 bug。

## 生命周期契约的骨架与演进细节

`specs/sandbox-lifecycle.yml`（`info.version: 0.1.0`，约 124 KB）统一挂载在 `/v1` 下，端点按四组资源组织：

| 资源组 | 核心路径 | 语义 |
|---|---|---|
| 沙箱 | `POST /sandboxes`、`GET /sandboxes`、`GET/DELETE /sandboxes/{id}` | 创建、分页列举、查询详情、提交删除 |
| 状态与元数据 | `POST .../pause`、`.../resume`、`.../renew-expiration`、`PATCH .../metadata` | 中间态切换、续期、修改非保留元数据 |
| 访问与网络 | `GET .../endpoints/{port}`、`/networkpolicy`、`/credential-vault` | 解析可达入口、管理出向策略与凭据保管 |
| 派生制品 | `/snapshots`、`/templates` | 快照创建/查询/删除、Fast Sandbox 模板管理 |

创建请求 `CreateSandboxRequest` 在 `server/opensandbox_server/api/schema.py`（2054 行）里展开，核心字段分成四块：

```text
来源       image / snapshotId / templateId        （三者互斥）
形状       platform / resourceLimits / resourceRequests / volumes
行为       entrypoint / env / lifecycle / networkPolicy / credentialProxy
元数据     metadata / extensions / secureAccess / timeout
```

最近两个版本里，生命周期契约有四处变化直接影响调用方：

1. **快照模式进入公开契约**。`CreateSnapshotRequest.mode` 支持 `full`（默认）与 `rootfsOnly`，快照对象顶层暴露只读的 `Snapshot.mode`。五大 SDK 全部透传该字段，缺省时不往线上发字段以兼容旧服务端，读到未携带 `mode` 的响应时按 `full` 解释。
2. **封闭请求对象拒绝未知字段**。从 `1.2.1` 起，拼错字段名或在封闭对象里塞入未定义键不再静默忽略，统一返回 `422`；同一个字段同时写两个别名也会被拒。
3. **显式澄清 `DELETE 204` 与后续 `404` 的含义**。`1.3.0-rc.2` 把这条纪律写进了 API 规格：收到 `204` 只代表控制面接受了删除请求，不代表底层容器或进程已经退出；之后轮询拿到 `404` 只能说明资源对象已被清理，绝不能直接推导为工作负载已确认终止（比如使用 `Noop` 回收策略的池 Pod 可能仍留着进程）。
4. **保留字符串扩展槽 `extensions`**。它以 `map<string,string>` 承载过渡期语义，比如访问续期的旧写法 `extensions["access.renew.extend.seconds"]` 在 1.x 周期内继续有效，新代码迁移到结构化的 `lifecycle.renewalPolicy`（OSEP-0009）。

另外，`resourceLimits` 采用 `RootModel[Dict[str,str]]`，直接透传 `"500m"`、`"512Mi"` 这类 Kubernetes 数量字符串，Docker 后端再自行换算。这省掉了跨后端数值单位的二次翻译，代价是契约层管不住大小写拼错（如 `"500M"` 与 `"500m"`），必须留给服务端校验器把关。

## 四字段状态与按语义分层的错误码

沙箱对象的 `status` 由四个字段共同描述：

```text
status.state             Pending / Running / Pausing / Paused / Resuming / Stopping / Terminated / Failed
status.reason            机器可读原因码（如 SANDBOX_COMPLETED、Expired）
status.message           面向人的补充说明
status.lastTransitionAt  最近一次状态迁移时间
```

光给一个 `Failed` 或 `Terminated` 无法指导重试。带上 `reason` 之后，客户端能区分是镜像拉取失败、池容量打满，还是主进程以退出码 0 正常收尾（`1.3.0-rc.2` 中 BatchSandbox 的 `Completed` 相位会映射为 `Terminated` + `SANDBOX_COMPLETED`）。

错误响应体统一为 `ErrorResponse{code, message}`。HTTP 状态码负责粗粒度路由：`400` 参数非法、`401` 认证失败、`404` 不存在、`409` 状态冲突或运行时不支持该模式（例如 Docker/K8s 收到 `mode="rootfsOnly"`）、`422` 校验失败（包括 `Diagnostics` 漏传必填 `scope` 或 `bufferMin > bufferMax`）、`429` 容量耗尽、`501` 能力未实现（例如非 BatchSandbox 的 `WorkloadProvider` 调用 `/v1/pools` 返回 `501 KUBERNETES::POOL_NOT_SUPPORTED`）、`503` 暂不可用。

兼容性文档特意提醒调用方，不同子系统对不支持的操作并没有统一成一个状态码，不要写死一套 `if status == 501` 去判断能力边界。

## 异步创建与 endpoint 打包契约

创建沙箱天生耗时不可控，契约因此定为异步返回，客户端随后轮询 `GET /v1/sandboxes/{id}` 或调用 SDK 的就绪等待逻辑。不用长连接，是因为中间网关极易在数十秒后掐断空闲连接；不用回调，是因为大量调用方本身躲在无法被服务端反连的内网里。

`GET /v1/sandboxes/{id}/endpoints/{port}` 是整份契约里最省事却最容易被低估的端点。它不返回裸 URL，而是把地址和必需请求头打包返回：

```json
{
  "endpoint": "http://10.0.0.12:44772/proxy/8080",
  "headers": { "X-EXECD-ACCESS-TOKEN": "..." }
}
```

不同拓扑需要的请求头完全不同：单机直连带 execd 令牌，Kubernetes 网关带 `OpenSandbox-Ingress-To` 与可选 `secureAccess` 凭据，Fast Sandbox 返回签名路由和 `OPEN-SANDBOX-ORIGIN: template` 响应头。把头和地址绑在一起下发，客户端只需原样透传就能跨拓扑复用同一份代码。

## 数据面契约：SSE 事件模型写死了三条红线

`specs/execd-api.yaml`（约 119 KB）定义沙箱内 API，默认端口 `44772`。路由列表之外，真正决定客户端会不会写错的是 SSE 事件模型：

```text
init / status / stdout / stderr / result / execution_complete / execution_count / error / ping
```

Go 侧的 `ServerStreamEvent` 字段刻意压得很薄，只有 `type`、`text`、`exit_code`、`execution_time`、`timestamp`、`results`、`error` 等少量核心键，方便五种语言的客户端精准解码。契约钉死了三条语义：

1. **原始行尾原样保留**。`stdout` 与 `stderr` 事件保留 `\n`、`\r`、`\r\n`，客户端按序拼接即可还原终端进度条或半行输出。
2. **退出码明确归位**。前台 `POST /command` 成功结束必须带 `execution_complete` 与 `exit_code: 0`；超时则发出 `ename: "CommandTimeout"`（`1.3.0-rc.2` 起不再混用 `CommandExecError`）。后台启动确认和部分代码执行事件不带 `exit_code`，缺字段不能推断成功。
3. **断流不等于成功**。在收到终止事件前流被截断，客户端必须当成结果不确定来处理。

## 契约没写进去的部分

`docs/api/implementation-compatibility.md` 老实列出了契约和实现之间的四条缝隙：

- **PTY 路由尚未写进 `execd-api.yaml`**。`execd` 已经实现了 `/pty` 的 HTTP 创建与 WebSocket 接入，但 OpenAPI 规格还没覆盖它，不能因为某实现兼容 OpenAPI 就假定它支持 PTY。
- **隔离会话的 `diff` 与 `commit` 在契约里却未实现**。两个端点当前返回 `503`，而 `/v1/isolated/capabilities` 会明确报 `diff_supported: false` 和 `commit_supported: false`。
- **能力随后端分化**。模板属于 Fast Sandbox，生命周期钩子属于 Kubernetes 容器后端，Fast Sandbox 拒绝卷挂载与 `credentialProxy`。
- **尚无全局能力协商端点**。目前只有局部的能力探测（如隔离会话与加固地板的 capabilities 端点），全局层面仍依赖文档和具体错误码。

另外，五种语言的 SDK 只是契约的消费者，不等于契约本身。比如当前 Python SDK 的 `create()` 会依次查 `44772` 和 `18080` 两个端口，而 `create_from_template()` 只查 `44772`。如果要自建兼容后端，只要不绑定官方高级 `Sandbox` 封装类，完全可以只实现窄集合的生命周期端点并配自己的执行客户端。

## 源码入口

| 路径 | 看什么 |
|---|---|
| `specs/sandbox-lifecycle.yml` | 生命周期资源组、快照 `mode`、删除语义与错误响应 |
| `specs/execd-api.yaml` | 数据面路由与 SSE 事件模型 |
| `specs/egress-api.yaml` | 出向策略与 Credential Vault 接口 |
| `specs/diagnostic-api.yml` | 必填 `scope` 的诊断描述符契约 |
| `server/opensandbox_server/api/schema.py` | 契约在 FastAPI 侧的 Pydantic 落地（2054 行） |
| `docs/api/implementation-compatibility.md` | 契约覆盖与后端能力差异清单 |
