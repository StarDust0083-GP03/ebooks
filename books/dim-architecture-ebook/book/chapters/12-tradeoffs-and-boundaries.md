# 架构权衡与工程边界

设计文档写得再严密，如果没有自动化门禁替人盯着，代码库跑上三个月就会开始长出捷径：某个插件偷调了另一个插件的私有函数，某个新加的 RPC 在代码里硬编码了字符串 `"main"`，或者改完服务接口却忘了更新给 TypeScript 用的 `.d.ts` 类型文件。另一边，任何架构选择都不是免费午餐，把状态机严格塞进时间总线和三层作用域里，必然带来可观测的工程代价。

这一章专门看两件事：Dim 靠哪套机器检查把架构规矩钉在 CI 里，以及它明知存在却主动接受了哪些代价和未解边界。

## `xtask-check` 与 `xtask`：把架构纪律变成编译级门禁

Dim 把架构校验拆成了两档（`AGENTS.md` 与 `xtask/check/src/lib.rs`）：一档是零业务依赖的纯静态检查 `cargo run --locked -p xtask-check`，不需要编译庞大的 `dim-app-server` 和 `rutis`，几秒就能跑完；另一档是 `cargo run --locked -p xtask -- check`，在静态检查之上额外拉起真实的 App Server 产品装配并核对服务目录。

`xtask/check/src/lib.rs` 的 `check_all` 串起了五道静态红线：

| 门禁模块 | 源码位置 | 卡死什么违规行为 |
| --- | --- | --- |
| Crate 与模块边界 | `boundaries.rs`、`modules.rs` | 读取 `cargo metadata --offline` 与 `syn` AST，禁止 `types → runtime/capabilities`、`runtime → capabilities/server`、`capabilities → server` 以及 `xtask-check → dim-*/rutis` 反向依赖，同时核对 `crates.json` / `crates.mmd` 新鲜度 |
| 插件文档一致性 | `plugin_docs.rs` | 校验 `docs/architecture/rust-agent/plugins/` 下每个部件页面的依赖表和自动生成块与代码一致，防止文档长期失真 |
| 跨语言类型声明 | `types.rs` | 重新生成 `npm/branch-services.d.ts` 并比对差异，只要改了 `#[rutis::service]` 类型而没更新 TypeScript 声明立刻失败 |
| 插件锁与清单 | `npm.rs` | 核验内置插件目录的 `package-lock.json` 与清单匹配，避免打包发版时依赖漂移 |
| 注释链接与裸 `"main"` 检查 | `comment_links.rs`、`literal_main.rs` | 校验源码注释中的文档路径有效；用 `syn` AST 和 JS/TS/Vue 词法解析器禁止生产代码里出现裸字面量 `"main"` |

这五道门禁里，最耐人寻味的是高达 2200 多行的 `xtask/check/src/literal_main.rs`。为什么要在 Rust、TypeScript 和 Vue 源码里大动干戈地封杀裸字符串 `"main"`？答案正来自第 5 章的默认分支改动（PR #1917）：当默认分支不再永远等于起点 `main` 之后，代码里到处散落的 `"main"` 会变成极隐蔽的 Bug 温床。有的地方真的指起点（必须写常量 `ROOT_BRANCH_ID`），有的地方指“不查默认分支时的兜底起点”（必须写 `DEFAULT_BRANCH_ID`），而且这两个常量全仓库只允许在 `crates/dim-agent-types/src/events.rs` 和 `web/src/rpc/protocol.ts` 里各定义一次，连 SQLite 的 `is_readonly("main")` 这种与分支无关的用法都要登记在 `UNRELATED` 白名单里写明理由。

此外，工作区根 `Cargo.toml` 还打开了 `[workspace.lints.clippy]` 下的 `allow_attributes_without_reason = "warn"`：全仓库每一次写 `#[allow(...)]` 压制告警都必须附上 `reason = "..."`，把“图省事关警告”的口子堵死在审查阶段。

## 真实装配图：拒绝手绘权威架构图

在 `AGENTS.md` 里有一条很硬的规定：禁止手写维护现状架构图，也禁止手改 `docs/architecture/rust-agent/generated/` 下的文件。运行 `cargo run --locked -p xtask -- graph` 会直接装配真实的 App Server 产品 Profile（含一个 Session 和它的 `main` 分支），从活体依赖图里吐出四类权威快照：

- `services.mmd` / `services.md` / `services.json`（真实服务装配关系）
- `instances.mmd` / `instances.json`（实例键结构）
- `crates.mmd` / `crates.json`（包依赖图）
- `catalog.md`（111 个服务的组归属、提供方、`data`/`internal(reason)` 分类、方法表，以及按是否注入 `dispatcher`/`timeline` 自动标记出的 20 个 Capability）

手绘的图表只允许用来画“目标设计”，现状架构永远以 `xtask` 从代码跑出来的图为准，改动装配却不更新图，`xtask check` 直接拦下 PR。

## 主动接受的工程代价与未解边界

一个成熟的架构不会假装自己没有短板。翻完 Dim 的源码、ADR 和设计文档，至少有七处明确写进了文档的已知代价或当前边界：

**1. 跨语言数据面迁移尚未 100% 完成。** 真实目录 111 个服务中，已有 75 个迁成 `data` 数据面，但仍有 36 个停在 `internal(reason)`，其中 16 个是每回合高频热路径（`agentLoop`、`prompt`、`contextPreparation`、`toolExecutor` 等）。在它们完成操作化之前，核心执行链上的替换部件仍必须用进程内 Rust 编写。

**2. 写入轮合批在混合负载下放大 WAL 写入量。** 如第 3 章所述，上游在 Apple M4 上实测确认：当 64 条流式分支与 64 条普通依赖写入链并发时，普通批次“不等 20 ms 立刻提交”的设计把普通链延迟中位数压到了 9 ms，但顺带每轮都重写了正在活跃的流式输出行，使 WAL 吞吐从 40 MB/s 涨到 80 MB/s。

**3. 崩溃窗口期可能丢失两类分支通知。** `docs/design/timeline-branches.md` §6.2 明确记录：如果守护进程刚好在工具线 `turn/end` 提交之后、`BranchLine` 写出 `<dim-branch-idle>` 之前崩溃，或者在 `branch/ended` 与 `<dim-branch-ended>` 通知之间崩溃，因为 `BranchLine` 只对加载后的实时事件做通知决定，这两类提示通知不会补发（重启自愈只会更新名册 `branch:roster`，父线仍可通过名册和 `read` 查到完整历史）。

**4. 分支共享同一工作区，不做按分支文件系统隔离。** 同一 Session 的多条分支共用一个磁盘工作目录。想并行修改同一组文件而不互相覆盖，不能用分支，必须靠受管 Git worktree（`dim-app-server` 支持工作区 worktree 启动与关联）、独立分叉或带独立 `cwd` 的协作子代理。同时，分支的“比较与采纳（compare & adopt）”语义仍留待后续设计。

**5. 热重载与结构性配置变动的传播代价。** 按 ADR 002 D3，业务配置变更绝不重载插件，但真正的结构性操作（装卸插件、替换被依赖的底层行）会沿 `rutis` 依赖图级联重载下游依赖，并直接让在途 turn 失败。Dim 明确把这列为已接受成本，不为这类低频操作增加复杂的无损热迁移层。

**6. 安全模型明确不是多租户沙箱。** 已启用的插件、Hook、MCP stdio 进程都是受信任本地代码（AGENTS 规则 11）；ADR 003 D4 接受用户启用项目条目后不校验后续配置指纹变化；ADR 010 让进程沙箱默认关闭且 Windows 尚无后端；ADR 018 拿掉 `PermissionPolicy` 的 `deny_unknown_fields` 换取旧记录兼容，代价是放弃了对策略 JSON 拼写错误的解析拦截。此外，默认 App Server 产品装配里 `CompactionStrategyHost` 与上下文压缩模型宿主仍依赖显式绑定配置。

**7. 极简学习门槛并不低。** 哪怕只写一个简单状态机插件，开发者也必须理解单调序号、`committed`/`published` 差异、`consume` 重建截点、`before` 不可逆与副作用不重放。为了不让这套设计停留在纸面概念里，下一章我们用不到四百行零依赖的 Python 代码，亲手把 Dim 最核心的五组不变量搭起来，并用 18 个单元测试逐条跑通验证。
