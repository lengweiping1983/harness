"""项目（Workspace）注册接口。

围绕 WorkspaceRegistry 提供项目注册、读取、更新与删除。
会话使用项目目录，侧栏按项目分组，文件浏览跟随当前项目。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import Any

from ..errors import BadRequest, NotFound, ServiceUnavailable
from ..responses import json_response
from ..router import Router


def build_router(ctx: Any) -> Router:
    router = Router()

    @router.get('/api/v1/workspace-options')
    def workspace_options(request: Any) -> Any:
        from pathlib import Path
        from harness.core.errors import HarnessError
        path = request.get('path', '')
        if len(request.query.get('path', [])) != 1 or not isinstance(path, str) or not path or '\0' in path or not Path(path).is_absolute():
            raise BadRequest(tr('ui.newConversation.chooseProject'), code='workspace/invalid')
        try:
            service = ctx.harness.worktrees
            if service is None:
                directory = Path(path).resolve(strict=True)
                if not directory.is_dir():
                    raise ValueError(path)
                return json_response({'cwd': str(directory), 'is_git': None, 'root': None, 'branch': None,
                                      'head': None, 'refs': [], 'local_branches': [], 'dirty': None})
            return json_response(service.conversation_options(path))
        except (OSError, ValueError, RuntimeError) as exc:
            raise BadRequest(tr('python.shell_tools.9065985f4c79', p0=path), code='workspace/invalid') from exc
        except HarnessError as exc:
            raise BadRequest(exc.message, code=exc.code, details=exc.details) from exc

    @router.get("/api/v1/workspaces")
    def list_workspaces(request: Any) -> Any:
        registry = ctx.workspaces()
        registry.ensure_seeded()
        return json_response(
            {"workspaces": registry.list(), "default": str(ctx.workspace_root())},
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.post("/api/v1/workspaces")
    def create_workspace(request: Any) -> Any:
        payload = request.json()
        path = str(payload.get("path") or "").strip()
        if not path:
            raise BadRequest(tr('python.workspaces.60d06a50fc25'), code="workspace/missing-path")
        registry = ctx.workspaces()
        try:
            record = registry.register(
                path, name=payload.get("name"), color=payload.get("color")
            )
        except FileNotFoundError as exc:
            raise BadRequest(str(exc), code="workspace/not-found-dir") from exc
        except (NotADirectoryError, ValueError) as exc:
            raise BadRequest(str(exc), code="workspace/invalid") from exc
        return json_response({"workspace": record}, status=201)

    @router.patch("/api/v1/workspaces/{workspace_id}")
    def update_workspace(request: Any, workspace_id: str) -> Any:
        payload = request.json()
        try:
            record = ctx.workspaces().update(
                workspace_id, name=payload.get("name"), color=payload.get("color")
            )
        except OSError as exc:
            raise ServiceUnavailable(tr('python.workspaces.787dd2c1c151'), code="workspace/persist-failed") from exc
        if record is None:
            raise NotFound(tr('python.workspaces.4b66367c9070', p0=f'{workspace_id}'), code="workspace/unknown")
        return json_response({"workspace": record})

    @router.delete("/api/v1/workspaces/{workspace_id}")
    def delete_workspace(request: Any, workspace_id: str) -> Any:
        if not ctx.workspaces().unregister(workspace_id):
            raise NotFound(tr('python.workspaces.4b66367c9070', p0=f'{workspace_id}'), code="workspace/unknown")
        return json_response({"deleted": workspace_id})

    @router.post("/api/v1/workspaces/mkdir")
    def mkdir(request: Any) -> Any:
        """在目录选择器里新建一个文件夹。

        用户浏览到任意目录都可手动新建，等价于在系统文件管理器里新建文件夹
        （服务只监听本机）。仍保留两条边界：名字只能是**单层目录**（不许带
        分隔符），父目录必须已经存在（``parents=False``），不代为创建整条路径。
        """
        from pathlib import Path

        payload = request.json()
        parent = str(payload.get("parent") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not name:
            raise BadRequest(tr('python.workspaces.e356da1e97d6'), code="workspace/missing-name")
        # 目录名不能带分隔符，否则一次调用能创建出一整条路径。
        if ("/" in name or "\\" in name or name in {".", ".."}
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise BadRequest(tr('python.workspaces.c8233f47cf0d'), code="workspace/bad-name")
        target = Path(parent).expanduser() if parent else ctx.workspace_root()
        # 只接受绝对路径，避免相对路径落到进程工作目录这种意外写入。
        if not target.is_absolute():
            raise BadRequest(tr('python.workspaces.c8233f47cf0d'), code="workspace/bad-name")
        created = target / name
        try:
            created.mkdir(parents=False, exist_ok=False)
        except FileExistsError as exc:
            raise BadRequest(tr('python.workspaces.d74b8d0bb10f'), code="workspace/exists") from exc
        except ValueError as exc:
            raise BadRequest(tr('python.workspaces.c8233f47cf0d'), code="workspace/bad-name") from exc
        except OSError as exc:
            raise BadRequest(tr('python.workspaces.c6c409894789', p0=f'{exc}'), code="workspace/mkdir-failed") from exc
        return json_response({"created": str(created), "name": name}, status=201)

    @router.post("/api/v1/workspaces/{workspace_id}/activate")
    def activate_workspace(request: Any, workspace_id: str) -> Any:
        """选中项目：记一次最近使用，供"最近项目"排序与后续默认 cwd 用。"""
        registry = ctx.workspaces()
        record = registry.get(workspace_id)
        if record is None:
            raise NotFound(tr('python.workspaces.4b66367c9070', p0=f'{workspace_id}'), code="workspace/unknown")
        registry.touch(workspace_id)
        return json_response({"workspace": record})

    return router


__all__ = ["build_router"]