# 堆叠沙箱：Jujutsu 工作区、Git Worktree 与 OS 写隔离

让两个并行的编码 Agent 在同一个项目目录里干活，一个负责补单元测试，另一个负责重写 README 并顺手跑 `cargo fmt`，不出十秒就会撞出经典灾难：文件互相覆盖，未暂存的 `git diff` 混成一锅粥，开发者的工作树被改得面目全非，甚至模型在子目录里敲了一条不小心的清理命令，顺手把宿主目录里的未提交代码删光了。

Infinity Code 把代码编辑与命令执行全部做成了一个独立的 RAP 工具服务，由 `sandbox-core`（3,902 行）、`sandbox-local`（4,911 行）和 `sandbox-remote`（534 行）三个 crate 组成。它的核心思路很干净：宿主的真实工作目录永远只读不动，每个 Agent 线程都分配一块隔离的独立工作区，父子线程之间以堆叠方式共享基线、干完再合并，同时在操作系统层用内核沙箱把写权限死死框在沙箱目录内。

![堆叠沙箱与操作系统级写隔离边界](../../wiki/assets/diagrams/sandbox-isolation.svg)

## SandboxBackend 与 ModeProvider：把 VCS 策略抽离出核心服务

打开 `crates/sandbox-core/src/sandbox.rs`，沙箱层没有把 Jujutsu 或 Git 逻辑硬编码进 HTTP 路由，而是拆出了两层 trait：`SandboxBackend` 负责底层环境差异（本地目录加 OS 写隔离 vs 云端 AWS EFS 镜像），`ModeProvider` 负责具体版本控制系统的工作区生命周期（`detect`、`create_sandbox`、`refresh_sandbox`、`describe`、`detect_external_change`、`push_sandbox`、`squash`、`diff_files`、`cleanup` 以及供 `Drop` 使用的同步 `cleanup_blocking`）。

在 `crates/sandbox-local/src/backend.rs` 里，`LocalBackend::new` 默认注册了两个对称的内置提供器：`LocalJjProvider` 在前，`LocalGitProvider` 垫底（调用方也可以通过 `with_provider` 往前插自定义提供器）。每次收到 `clone_repo` 调用时，后端先从请求路径向上回溯找到真实仓库根（哪怕 `cwd` 落在嵌套子目录也能准确识别），再按注册顺序询问提供器：

- **Jujutsu 模式（`SandboxMode::Jj`，推荐）**，检测到 `.jj` 目录即接管。`LocalJjProvider` 在 `{repo}/.infinity/.sandboxes/` 下建临时目录，执行 `jj workspace add -r root()`，配置工作区级 `user.name`/`user.email`，检出或新建名为 `sandbox-{thread_id}` 的书签。每次复用缓存工作区前自动跑 `jj workspace update-stale`；每次写操作完成后调用 `jj bookmark set sandbox-{thread_id} -B` 推进书签，若是 colocated 仓库还会执行 `jj --ignore-working-copy git export` 让底层 Git 引用保持同步；每次操作前还会通过 `jj_detect_external_bookmark_move` 检查书签是不是被用户在外部挪过位置，如果有偏移就在工具结果里附带醒目警告。
- **Git 模式（`SandboxMode::Git`，兜底）**，纯 Git 仓库下自动启用。`LocalGitProvider` 通过 `git worktree add` 在 `{repo}/.infinity/.sandboxes/` 下拉出独立 worktree 并绑定同名分支 `sandbox-{thread_id}`，每次修改后用 `git_amend_all` 把改动收拢到分支顶端，销毁时用 `git worktree remove` 清理，如果顶端提交为空还会顺手删掉空分支。
- **直接模式（`SandboxMode::Direct`）**，通过 `open_sandbox_direct` 打开原始目录，跳过 `ModeProvider`。注意直接模式下 `sandbox_writable` 默认设为 `false`，命令和文件工具只读不能直接改写原目录，真正要写必须走下面的人工审批流程。

## 堆叠与合并：父子并行线程怎么无冲突汇流

当根线程启动后，它拿到的工作区挂在书签 `sandbox-{root}` 上。此时它通过 `spawn_thread` 派生出两个子线程分别改单测和文档，每个子线程初始化沙箱时传入 `base_thread_id = root`，于是 `CloneContext::base_bookmark` 指向 `sandbox-{root}`。

这样就形成了**堆叠沙箱（stacked sandboxes）**：子线程 A 拿到 `sandbox-{c1}`，子线程 B 拿到 `sandbox-{c2}`，两者的基线都站在父线程的最新改动之上，但各自在独立的目录里修改、编译和跑测试，彼此完全看不见对方的半成品。等子线程 A 报告完成，父线程调用 `squash_sandbox`（底层在 jj 模式调 `jj::squash_from`，在 git 模式调 `git::squash_from`）把 `sandbox-{c1}` 压缩并回 `sandbox-{root}`；子线程 B 完成后如法炮制。整个过程中开发者自己的工作副本丝毫未动，最后只需要审一次 `sandbox-{root}` 的统一 Diff，满意了执行一条 `jj squash --from sandbox-{root}` 或 `git merge sandbox-{root}` 即可。子线程关闭时，`RapNotifier` 发出的 `/close_thread` 通知会触发 `cleanup_sandbox_permanently`，自动清理已合并的子工作区与空书签。

## OS 级写隔离：macOS sandbox-exec 与 Linux Bubblewrap

光靠把子进程 `current_dir` 设在沙箱目录里防不住模型越界写文件，一条带相对路径 `../../../foo` 或绝对路径的 Shell 命令就能改穿宿主仓库。因此 `sandbox-local/src/backend.rs` 在 `spawn_command` 里（第 307 至 426 行）引入了操作系统原生的写权限隔离：

- **macOS (`sandbox-exec`)**，动态拼出 SBPL 策略：默认 `(allow default)` 放行读与常规系统调用，紧接着 `(deny file-write*)` 封死全盘写权限，然后只用 `(allow file-write* (subpath ...))` 白名单放行当前沙箱目录（Direct 模式除外）、该沙箱专属的持久化 `TMPDIR`、`sccache` 缓存目录、额外授权路径，以及字符设备 `/dev/null`。
- **Linux (`bwrap` / Bubblewrap)**，用命名空间挂载构造同样的写边界：`--ro-bind / /` 把整棵根文件系统只读挂载进来，再用 `--bind` 单独把沙箱目录、`TMPDIR`、`sccache` 缓存和额外授权路径绑定为可写，并挂载独立 `--dev /dev` 与 `--proc /proc`。

源码里还有三个很老道的工程细节值得记下来：

1. **防止 Git 越界向上爬目录**，因为沙箱建在 `{repo}/.infinity/.sandboxes/` 内部，如果不加干预，在沙箱里跑 `git status` 或 `git rev-parse` 可能会一路向上穿透识别到外层宿主仓库。`spawn_command` 统一给子进程注入环境变量 `GIT_CEILING_DIRECTORIES`，把天花板钉在沙箱父目录上（`sandbox-local/tests/jj_colocated.rs` 专门有测试守住这条底线）。
2. **`sccache` 外部保活**，如果让 `cargo build` 在沙箱里自己拉起 `sccache` 守护进程，它会被框在沙箱写权限里。`LocalBackend` 在每次起沙箱命令前，先在沙箱外调用 `ensure_sccache_server()` 跑一次 `sccache --start-server`，同时把 `SCCACHE_DIR` 加进可写白名单，既保住增量编译缓存又不破坏写隔离。
3. **Linux 构建前置条件（commit `e1a3e4b` / PR #188）**，翻开 `crates/sandbox-local/build.rs`，在 Linux 上编译 `sandbox-local` 时，构建脚本会先探测系统里有没有 `bwrap --version`；如果没有，就执行 `scripts/install-bwrap.sh` 试图从源码现场编译 Bubblewrap，而源码编译需要 `git`、C 编译器（`gcc` / `build-essential`）和 `libcap` 开发头文件（`libcap-dev` / `libcap-devel`）。最新快照 `e1a3e4b` 正是在文档里把 Debian/Ubuntu 与 Fedora/AL2023 的预装包命令补清楚了。

## 越权审批与流式命令执行

如果模型确实需要改写沙箱外的文件（比如执行 `git push` 或修改用户指定的原仓库路径），`execute_command` 的参数支持声明 `additional_permissions`，接受 `"write-orig"` 或 `"write:/path"`。

`sandbox-core/src/server.rs` 的 `handle_execute_command_streaming_inner`（第 845 至 930 行）会沿着 `RapInvocation.thread_ancestors` 拼出完整线程链，逐级检查元数据里是不是已经有过授权。如果没有，沙箱服务会挂起当前任务、通过 RAP 的 `send_user_choice` 往前端弹一个多选项确认框：既可以选择永久授权给根线程或当前子线程（授权会沿祖先链自动向下继承），也可以选 `"Yes once"` 单次放行，或者 `"No"` 拒绝。

命令真正跑起来后，流式执行还有两处关键防护：第一，`invoke_handler` 在 `tokio::spawn` 后台任务**之前**就同步把取消通道 `oneshot::Sender` 登记进 `InFlightMap`，彻底消灭任务刚启动就收到 `/cancel_tool_call` 却找不到取消句柄的时间窗口；第二，子进程创建时调用 `.process_group(0)` 自成进程组，收到取消信号后 `kill_process` 向负 PID（`-pgid`）发 `SIGTERM`，一次清理整棵子进程树（例如 `cargo test` 派生出的所有编译和测试子进程），同时通过 `ViewUpdate` 回调把最新文件改动实时推给前端 Diff 面板。

## 云端镜像：sandbox-remote 的 EFS 后端

当沙箱部署在无服务器 AWS Lambda 上时，没有常驻本地磁盘可以缓存工作区。`crates/sandbox-remote/src/backend.rs` 实现了另一套 `SandboxBackend`，叫 `EfsBackend`：

- 共享的 AWS EFS 挂载点（如 `/mnt/efs/sandbox-repos`）按归一化后的远端 Git URI 存放一份裸 Jujutsu 仓库镜像（`jj git clone --no-colocate`），多个操作同一仓库的线程共用这份底仓，每次初始化顺手 `jj git fetch` 同步上游。
- 每次工具调用在 Lambda 的瞬时 `/tmp` 目录里基于 EFS 底仓拉出临时沙箱工作区，改完代码后通过 `jj_push_working_copy` 把 `sandbox-{thread_id}` 书签推回 EFS 裸仓，调用结束立刻删除 `/tmp` 目录。
- 回调通道换成了 `SigV4CallbackClient`（`sandbox-remote/src/sigv4_client.rs`），给发往 `rap-receiver` Function URL 的请求自动签发 AWS SigV4 签名。

不管是本地 `LocalBackend` 还是云端 `EfsBackend`，上层 `sandbox-core` 的路由和核心运行时完全感知不到差异。下一章我们往上走一层，看看本地守护进程怎么把这些沙箱、模型供应器、远程 SSH 开发机和跨主机热迁移统统串起来。
