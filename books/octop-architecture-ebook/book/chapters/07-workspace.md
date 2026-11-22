# 工作区后端：内容访问、对象存储与容器挂载边界

把一位专家的存储从本地目录切到腾讯云 COS、AWS S3 或火山引擎 TOS 之后，如果某条业务代码还顺手写着 `Path(workspace_dir) / "SOUL.md"` 直接读磁盘，最坏的情况不会报错，而是静默分叉：页面显示规则已经改好了，模型跑起来读到的却还是本地残留的旧文件。

Octop 在 `docs/agent-backend-file-io.md` 和 `AGENTS.md` 里把这条红线立得很硬：凡是专家工作区里的内容文件（`SOUL.md`、`skills/`、聊天上传的 `inbound/` 附件、专家生成的产出文件），读写一律走 `HarnessAgent.workspace`（即 `octop-harness` 提供的 `BackendWorkspace`），宿主业务代码禁止直接对 `~/.octop/agents/<id>/` 调用 `Path.read_text` 或 `Path.write_text`，也禁止在业务层按后端类型分叉拼接路径。

## 内容文件与系统运行材料两条路

工作区目录里住着两类生命周期截然不同的文件：

- **内容文件**走 `BackendWorkspace`，调用方统一传工作区相对路径（如 `SOUL.md`、`skills/foo/SKILL.md`），目录列举默认用 `"."`，HTTP 路由收到前端传来的前导斜杠路径时先经 `workspace_api_path()` 归一化。用户上传聊天附件时，文件经 `api/common/attachments.py` 和 `api/routers/uploads.py` 落到 `{workspace}/inbound/`，后续聊天准备阶段只认受控的工作区路径引用，不拿浏览器临时预览 URL 当文件权威。
- **系统运行材料**放在 `{workspace}/.octop/` 下（老专家的认证文件兼容 `{workspace}/.octop-auth`），包括 harness 托管的本地状态、`sessions/*.jsonl` 回退文件以及托管专家的链接清单。这个 `system_files_path` 对用户不可配置，在工作区首次初始化时自动建出。

即使内容平面已经切到远程对象存储，本机上的 `workspace_dir` 有时仍作为系统运行锚点存在。看到磁盘上有同名文件夹，不能想当然以为里面的文件就是权威副本。

## 多后端适配与 v1.0.3b3~b6 的新边界

`src/octop/infra/backend/adapter.py` 和 `resolver.py` 把控制面里保存的存储后端配置行，翻译成 `octop-harness` 识别的 backend spec。截至 `v1.0.3b7`，Octop 支持的后端覆盖本地文件系统、`local_shell`、PostgreSQL 文件后端、S3、腾讯云 COS、**火山引擎 TOS**（`v1.0.3b3` 新增 `tos_backend.py`，可选依赖 `tos>=2.9,<3`）、Docker 沙箱、OpenSandbox（`v1.0.3b3` 补齐了文件操作桥接 `opensandbox_mkdir.py`）以及把多个后端按路径前缀拼起来的 `composite` 组合后端。

这里最容易混淆的一点，是把远程文件存储和远程代码执行混为一谈。把文件存进 COS 或 TOS 解决的是持久化位置和跨副本共享，并不能隔离 shell 命令执行；而 Docker 和 OpenSandbox 才涉及容器进程、资源配额（`memory`、`cpus`、`pids_limit`、`allow_network`）和执行边界。

另外从 `v1.0.3b6` 起，管理员可以在全局配置里**限制创建专家时允许选择的存储后端**，并且 `v1.0.3b3` 修复了创建专家时强制校验允许的存储根路径。在多副本 Kubernetes 集群里，这个开关非常实用：管理员可以直接禁掉容易随 Pod 销毁丢数据的本机临时盘，强制新建专家落在 PostgreSQL、S3/COS/TOS 或 OpenSandbox 上。

## 容器挂载的两次修正：为什么 b6 一刀切，b7 又放开了条件

`v1.0.3b6` 到 `v1.0.3b7` 在 Docker 专家挂载宿主机目录这件事上经历了一次极具代表性的安全取舍，值得直接翻 `src/octop/infra/backend/docker_spec.py` 细看。

问题的起点很直白：如果 Octop 直接装在宿主机上（比如开发者的 Linux/macOS 机器或一台共享虚机），而 Docker 专家的配置允许传任意 `volumes` 给宿主机 Docker daemon，那普通用户只要在配置里把 `/` 或 `/etc` 挂进容器，就能借沙箱读穿整台宿主机，沙箱隔离形同虚设。因此 `v1.0.3b6` 直接把 Docker 专家的宿主机目录挂载全拦掉了。

但一刀切马上伤及了另一类正当部署：用户把 Octop 自己就跑在 Docker 容器里（通过 `-v /host/projects:/workspace` 提前由运维选定了允许暴露的宿主机目录），再把 Docker socket 挂进去跑 Docker 专家。在这种场景下，如果不把 `volumes` 透传给 Docker daemon，容器里的 Docker 专家压根碰不到运维已经明确授权的那几个项目目录。

于是 `v1.0.3b7` 在 `docker_spec.py` 的 `omit_docker_volumes()` 里改成了按部署环境区分：

```python
def omit_docker_volumes(spec: Any) -> Any:
    if running_in_container():
        return spec
    if not isinstance(spec, dict):
        return spec
    kind = str(spec.get("type") or "").strip().lower()
    if kind == "docker":
        if "volumes" not in spec:
            return spec
        logger.warning("ignoring docker backend volumes; host mounts are not allowed")
        out = dict(spec)
        out.pop("volumes", None)
        return out
    if kind == "composite":
        # 递归剔除 default 和 routes 下每个子后端里的 volumes
        ...
```

逻辑非常干净：`volumes` 键可以原样留在存储配置里，但在真正装配给 harness 或做健康探测（`enrich_docker_backend_spec`）时，只有当 `running_in_container()` 为真（即 Octop 自身运行在 Docker/Podman 容器中，宿主机可见目录已由运维在容器编排层把关）才保留挂载；只要是裸机宿主安装，无论顶层是 `docker` 还是 `composite` 里的子路由，一律剥掉 `volumes` 并打告警日志。

同时，`docker_spec.py` 还控制着后台存储浏览页面的可见性（`docker_spec_previewable()`）：非 Docker 后端默认可浏览，Docker 后端只有在 `sandbox_scope == "fixed"` 或显式设了 `previewable=True` 时，才允许管理员的 `/admin/backend` 树浏览其文件，而专家自己的工作区预览不受此限。

## 别把 root_dir 当成真正的操作系统沙箱

最后看一眼 `src/octop/infra/utils/host_dirs.py` 里一个极诚实的判断函数 `host_jail_enforced()`：

```python
def host_jail_enforced() -> bool:
    if os.name != "posix":
        return False
    return _bwrap_on_path()
```

这段注释说得非常透彻：只有在 Linux 且系统 `PATH` 里存在 `bwrap`（bubblewrap）时，非根目录的 `root_dir` 才具备真正的操作系统级隔离。在 Windows、macOS 或没装 `bwrap` 的 Linux 上，配置一个受限的 `root_dir` 只能约束 Agent 自身文件工具的路径参数（同时 POSIX 下拒绝 `/proc`、`/sys`、`/dev`、`/etc`、`/root` 等敏感前缀），但子进程依然跑在 Octop 服务用户的真实操作系统权限下，UI 绝不能把它包装成安全沙箱。分清路径参数检查和内核级沙箱的差别，才不会在安全评审时给团队留下虚假安全感。

## 源码入口

- [`docs/agent-backend-file-io.md`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/docs/agent-backend-file-io.md)
- [`src/octop/infra/backend/adapter.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/adapter.py)
- [`src/octop/infra/backend/resolver.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/resolver.py)
- [`src/octop/infra/backend/docker_spec.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/docker_spec.py)
- [`src/octop/infra/backend/tos_backend.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/backend/tos_backend.py)
- [`src/octop/infra/utils/host_dirs.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/infra/utils/host_dirs.py)
- [`src/octop/api/routers/chat/turn.py`](https://github.com/TencentCloud/Octop/blob/ecd2cdbce3a54dee0f424f021d0a144d9669685e/src/octop/api/routers/chat/turn.py)
