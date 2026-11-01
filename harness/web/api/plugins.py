"""Agent·技能·连接器（ESC）管理接口。

三体系统一在这里暴露生命周期管理（目录 / 启停 / 安装 / 卸载 / 连接器声明）。

设计纪律：

* **读快照直接读，写操作调度到事件循环**——连接器的 enable/disable
  会连接/断开 MCP 服务器（异步），必须走 :meth:`WebContext.call`；
  技能与Agent的启停是纯文件/内存操作，直接同步完成；
* **不做任何"隐式启用"**：安装只落盘 + 刷新注册表，是否启用由
  esc-state 决定（默认启用）；
* 更新一律 PATCH/POST（Router 不支持 PUT，settings.py 已有先例）。
"""

from __future__ import annotations

from harness.i18n import tr

import mimetypes
import hashlib
import os
import stat as file_stat
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from ...core.file_handles import check_handle_root
from ..responses import bytes_response, json_response, weak_etag
from ..router import Router
from ...core.errors import (
    ConnectorError,
    AgentNotFoundError,
    HarnessError,
    SessionNotFoundError,
    ValidationError,
)
from ...connectors.store import ConnectorRecord
from ...connectors.presets import PRESETS
from ...plugins.installer import install_from_directory, uninstall
from ...plugins.state import EscStateStore
from ...agents.authoring import create as create_agent, markdown as agent_markdown
from ...agents.parser import parse_agent_text
from ...agents.presentation import agent_presentation
from ...agents.editing import snapshot as agent_snapshot, update as update_agent, RevisionConflict

#: 安装源路径的最大长度（防误传超长字符串）
MAX_INSTALL_PATH = 1024


def _agent_capabilities(harness: Any) -> dict[str, bool]:
    """管理页与输入框使用同一能力声明，不试探不支持的写入接口。"""
    return {'persistent_team_selection': getattr(harness, 'agents', None) is not None
            and getattr(harness, 'agent_mount', None) is not None
            and bool(getattr(getattr(harness, 'config', None), 'enable_subagent', False))
            and callable(getattr(harness, 'set_agent_selection', None))}


def _skill_removal_scope(skill: Any, *, cwd: Any, home: Any) -> str | None:
    """仅对当前管理目录中的可见技能提供删除，不删除同名隐藏配置。"""
    if skill.path is None or skill.source == 'bundled':
        return None
    manifest = Path(skill.path).resolve()
    from ...skills.paths import installation_root
    targets = {scope: installation_root('skill', scope, cwd=cwd, home=home) for scope in ('project', 'user')}
    return next((scope for scope, root in targets.items()
                 if manifest == (root / skill.name / 'SKILL.md').resolve()), None)


def _skill_rows(harness: Any, registry: Any = None, cwd: Any = None, *, picker: bool = False) -> list[dict[str, Any]]:
    """技能目录（含 enabled；忽略过滤器，管理界面要看到停用项）。"""
    if harness.skills is None:
        return []
    state: EscStateStore = harness.esc_state
    rows = []
    for skill in (registry if registry is not None else harness.skills).list_all():
        row = {key: getattr(skill, key) for key in ('name', 'description', 'user_invocable')} if picker else skill.to_dict()
        if picker:
            row['interface'] = {key: skill.interface[key] for key in ('display_name', 'short_description', 'default_prompt') if key in skill.interface}
        row["plugin_enabled"] = skill.plugin_id is None or harness.packages.is_enabled(skill.plugin_id)
        row["enabled"] = state.is_enabled("skill", skill.name) and row["plugin_enabled"]
        if not picker:
            row['removal_scope'] = _skill_removal_scope(skill, cwd=cwd or harness.config.cwd, home=harness.config.home)
        rows.append(row)
    return rows


def _agent_edit_scope(agent: Any, *, cwd: Any, home: Any) -> str | None:
    """只编辑当前可见的管理文件，内置和继承文件通过复制定制。"""
    if agent.path is None or agent.source == 'bundled':
        return None
    targets = {'project': (Path(cwd) if cwd else Path.cwd()) / '.harness' / 'agents',
               'user': (Path(home).expanduser() if home else Path.home() / '.harness') / 'agents'}
    return next((scope for scope, root in targets.items()
                 if Path(agent.path).resolve() == (root / agent.name / 'AGENTS.md').resolve()), None)


def _agent_avatar_path(agent: Any) -> Path | None:
    """解析Agent头像文件；只接受Agent目录内部、真实存在的文件。"""
    if agent.path is None:
        return None
    base = Path(agent.path).resolve().parent
    relative = str(agent.metadata.get("avatar") or "avatars/agent.png")
    try:
        target = (base / relative).resolve()
        if not target.is_file() or not target.is_relative_to(base):
            return None
    except OSError:
        return None
    return target


def _default_agent_avatar(agent: Any) -> bytes:
    """为没有自定义头像的Agent生成稳定、安全且无需额外资源的字标。"""
    label = ''.join(char for char in agent_presentation(agent)['display_name'][:120]
                    if ord(char) >= 32 and not 0xD800 <= ord(char) <= 0xDFFF and ord(char) not in (0xFFFE, 0xFFFF)) or agent.name
    initial = escape((label.strip() or agent.name)[0].upper())
    colors = (('#e4f1f0', '#0f766e'), ('#e9eef8', '#345994'),
              ('#f6edde', '#94662b'), ('#edeaf5', '#67518a'))
    background, foreground = colors[hashlib.sha256(agent.name.encode()).digest()[0] % len(colors)]
    if agent.agent_type == 'team':
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
                f'<title>{escape(label)}</title><rect width="64" height="64" rx="18" fill="{background}"/>'
                f'<g fill="none" stroke="{foreground}" stroke-width="3" stroke-linecap="round">'
                '<circle cx="27" cy="24" r="7"/><path d="M14 47v-4a13 13 0 0 1 26 0v4'
                'M41 18a7 7 0 0 1 0 14M47 47v-4a13 13 0 0 0-5-10"/></g></svg>').encode()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<title>{escape(label)}</title><rect width="64" height="64" rx="18" fill="{background}"/>'
            f'<text x="32" y="42" text-anchor="middle" font-family="system-ui,sans-serif" '
            f'font-size="30" font-weight="600" fill="{foreground}">{initial}</text></svg>').encode()


def _image_media_type(raw: bytes, fallback: str) -> str:
    """按魔数识别图片真实类型。

    头像文件在插件里可能被错误命名（例如 JPEG 内容存成 ``agent.png``），
    而响应带 ``X-Content-Type-Options: nosniff``，因此必须按内容而不是
    扩展名给出 Content-Type。
    """
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:2] == b"BM":
        return "image/bmp"
    if raw[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if raw.lstrip()[:4].lower() == b"<svg":
        return "image/svg+xml"
    return fallback


#: 自定义头像最多读取5 MiB，读取期间增长同样拒绝。
MAX_AVATAR_BYTES = 5 * 1024 * 1024


def _read_avatar(target: Path, root: Path) -> bytes:
    """从所属目录句柄读取普通文件，核对读取前后身份及大小。"""
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    if os.open in os.supports_dir_fd:
        parts = target.relative_to(root).parts
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(root, directory_flags)
        try:
            for name in parts[:-1]:
                child = os.open(name, directory_flags, dir_fd=parent)
                os.close(parent)
                parent = child
            descriptor = os.open(parts[-1], flags, dir_fd=parent)
        finally:
            os.close(parent)
    else:
        descriptor = os.open(target, flags)
    try:
        incoming = os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise
    with incoming:
        check_handle_root(incoming.fileno(), root)
        before = os.fstat(incoming.fileno())
        if not file_stat.S_ISREG(before.st_mode) or before.st_size > MAX_AVATAR_BYTES:
            raise OSError('avatar file exceeds limit or is not regular')
        raw = incoming.read(MAX_AVATAR_BYTES + 1)
        after = os.fstat(incoming.fileno())
        def identity(info):
            return info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size
        if len(raw) > MAX_AVATAR_BYTES or len(raw) != before.st_size or identity(before) != identity(after):
            raise OSError('avatar file changed during read')
        return raw


def _avatar_preview(raw: bytes, filename: str) -> tuple[bytes, str]:
    """仅转换已确认的有限正文，动画与非位图按原格式返回。"""
    target = Path(filename)
    media_type = _image_media_type(raw, mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
    if media_type not in ('image/png', 'image/jpeg', 'image/webp', 'image/bmp', 'image/tiff'):
        return raw, media_type
    try:
        with Image.open(BytesIO(raw)) as image:
            if getattr(image, 'is_animated', False) or max(image.size) <= 128:
                return raw, media_type
            image = ImageOps.exif_transpose(image)
            image.thumbnail((128, 128))
            output = BytesIO()
            image.convert('RGBA').save(output, format='PNG')
            preview = output.getvalue()
            return (preview, 'image/png') if len(preview) < len(raw) else (raw, media_type)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return raw, media_type


def _agent_rows(harness: Any, registry: Any = None, cwd: Any = None, *, picker: bool = False) -> list[dict[str, Any]]:
    """Agent目录（含 enabled 与头像地址）。"""
    if harness.agents is None:
        return []
    state: EscStateStore = harness.esc_state
    rows = []
    for agent in (registry if registry is not None else harness.agents).list_all():
        row = {key: getattr(agent, key) for key in ('name', 'description', 'agent_type')} if picker else agent.to_dict()
        row.update(agent_presentation(agent))
        row["enabled"] = state.is_enabled("agent", agent.name) and (agent.provider != 'plugins' or harness.packages.is_enabled(agent.metadata['plugin_id']))
        if not picker:
            row['edit_scope'] = _agent_edit_scope(agent, cwd=cwd or harness.config.cwd, home=harness.config.home)
            row['avatar'] = (
                f"/agents/{quote(agent.name)}/avatar" if _agent_avatar_path(agent) is not None or not agent.metadata.get('avatar') else None
            )
        rows.append(row)
    return rows


def _connector_rows(harness: Any) -> list[dict[str, Any]]:
    """连接器目录（含实时连接状态）。"""
    if harness.connectors is None:
        return []
    return harness.connectors.catalog()


def _scope_of(request: Any) -> str:
    scope = (request.get("scope") or "user").strip().lower()
    if scope not in ("user", "project"):
        raise BadRequest(tr('python.plugins.7e17a180a7cf'), code="plugin/bad-scope")
    return scope


def _install_payload(request: Any) -> tuple[str, str]:
    """解析安装请求体，返回 (path, scope)。"""
    body = request.json()
    if not isinstance(body, dict):
        raise BadRequest(tr('python.plugins.37840a0a4194'), code="plugin/bad-body")
    raw_path = body.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path.strip()) > MAX_INSTALL_PATH:
        raise BadRequest(tr('python.plugins.04625d617c0f'), code="plugin/bad-path")
    raw_path = raw_path.strip()
    scope = body.get("scope", "user")
    if not isinstance(scope, str) or scope.strip().lower() not in ("user", "project"):
        raise BadRequest(tr('python.plugins.7e17a180a7cf'), code="plugin/bad-scope")
    return raw_path, scope.strip().lower()


def build_router(ctx: Any) -> Router:
    """注册 ESC 管理路由。``ctx`` 是 :class:`~harness.web.context.WebContext`。"""
    router = Router()
    harness = ctx.harness

    def project_scope(request: Any, body: Any = None) -> tuple[Any, str | None]:
        """项目操作绑定已有聊天，不接收客户端任意 cwd。"""
        raw = request.get("session_id")
        if raw is None:
            raw = body.get("session_id") if isinstance(body, dict) else None
        if raw is None or raw == "":
            return None, str(harness.config.cwd) if harness.config.cwd else None
        if not isinstance(raw, str):
            raise BadRequest(tr('python.plugins.37840a0a4194'), code="plugin/bad-body")
        try:
            session = harness.get_session(raw.strip())
        except SessionNotFoundError as exc:
            raise NotFound(str(exc), code="plugin/session-not-found") from exc
        return session, str(harness.agent_project_root(session))

    def agent_scope(request: Any, body: Any = None) -> tuple[Any, str | None]:
        """获取当前聊天的Agent视图和写入根目录。"""
        session, root = project_scope(request, body)
        return harness.agents_for(session) if session is not None else harness.agents, root

    def skill_scope(request: Any, body: Any = None) -> tuple[Any, str | None]:
        """技能读取跟随聊天，安装写入当前 checkout，不写共享记忆根目录。"""
        session, root = project_scope(request, body)
        if session is not None:
            from ...skills.scope import checkout_root
            root = str(checkout_root(session.header.cwd or harness.config.cwd or Path.cwd()))
        return harness.skills_for(session, configured=False) if session is not None else harness.skills, root

    # -- 总览 ------------------------------------------------------------
    @router.get("/api/v1/plugins")
    def overview(request: Any) -> Any:
        """三体系总览（管理界面首屏一次拉取）。"""
        skills, skill_root = skill_scope(request)
        agents, agent_root = agent_scope(request)
        return json_response(
            {
                "skills": _skill_rows(harness, skills, skill_root),
                "agents": _agent_rows(harness, agents, agent_root),
                "connectors": _connector_rows(harness),
                "capabilities": _agent_capabilities(harness),
                "enabled": {
                    "skills": harness.skills is not None,
                    "agents": harness.agents is not None,
                    "connectors": harness.connectors is not None,
                },
            },
            accept_encoding=request.header("Accept-Encoding"),
        )

    # -- 技能 ------------------------------------------------------------
    @router.post("/api/v1/plugins/skills/create")
    def create_skill(request: Any) -> Any:
        """在当前作用域创建一次，直接进入唯一技能目录。"""
        from ...skills.authoring import create_skill as create, skill_name
        from ...skills.paths import installation_root
        body = request.json()
        if not isinstance(body, dict) or set(body) - {'name', 'description', 'instructions', 'scope', 'session_id'}:
            raise BadRequest(tr('python.web.validation.skillFields'), code='skill/invalid')
        scope = body.get('scope', 'user')
        if scope not in ('user', 'project'):
            raise BadRequest(tr('python.web.validation.skillScope'), code='skill/invalid')
        _, cwd = skill_scope(request, body)
        if harness.skills is None:
            raise ServiceUnavailable(tr('python.web.validation.skillsDisabled'), code='skill/disabled')
        try:
            skill_name(body.get('name'))
            result = create(installation_root('skill', scope, cwd=cwd, home=harness.config.home),
                            body['name'], body.get('description'), body.get('instructions'))
        except (ValueError, TypeError, HarnessError) as exc:
            raise BadRequest(str(exc), code='skill/invalid') from exc
        except FileExistsError as exc:
            raise Conflict(tr('ui.skills.exists', name=body['name']), code='skill/conflict') from exc
        harness.refresh_skills()
        return json_response(result)

    @router.get("/api/v1/plugins/skills/created")
    def created_skill(request: Any) -> Any:
        """按同一作用域回查原文件，不重放创建或读取其他任意路径。"""
        from ...skills.authoring import snapshot, skill_name
        from ...skills.paths import installation_root
        scope = request.get('scope') or 'user'
        try:
            name = skill_name(request.get('name'))
            if scope not in ('user', 'project'):
                raise ValueError(tr('runtime.validation.fa0916ec579e'))
            _, cwd = skill_scope(request)
            result = snapshot(installation_root('skill', scope, cwd=cwd, home=harness.config.home) / name)
        except FileNotFoundError as exc:
            raise NotFound(str(exc), code='skill/not-found') from exc
        except (ValueError, TypeError, HarnessError) as exc:
            raise BadRequest(str(exc), code='skill/invalid') from exc
        harness.refresh_skills()
        return json_response(result)

    @router.get("/api/v1/plugins/skills")
    def skills_catalog(request: Any) -> Any:
        skills, skill_root = skill_scope(request)
        return json_response(
            {"skills": _skill_rows(harness, skills, skill_root, picker=request.get('view') == 'picker'), "enabled": harness.skills is not None},
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.post("/api/v1/plugins/skills/{name}/enabled")
    def set_skill_enabled(request: Any, name: str) -> Any:
        body = request.json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise BadRequest(tr('python.plugins.81b2c53e9d37'), code="plugin/bad-body")
        registry, _ = skill_scope(request, body)
        if registry is None or not any(skill.name == name for skill in registry.list_all()):
            raise NotFound(tr('python.plugins.bd93cfc02434', p0=f'{name}'), code="plugin/not-found")
        harness.set_skill_enabled(name, bool(body["enabled"]))
        return json_response({"name": name, "enabled": bool(body["enabled"])})

    @router.post("/api/v1/plugins/skills/install")
    def install_skill(request: Any) -> Any:
        raw_path, scope = _install_payload(request)
        registry, cwd = skill_scope(request, request.json())
        try:
            target = install_from_directory(
                raw_path,
                kind="skill",
                scope=scope,  # type: ignore[arg-type]
                cwd=cwd,
                home=harness.config.home,
            )
        except ValidationError as exc:
            raise BadRequest(str(exc), code="plugin/invalid")
        except FileExistsError as exc:
            raise Conflict(str(exc), code="plugin/conflict")
        try:
            harness.refresh_skills()
            registry, _ = skill_scope(request, request.json())
        except Exception as exc:
            raise ServiceUnavailable(tr('plugin.install.refreshFailed'), code='plugin/install-refresh-failed',
                                     details={'installed': True, 'path': str(target)}) from exc
        skill = next((item for item in registry.list_all() if item.name == Path(target).name), None) if registry is not None else None
        return json_response(
            {"installed": True, "path": str(target), "skill": skill.to_dict() if skill else None}
        )

    @router.delete("/api/v1/plugins/skills/{name}")
    def uninstall_skill(request: Any, name: str) -> Any:
        scope = _scope_of(request)
        registry, cwd = skill_scope(request)
        visible = next((item for item in registry.list_all() if item.name == name), None) if registry is not None else None
        if visible is None:
            raise NotFound(tr('python.plugins.bd93cfc02434', p0=name), code='plugin/not-found')
        if _skill_removal_scope(visible, cwd=cwd, home=harness.config.home) != scope:
            raise Conflict(tr('python.plugins.skill_removal_unmanaged'), code='plugin/forbidden')
        try:
            uninstall(
                name,
                kind="skill",
                scope=scope,  # type: ignore[arg-type]
                cwd=cwd,
                home=harness.config.home,
            )
        except ValidationError as exc:
            raise BadRequest(str(exc), code="plugin/invalid")
        except FileNotFoundError as exc:
            raise NotFound(str(exc), code="plugin/not-found")
        except PermissionError as exc:
            raise Conflict(str(exc), code="plugin/forbidden")
        harness.esc_state.forget("skill", name)
        harness.refresh_skills()
        return json_response({"removed": True, "name": name})

    # -- Agent ------------------------------------------------------------
    @router.get("/api/v1/plugins/agents")
    def agents_catalog(request: Any) -> Any:
        agents, agent_root = agent_scope(request)
        return json_response(
            {"agents": _agent_rows(harness, agents, agent_root, picker=request.get('view') == 'picker'),
             "capabilities": _agent_capabilities(harness)},
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/plugins/agents/{name}/avatar")
    def agent_avatar(request: Any, name: str) -> Any:
        """回读Agent头像；只允许Agent目录内部的文件，拒绝目录穿越。"""
        registry, _ = agent_scope(request)
        agent = (
            next((item for item in registry.list_all() if item.name == name), None)
            if registry is not None else None
        )
        if agent is None:
            raise NotFound(tr('python.plugins.7799e3362903', p0=name), code='plugin/not-found')
        target = _agent_avatar_path(agent)
        if target is None:
            if agent.metadata.get('avatar'):
                raise NotFound(tr('agent.avatar.missing'), code='agent/avatar-missing')
            raw = _default_agent_avatar(agent)
            etag = weak_etag(name, raw)
            headers = {'ETag': etag, 'Cache-Control': 'private, max-age=300', 'X-Content-Type-Options': 'nosniff'}
            if request.header('If-None-Match') == etag:
                return bytes_response(b'', status=304, headers=headers)
            return bytes_response(raw, content_type='image/svg+xml', headers=headers)
        try:
            raw = _read_avatar(target, Path(agent.path).resolve().parent)
            raw, media_type = _avatar_preview(raw, target.name)
            etag = weak_etag(name, 'preview-128-v2', raw)
            headers = {"ETag": etag, "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
            if request.header("If-None-Match") == etag:
                return bytes_response(b"", status=304, headers=headers)
        except OSError as exc:
            raise ServiceUnavailable(tr('agent.avatar.unavailable'), code='agent/avatar-unavailable') from exc
        return bytes_response(
            raw,
            content_type=media_type,
            headers={
                "ETag": etag,
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/api/v1/plugins/agents/{name}/enabled")
    def set_agent_enabled(request: Any, name: str) -> Any:
        body = request.json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise BadRequest(tr('python.plugins.81b2c53e9d37'), code="plugin/bad-body")
        registry, _ = agent_scope(request, body)
        if registry is None or not registry.has(name):
            raise NotFound(tr('python.plugins.7799e3362903', p0=f'{name}'), code="plugin/not-found")
        harness.set_agent_enabled(name, bool(body["enabled"]))
        return json_response({"name": name, "enabled": bool(body["enabled"])})

    @router.post("/api/v1/plugins/agents/create")
    def author_agent(request: Any) -> Any:
        """在共享目录或聊天绑定项目创建新Agent，不覆盖同名文件。"""
        body = request.json()
        registry, cwd = agent_scope(request, body)
        if registry is None:
            raise BadRequest(tr('python.harness.efc9526645eb'), code="agent/disabled")
        if not isinstance(body, dict) or body.get("scope", "user") not in ("user", "project"):
            raise BadRequest(tr('python.plugins.37840a0a4194'), code="plugin/bad-body")
        root = (Path(cwd or Path.cwd()) / ".harness" / "agents") if body.get("scope") == "project" else (Path(harness.config.home or Path.home() / ".harness") / "agents")
        try:
            agent = create_agent(root, body, registry)
        except FileExistsError as exc:
            raise Conflict(tr("agent.authoring.exists", name=str(body.get("name", ""))), code="agent/exists") from exc
        except HarnessError as exc:
            raise BadRequest(str(exc), code="agent/invalid") from exc
        except OSError as exc:
            raise ServiceUnavailable(tr("agent.authoring.storage"), code="agent/storage") from exc
        harness.refresh_agents()
        updated, _ = agent_scope(request, body)
        row = next((item for item in updated.list_all() if item.name == agent.name), agent)
        return json_response({"created": True, "agent": row.to_dict()})

    def editable_agent(request: Any, name: str, body: Any = None):
        """只从当前聊天目录选取可编辑文件，不接受客户端路径。"""
        registry, cwd = agent_scope(request, body)
        agent = next((item for item in registry.list_all() if item.name == name), None) if registry is not None else None
        if agent is None:
            raise NotFound(tr('python.plugins.7799e3362903', p0=name), code='plugin/not-found')
        scope = _agent_edit_scope(agent, cwd=cwd, home=harness.config.home)
        if scope is None:
            raise Conflict(tr('agent.edit.unmanaged'), code='agent/not-editable')
        return registry, agent, scope

    @router.get('/api/v1/plugins/agents/{name}/edit')
    def read_agent_edit(request: Any, name: str) -> Any:
        _, agent, scope = editable_agent(request, name)
        try:
            result = agent_snapshot(agent.path, source=agent.source)
        except OSError as exc:
            raise ServiceUnavailable(tr('agent.authoring.storage'), code='agent/storage') from exc
        result['agent']['edit_scope'] = scope
        return json_response(result)

    @router.patch('/api/v1/plugins/agents/{name}')
    def edit_agent(request: Any, name: str) -> Any:
        body = request.json()
        if not isinstance(body, dict) or body.get('name') != name:
            raise BadRequest(tr('agent.authoring.invalid', field='name'), code='agent/invalid')
        registry, agent, scope = editable_agent(request, name, body)
        payload = {key: value for key, value in body.items() if key != 'expected_revision'}
        try:
            result = update_agent(agent.path, payload, registry, expected_revision=body.get('expected_revision'), source=agent.source)
        except RevisionConflict as exc:
            raise Conflict(tr('agent.edit.conflict'), code='agent/version-conflict') from exc
        except HarnessError as exc:
            raise BadRequest(str(exc), code='agent/invalid') from exc
        except OSError as exc:
            raise ServiceUnavailable(tr('agent.authoring.storage'), code='agent/storage') from exc
        from ...app.agent_refresh import refresh_agent_sessions
        result.update(ctx.call(refresh_agent_sessions(harness, agent.path)))
        if body.get('session_id'):
            result['mount'] = harness.agent_mount_state(body['session_id'])
        result['updated'] = True
        result['agent']['edit_scope'] = scope
        return json_response(result)

    @router.post("/api/v1/plugins/agents/preview")
    def preview_agent(request: Any) -> Any:
        """只解析上传文本，预览阶段不会写入配置。"""
        body = request.json()
        text = body.get("markdown") if isinstance(body, dict) else None
        if not isinstance(text, str) or len(text) > 256000:
            raise BadRequest(tr('python.plugins.37840a0a4194'), code="plugin/bad-body")
        try:
            agent = parse_agent_text(text)
        except HarnessError as exc:
            raise BadRequest(str(exc), code="agent/invalid") from exc
        return json_response({"agent": agent.to_dict()})

    @router.get("/api/v1/plugins/agents/{name}/export")
    def export_agent(request: Any, name: str) -> Any:
        """导出当前项目可见的配置，停用项也可供用户整理与分享。"""
        registry, _ = agent_scope(request)
        agent = next((item for item in registry.list_all() if item.name == name), None) if registry is not None else None
        if agent is None:
            raise NotFound(tr('python.plugins.7799e3362903', p0=name), code="plugin/not-found")
        return json_response({"name": agent.name, "format": "harness-agent-md-v1", "markdown": agent_markdown(agent)})

    @router.post("/api/v1/plugins/agents/install")
    def install_agent(request: Any) -> Any:
        raw_path, scope = _install_payload(request)
        registry, cwd = agent_scope(request, request.json())
        try:
            target = install_from_directory(
                raw_path,
                kind="agent",
                scope=scope,  # type: ignore[arg-type]
                cwd=cwd,
                home=harness.config.home,
            )
        except ValidationError as exc:
            raise BadRequest(str(exc), code="plugin/invalid")
        except FileExistsError as exc:
            raise Conflict(str(exc), code="plugin/conflict")
        try:
            harness.refresh_agents()
            updated, _ = agent_scope(request, request.json())
        except Exception as exc:
            raise ServiceUnavailable(tr('plugin.install.refreshFailed'), code='plugin/install-refresh-failed',
                                     details={'installed': True, 'path': str(target)}) from exc
        agent = next((item for item in updated.list_all() if item.name == Path(target).name), None) if updated is not None else None
        return json_response(
            {"installed": True, "path": str(target), "agent": agent.to_dict() if agent else None}
        )

    @router.delete("/api/v1/plugins/agents/{name}")
    def uninstall_agent(request: Any, name: str) -> Any:
        scope = _scope_of(request)
        registry, cwd = agent_scope(request)
        visible = next((item for item in registry.list_all() if item.name == name), None) if registry is not None else None
        if visible is None:
            raise NotFound(tr('python.plugins.7799e3362903', p0=name), code='plugin/not-found')
        if _agent_edit_scope(visible, cwd=cwd, home=harness.config.home) != scope:
            raise Conflict(tr('agent.remove.unmanaged'), code='plugin/forbidden')
        try:
            uninstall(
                name,
                kind="agent",
                scope=scope,  # type: ignore[arg-type]
                cwd=cwd,
                home=harness.config.home,
            )
        except ValidationError as exc:
            raise BadRequest(str(exc), code="plugin/invalid")
        except FileNotFoundError as exc:
            raise NotFound(str(exc), code="plugin/not-found")
        except PermissionError as exc:
            raise Conflict(str(exc), code="plugin/forbidden")
        harness.esc_state.forget("agent", name)
        harness.refresh_agents()
        return json_response({"removed": True, "name": name})

    # -- Agent挂载（会话级） -----------------------------------------------
    @router.get("/api/v1/plugins/agent-mount")
    def agent_mount_get(request: Any) -> Any:
        """读取某个会话当前挂载的Agent（``session_id`` 必填）。"""
        session_id = (request.get("session_id") or "").strip()
        if not session_id:
            raise BadRequest(tr('python.plugins.666aaba8bc94'), code="plugin/missing-session")
        try:
            state = harness.agent_mount_state(session_id)
        except SessionNotFoundError as exc:
            raise NotFound(str(exc), code="plugin/session-not-found") from exc
        return json_response({"mount": state})

    @router.post("/api/v1/plugins/agent-selection")
    @router.post("/api/v1/plugins/agent-mount")
    def agent_mount_post(request: Any) -> Any:
        """原挂载入口只接单Agent；选择入口还支持持续团队和工作区选项。"""
        body = request.json()
        if not isinstance(body, dict):
            raise BadRequest(tr('python.plugins.37840a0a4194'), code="plugin/bad-body")
        session_id = body.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise BadRequest(tr('python.plugins.37840a0a4194'), code="plugin/bad-body")
        session_id = session_id.strip() if isinstance(session_id, str) else ''
        if not session_id:
            raise BadRequest(tr('python.plugins.666aaba8bc94'), code="plugin/missing-session")
        name = body.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise BadRequest(tr('python.plugins.81af4a868780'), code="plugin/bad-name")
        unknown = set(body) - {'session_id', 'name', 'team_worktrees', 'team_working_copy'}
        if unknown:
            raise BadRequest(tr('agent.definition.fields_invalid', p0=', '.join(sorted(unknown))), code='agent/invalid-options')

        async def _mount() -> dict[str, Any]:
            try:
                if request.path.rstrip('/') == '/api/v1/plugins/agent-selection':
                    return await harness.set_agent_selection(session_id, name,
                        team_worktrees=body.get('team_worktrees', False), team_working_copy=body.get('team_working_copy', False))
                return await harness.set_agent_mount(session_id, name)
            except (AgentNotFoundError, SessionNotFoundError) as exc:
                raise NotFound(str(exc), code="plugin/not-found") from exc
            except HarnessError as exc:
                # 原挂载入口保持团队限制，显式选择入口还会校验工作区选项。
                raise BadRequest(exc.message, code=exc.code, details=exc.details or None) from exc

        state = ctx.call(_mount(), timeout=10.0)
        return json_response({"mount": state})

    # -- 连接器 ----------------------------------------------------------
    @router.get("/api/v1/plugins/connectors")
    def connectors_catalog(request: Any) -> Any:
        return json_response(
            {"connectors": _connector_rows(harness), "enabled": harness.connectors is not None,
             "environment": [{"server_name": config.server_name, "transport": config.transport} for config in harness.config.mcp_servers]},
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.post("/api/v1/plugins/connectors")
    def upsert_connector(request: Any) -> Any:
        body = request.json()
        if not isinstance(body, dict) or not isinstance(body.get("name"), str) or not body["name"].strip():
            raise BadRequest(tr('python.plugins.7650228e2aa8'), code="plugin/bad-body")
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")
        try:
            record = ConnectorRecord.from_dict(body)
        except ConnectorError as exc:
            raise BadRequest(str(exc), code="plugin/invalid")
        harness.connector_store.upsert(record)
        return json_response({"connector": record.to_dict()})

    @router.get("/api/v1/plugins/connectors/presets")
    def connector_presets(request: Any) -> Any:
        installed = {record.name for record in harness.connector_store.list()}
        return json_response(
            {
                "presets": [
                    {**preset.summary(), "installed": preset.record.name in installed}
                    for preset in PRESETS.values()
                ]
            },
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.post("/api/v1/plugins/connectors/presets/{name}")
    def install_connector_preset(request: Any, name: str) -> Any:
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")
        try:
            result = harness.install_connector_preset(name)
        except ConnectorError as exc:
            raise NotFound(str(exc), code="plugin/not-found") from exc
        return json_response(result)

    @router.patch("/api/v1/plugins/connectors/{name}")
    def update_connector(request: Any, name: str) -> Any:
        if harness.connector_store.is_plugin(name):
            raise Conflict(tr('python.web.validation.connectorOwner'), code='plugin-package/owned-connector')
        body = request.json()
        if not isinstance(body, dict):
            raise BadRequest(tr('python.plugins.37840a0a4194'), code="plugin/bad-body")
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")
        try:
            current = harness.connector_store.get(name)
        except ConnectorError as exc:
            raise NotFound(str(exc), code="plugin/not-found")
        merged = {**current.to_dict(), **{k: v for k, v in body.items() if k != "name"}}
        try:
            record = ConnectorRecord.from_dict(merged)
        except ConnectorError as exc:
            raise BadRequest(str(exc), code="plugin/invalid")
        harness.connector_store.upsert(record)
        return json_response({"connector": record.to_dict()})

    @router.delete("/api/v1/plugins/connectors/{name}")
    def remove_connector(request: Any, name: str) -> Any:
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")

        async def _remove() -> dict[str, Any]:
            if harness.connector_store.is_plugin(name):
                raise Conflict(tr('python.web.validation.connectorOwner'), code='plugin-package/owned-connector')
            try:
                await harness.connectors.disable(name)
            except ConnectorError as exc:
                raise NotFound(str(exc), code="plugin/not-found")
            harness.connector_store.remove(name)
            return {"removed": True, "name": name}

        return json_response(ctx.call(_remove()))

    @router.post("/api/v1/plugins/connectors/{name}/enabled")
    def set_connector_enabled(request: Any, name: str) -> Any:
        body = request.json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise BadRequest(tr('python.plugins.81b2c53e9d37'), code="plugin/bad-body")
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")

        async def _toggle() -> dict[str, Any]:
            try:
                if body["enabled"]:
                    return await harness.connectors.enable(name)
                return await harness.connectors.disable(name)
            except ConnectorError as exc:
                raise NotFound(str(exc), code="plugin/not-found")

        return json_response(ctx.call(_toggle()))

    @router.post("/api/v1/plugins/connectors/{name}/bind")
    def bind_connector(request: Any, name: str) -> Any:
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")

        async def _bind() -> dict[str, Any]:
            try:
                return await harness.connectors.bind(name)
            except ConnectorError as exc:
                if exc.code == "connector/auth-required":
                    raise BadRequest(str(exc), code=exc.code) from exc
                raise BadRequest(str(exc), code="connector/connect-failed")

        return json_response(ctx.call(_bind()))

    @router.post("/api/v1/plugins/connectors/{name}/unbind")
    def unbind_connector(request: Any, name: str) -> Any:
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")

        async def _unbind() -> dict[str, Any]:
            try:
                return await harness.connectors.unbind(name)
            except ConnectorError as exc:
                raise NotFound(str(exc), code="plugin/not-found")

        return json_response(ctx.call(_unbind()))

    # -- 连接器 OAuth（方式 B：浏览器授权） -------------------------------
    @router.post("/api/v1/plugins/connectors/{name}/oauth/login")
    def connector_oauth_login(request: Any, name: str) -> Any:
        """启动 OAuth 授权（设备流优先）。

        立即返回 pending 状态；后台完成发现 → 用户授权 → 令牌落库 →
        绑定。UI 轮询 ``GET .../oauth/status`` 跟踪进展。
        """
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")

        async def _start() -> dict[str, Any]:
            try:
                return await harness.connectors.begin_oauth_login(name)
            except ConnectorError as exc:
                # 未声明 OAuth / 已有授权在进行 → 400；连接器不存在 → 404
                if getattr(exc, "code", "").startswith("connector/oauth"):
                    raise BadRequest(str(exc), code=getattr(exc, "code", "connector/oauth")) from exc
                raise NotFound(str(exc), code="plugin/not-found") from exc

        return json_response(ctx.call(_start()))

    @router.get("/api/v1/plugins/connectors/{name}/oauth/status")
    def connector_oauth_status(request: Any, name: str) -> Any:
        """OAuth 状态：``none`` / ``pending``（含用户码与验证地址）/
        ``authorized``（含 scope 与过期时间）/ ``error``。"""
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")
        try:
            return json_response(harness.connectors.oauth_status(name))
        except ConnectorError as exc:
            raise NotFound(str(exc), code="plugin/not-found")

    @router.delete("/api/v1/plugins/connectors/{name}/oauth")
    def connector_oauth_logout(request: Any, name: str) -> Any:
        """注销 OAuth：取消进行中的授权、删除凭据并解绑。"""
        if harness.connectors is None:
            raise NotFound(tr('python.plugins.e944e835a38d'), code="plugin/forbidden")

        async def _logout() -> dict[str, Any]:
            try:
                return await harness.connectors.oauth_logout(name)
            except ConnectorError as exc:
                raise NotFound(str(exc), code="plugin/not-found")

        return json_response(ctx.call(_logout()))

    return router


__all__ = ["build_router"]
