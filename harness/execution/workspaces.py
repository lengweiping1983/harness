"""将本机项目注册表同步到 MCP；标识持久稳定，删除立即撤销后续访问。"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from ..i18n import tr
from .config import ProjectConfig
from .policy import fail


def sync_projects(config, configured=None):
    """每次工具调用读取原子注册表，既有 OAuth 连接不需要重新登录。"""
    if config.workspace_registry is None:
        return config
    try:
        payload = json.loads(config.workspace_registry.read_text(encoding='utf-8'))
        if (not isinstance(payload, dict) or type(payload.get('version')) is not int
                or payload['version'] != 1 or not isinstance(payload.get('workspaces'), list)
                or len(payload['workspaces']) > 64):
            raise ValueError(tr('python.workspace_sync.format'))
        rows = payload['workspaces']
        aliases_path = config.home / 'workspace-project-ids.json'
        aliases = json.loads(aliases_path.read_text(encoding='utf-8')) if aliases_path.exists() else {}
        if not isinstance(aliases, dict) or any(not isinstance(key, str) or not isinstance(value, str) or
            not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) for key, value in aliases.items()):
            raise ValueError(tr('python.workspace_sync.aliases'))
        if len(set(aliases.values())) != len(aliases):
            raise ValueError(tr('python.workspace_sync.conflict'))
        previous = {os.path.normcase(str(value.root)): value for value in (configured or {}).values()}
        previous.update({os.path.normcase(str(value.root)): value for value in config.projects.values()})
        for key, value in config.projects.items():
            aliases.setdefault(os.path.normcase(str(value.root)), key)
        projects = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('path'), str):
                raise ValueError(tr('python.workspace_sync.record'))
            path = Path(row['path']).expanduser()
            if not path.is_absolute() or not path.is_dir():
                continue
            root = path.resolve()
            private = config.home.parent
            if root == private or private.is_relative_to(root) or root.is_relative_to(private):
                continue
            canonical = os.path.normcase(str(root))
            identifier = aliases.get(canonical)
            if not identifier:
                name = str(row.get('name') or root.name)
                identifier = name if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) and name not in aliases.values() else 'p-' + hashlib.sha256(canonical.encode()).hexdigest()[:16]
                aliases[canonical] = identifier
            if identifier in projects:
                continue
            old = previous.get(canonical)
            projects[identifier] = ProjectConfig(root=root, writable=True, commands=True, commits=True,
                verification=old.verification if old else {}, display_name=str(row.get('name') or root.name))
        config.home.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(aliases, ensure_ascii=False, indent=2)
        if not aliases_path.exists() or aliases_path.read_text(encoding='utf-8') != serialized:
            handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=config.home,
                prefix='.workspace-project-ids-', suffix='.tmp', delete=False)
            try:
                with handle:
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, aliases_path)
            finally:
                Path(handle.name).unlink(missing_ok=True)
        return replace(config, projects=projects)
    except (OSError, ValueError, TypeError) as exc:
        fail('execution/project-registry-unavailable', tr('python.workspace_sync.unavailable', detail=str(exc)))
