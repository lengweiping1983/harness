"""本地与 Git 市场目录；所有条目复用同一个包安装服务。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
import json
from pathlib import Path
from typing import Any

from ..llm.routes import atomic_json
from .packages import NAME, PackageConflict, PackageError, _inside, _json

MANIFEST = '.agents/plugins/marketplace.json'


def _git_source(source: str) -> bool:
    return source.startswith(('https://', 'ssh://', 'file://'))



class MarketplaceStore:
    """只持久化来源地址，不复制插件能力或另建运行状态。"""

    def __init__(self, home: str | Path, cwd: str | Path):
        self.home = Path(home).expanduser().resolve()
        self.cwd = Path(cwd).expanduser().resolve()
        self.path = self.home / 'plugins/marketplaces.json'

    def registered(self) -> list[str]:
        if not self.path.exists():
            return []
        document = _json(self.path)
        if set(document) != {'version', 'sources'} or type(document['version']) is not int or document['version'] != 1 or not isinstance(document['sources'], list) or len(document['sources']) > 128:
            raise PackageError(tr('plugin.validation.c4fe979bf9bc'))
        sources = document['sources']
        if not all(isinstance(value, str) and (Path(value).is_absolute() or _git_source(value)) for value in sources) or len(set(sources)) != len(sources):
            raise PackageError(tr('plugin.validation.24cc3a4ac94f'))
        return sources

    def _git_pointer(self, source: str) -> Path:
        return self.home / 'plugin-sources/markets' / (hashlib.sha256(source.encode()).hexdigest() + '.json')

    def _cached_root(self, source: str) -> Path:
        """只读已获取的市场，不因刷新页面启动网络请求。"""
        from .git_sources import validate_source
        validate_source({'source': 'url', 'url': source})
        document = _json(self._git_pointer(source))
        if set(document) != {'source', 'path'} or document['source'] != source or not isinstance(document['path'], str):
            raise PackageError(tr('plugin.validation.27f3047bb195'))
        root = Path(document['path']).resolve()
        storage = (self.home / 'plugin-sources/git').resolve()
        if not root.is_relative_to(storage) or root == storage or not root.is_dir():
            raise PackageError(tr('plugin.validation.982d3d43aac3'))
        return root

    def manifest(self, source: str) -> Path:
        """支持市场根目录或精确清单；相对插件路径以市场根解释。"""
        path = self._cached_root(source) if _git_source(source) else Path(source).expanduser().resolve()
        if path.is_dir():
            path = path / MANIFEST
        if path.parts[-3:] != ('.agents', 'plugins', 'marketplace.json'):
            raise PackageError(tr('plugin.validation.ed02a445db87'))
        _inside(path.parents[2], MANIFEST)
        return path

    def read(self, source: str) -> dict[str, Any]:
        manifest = self.manifest(source)
        with manifest.open('rb') as stream:
            before = stream.read(1024 * 1024 + 1)
        if len(before) > 1024 * 1024:
            raise PackageError(tr('plugin.validation.791fa802a5eb'))
        try:
            document = json.loads(before)
        except (ValueError, UnicodeError) as exc:
            raise PackageError(tr('plugin.validation.cf8b991b8020')) from exc
        if not isinstance(document, dict):
            raise PackageError(tr('plugin.validation.4548e634bbd5'))
        name, entries = document.get('name'), document.get('plugins')
        if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 64 or not isinstance(entries, list) or len(entries) > 4096:
            raise PackageError(tr('plugin.validation.3ef2efd8738d'))
        interface = document.get('interface', {})
        if not isinstance(interface, dict) or not isinstance(interface.get('displayName', name), str):
            raise PackageError(tr('plugin.validation.6d560181c352'))
        plugins, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or not NAME.fullmatch(entry['name']) or len(entry['name']) > 64 or entry['name'] in seen:
                raise PackageError(tr('plugin.validation.697a06f76eb9'))
            seen.add(entry['name'])
            policy = entry.get('policy', {})
            if not isinstance(policy, dict) or policy.get('installation', 'AVAILABLE') not in ('AVAILABLE', 'NOT_AVAILABLE', 'INSTALLED_BY_DEFAULT') or policy.get('authentication', 'ON_INSTALL') not in ('ON_INSTALL', 'ON_USE'):
                raise PackageError(tr('plugin.validation.49937bdbf2d6'))
            source_data = entry.get('source')
            if not isinstance(source_data, dict) or not isinstance(source_data.get('source'), str):
                raise PackageError(tr('plugin.validation.1bec8e84067a'))
            row = {'name': entry['name'], 'category': entry.get('category', ''),
                   'installation': policy.get('installation', 'AVAILABLE'), 'authentication': policy.get('authentication', 'ON_INSTALL'),
                   'source_type': source_data['source'], 'path': '', 'error': ''}
            if not isinstance(row['category'], str):
                raise PackageError(tr('plugin.validation.d2c7ee660c22'))
            if source_data['source'] == 'local':
                try:
                    row['path'] = str(_inside(manifest.parents[2], source_data.get('path'), directory=True))
                except PackageError as exc:
                    row['error'] = str(exc)
            elif source_data['source'] in ('url', 'git-subdir'):
                try:
                    from .git_sources import validate_source
                    row['git_source'] = validate_source(source_data)
                except PackageError as exc:
                    row['error'] = str(exc)
            else:
                row['error'] = tr('plugin.validation.source_type', source=source_data['source'])
            if policy.get('products'):
                row['error'] = tr('plugin.validation.product_policy')
            plugins.append(row)
        return {'name': name, 'display_name': interface.get('displayName', name), 'path': source if _git_source(source) else str(manifest),
                'fingerprint': hashlib.sha256(before + (b'\0' + str(manifest).encode() if _git_source(source) else b'')).hexdigest(), 'plugins': plugins}

    def catalog(self) -> dict[str, Any]:
        registered = self.registered()
        defaults = [self.cwd / MANIFEST, self.home.parent / MANIFEST]
        paths = list(dict.fromkeys([str(path.resolve()) for path in defaults if path.is_file()] + registered))
        rows = []
        for source in paths:
            try:
                row = self.read(source)
                rows.append({**row, 'registered': source in registered, 'error': ''})
            except (PackageError, OSError) as exc:
                rows.append({'name': '', 'display_name': '', 'path': source, 'fingerprint': '', 'plugins': [], 'registered': source in registered, 'error': str(exc)})
        return {'marketplaces': rows}

    def add(self, source: str) -> dict[str, Any]:
        registered = self.registered()
        if _git_source(source):
            if source not in registered and len(registered) >= 128:
                raise PackageError(tr('plugin.validation.7949b1fc1645'))
            if source in registered or self._git_pointer(source).exists():
                self._cached_root(source)
            from .git_sources import materialize
            root = materialize({'source': 'url', 'url': source}, self.home / 'plugin-sources/git')
            self.read(str(root))
            # 只有完整清单验证成功后才更新指针；失败保留上次已注册市场。
            atomic_json(self._git_pointer(source), {'source': source, 'path': str(root)})
        market = self.read(source)
        if market['path'] not in registered:
            if len(registered) >= 128:
                raise PackageError(tr('plugin.validation.7949b1fc1645'))
            atomic_json(self.path, {'version': 1, 'sources': [*registered, market['path']]})
        return self.catalog()

    def remove(self, source: str) -> dict[str, Any]:
        registered = self.registered()
        if source not in registered:
            raise KeyError(source)
        atomic_json(self.path, {'version': 1, 'sources': [item for item in registered if item != source]})
        return self.catalog()

    def select(self, source: str, name: str, fingerprint: str) -> dict[str, Any]:
        market = self.read(source)
        if market['fingerprint'] != fingerprint:
            raise PackageConflict(tr('plugin.validation.1233b4347d77'))
        row = next((entry for entry in market['plugins'] if entry['name'] == name), None)
        if row is None:
            raise KeyError(name)
        if row['error'] or row['installation'] == 'NOT_AVAILABLE':
            raise PackageError(row['error'] or tr('plugin.validation.unavailable'))
        return row
