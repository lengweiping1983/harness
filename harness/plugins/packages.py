"""聚合插件包：严格读取、内容快照、原子安装及独立启用状态。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.errors import HarnessError
from ..skills.parser import parse_skill_file

NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
MAX_FILES = 4096
MAX_BYTES = 128 * 1024 * 1024
MANIFESTS = ('plugin.json', '.codex-plugin/plugin.json')


class PackageError(HarnessError):
    """插件清单、内容或状态无效，原始文件保持不变。"""

    def __init__(self, message: str):
        super().__init__(message, code='plugin-package/invalid')


class PackageConflict(PackageError):
    """已安装版本在预览后变化，拒绝覆盖当前选择。"""


def _unique_json_fields(pairs):
    """清单任意嵌套对象拒绝重复字段，不以最后一个值覆盖声明。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def _invalid_json_constant(value):
    """JSON清单不接受NaN及无穷等非标准数值。"""
    raise ValueError('invalid JSON constant')


def _open_json(path: Path, root: Path | None, flags: int) -> int:
    """包内文件逐级绑定目录句柄，不在已验证路径上重新追随父链接。"""
    if root is None or os.open not in os.supports_dir_fd:
        descriptor = os.open(path, flags)
        if root is not None:
            try:
                from ..core.file_handles import check_handle_root
                check_handle_root(descriptor, root)
            except BaseException:
                os.close(descriptor)
                raise
        return descriptor
    parts = path.relative_to(root).parts
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    expected = root.stat()
    parent = os.open(root, directory_flags)
    try:
        opened = os.fstat(parent)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise OSError('package root changed')
        for name in parts[:-1]:
            child = os.open(name, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(parts[-1], flags, dir_fd=parent)
    finally:
        os.close(parent)


def _json(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    """读取受大小限制的对象，错误保留具体文件位置。"""
    try:
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        descriptor = _open_json(path, root, flags)
        try:
            handle = os.fdopen(descriptor, 'rb')
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise PackageError(tr('plugin.validation.invalid_json', path=path))
            raw = handle.read(1024 * 1024 + 1)
            after = os.fstat(handle.fileno())
            def identity(info):
                return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
            if identity(before) != identity(after) or len(raw) <= 1024 * 1024 and len(raw) != before.st_size:
                raise PackageError(tr('plugin.validation.invalid_json', path=path))
        if len(raw) > 1024 * 1024:
            raise PackageError(tr('plugin.validation.b9486896d1d2'))
        try:
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_json_fields,
                              parse_constant=_invalid_json_constant)
            pending = [(data, 0)]
            while pending:
                value, depth = pending.pop()
                if isinstance(value, (dict, list)):
                    if depth > 64:
                        raise ValueError('JSON nesting exceeds limit')
                    pending.extend((item, depth + 1) for item in (value.values() if isinstance(value, dict) else value))
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise PackageError(tr('plugin.validation.invalid_json', path=path)) from exc
        if not isinstance(data, dict):
            raise PackageError(tr('plugin.validation.ab1ccbe65f15'))
        return data
    except OSError as exc:
        raise PackageError(f'{path}: {exc}') from exc


def _inside(root: Path, value: Any, *, directory: bool = False, allow_missing: bool = False) -> Path:
    """只接受包内相对路径，拒绝符号链接与越界。"""
    if not isinstance(value, str) or not value.strip() or Path(value).is_absolute():
        raise PackageError(tr('plugin.validation.00da98bba6e6', p0=repr(value)))
    relative = Path(value)
    if '..' in relative.parts or '\\' in value:
        raise PackageError(tr('plugin.validation.b375b896ea3d', p0=value))
    target = root / relative
    try:
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(tr('plugin.validation.899b758a80de'))
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(tr('plugin.validation.f7384699555a'))
        if (target.exists() or not allow_missing) and (directory and not target.is_dir() or not directory and not target.is_file()):
            raise ValueError(tr('plugin.validation.fb58e0f79db9'))
    except (OSError, ValueError) as exc:
        raise PackageError(f'{value}: {exc}') from exc
    return target


@dataclass(frozen=True)
class PackageSpec:
    """已验证的包声明，能力路径始终相对于已确认包根。"""
    root: Path
    name: str
    version: str
    description: str
    display_name: str
    skills: tuple[Path, ...]
    servers: dict[str, dict[str, Any]]
    hooks: tuple[dict[str, Any], ...] = ()
    apps: dict[str, dict[str, Any]] = field(default_factory=dict)

    def preview(self) -> dict[str, Any]:
        """安装前展示完整能力清单，不启动代码或外部连接。"""
        skills = [parse_skill_file(path) for path in self.skills]
        from ..app.hooks import validate_document
        hooks = [row for document in self.hooks for row in validate_document(document)]
        return {'name': self.name, 'version': self.version, 'description': self.description,
                'display_name': self.display_name, 'hooks': hooks,
                'apps': [{'name': name, **config} for name, config in self.apps.items()],
                'hooks_fingerprint': hashlib.sha256(json.dumps(self.hooks, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'skills': [{'name': f'{self.name}:{skill.name}', 'description': skill.description} for skill in skills],
                'connectors': [{'name': name, 'transport': 'streamable-http' if 'url' in config else 'stdio'}
                               for name, config in self.servers.items()]}


def read_package(root: str | Path) -> PackageSpec:
    """读取聚合插件清单及扩展，在同一边界校验所有能力。"""
    root = Path(root).expanduser().resolve()
    portable = root / 'plugin.json'
    overlay = root / '.codex-plugin/plugin.json'
    if portable.is_file():
        manifest = _json(_inside(root, 'plugin.json'), root=root)
        extensions = manifest.get('extensions', {})
        if not isinstance(extensions, dict):
            raise PackageError(tr('plugin.validation.8eebe5d22a61'))
        selected = extensions.get('com.openai')
        if selected is None and overlay.is_file():
            selected = _json(_inside(root, '.codex-plugin/plugin.json'), root=root)
        if selected is not None:
            if not isinstance(selected, dict):
                raise PackageError(tr('plugin.validation.238ea8d5a976'))
            # 内联扩展整体优先于独立扩展；身份及可移植组件由根清单决定。
            manifest = {**selected, **manifest}
        if 'mcpServers' not in manifest and (root / 'mcp.json').is_file():
            manifest['mcpServers'] = 'mcp.json'
    elif overlay.is_file():
        manifest = _json(_inside(root, '.codex-plugin/plugin.json'), root=root)
    else:
        raise PackageError(tr('plugin.validation.777419984627'))
    if 'agents' in manifest:
        raise PackageError(tr('plugin.validation.82e42589d2b5'))
    if 'skills' not in manifest and (root / 'skills').is_dir():
        manifest['skills'] = 'skills'
    name = manifest.get('name')
    version = manifest.get('version', '0.0.0')
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 64:
        raise PackageError(tr('plugin.validation.81f614de5df5'))
    if not isinstance(version, str) or not version.strip() or len(version) > 128:
        raise PackageError(tr('plugin.validation.f1dd9ad848b5'))
    description = manifest.get('description', '')
    interface = manifest.get('interface', {})
    if not isinstance(description, str) or not isinstance(interface, dict):
        raise PackageError(tr('plugin.validation.c2233cf66116'))
    display_name = interface.get('displayName', name)
    if not isinstance(display_name, str) or not display_name.strip():
        raise PackageError(tr('plugin.validation.5b090ccb2762'))
    # 没有实现的可执行声明必须明确拒绝，不能假装完成安装。
    unsupported = [key for key in ('appTemplates', 'scheduledTasks') if manifest.get(key)]
    if unsupported:
        raise PackageError(tr('plugin.validation.unsupported_capabilities', capabilities=', '.join(unsupported)))
    apps = {}
    if manifest.get('apps') is not None:
        declaration = _json(_inside(root, manifest['apps']), root=root)
        if not isinstance(declaration.get('apps'), dict):
            raise PackageError(tr('plugin.validation.d4b22c665d87'))
        for alias, config in declaration['apps'].items():
            if not isinstance(alias, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', alias) or not isinstance(config, dict):
                raise PackageError(tr('plugin.validation.28a2a8f16e88'))
            identity = config.get('id')
            if not isinstance(identity, str) or not re.fullmatch(r'(?:asdk_app_|connector_|templated_apps_)[A-Za-z0-9][A-Za-z0-9_-]*', identity):
                raise PackageError(tr('plugin.validation.ecf5d3da91ea', p0=alias))
            for key in ('required', 'optional'):
                if key in config and type(config[key]) is not bool:
                    raise PackageError(tr('plugin.validation.f786fdd642ea', p0=key, p1=alias))
            if 'required' in config and 'optional' in config and config['required'] == config['optional']:
                raise PackageError(tr('plugin.validation.c57dd360eb41', p0=alias))
            apps[alias] = {'id': identity, 'required': config.get('required', not config.get('optional', False))}
    hooks = []
    declarations = manifest.get('hooks')
    if declarations is None and (root / 'hooks/hooks.json').is_file():
        declarations = './hooks/hooks.json'
    if declarations is not None:
        from ..app.hooks import validate_document
        for item in declarations if isinstance(declarations, list) else [declarations]:
            document = _json(_inside(root, item), root=root) if isinstance(item, str) else item
            try:
                validate_document(document)
            except ValueError as exc:
                raise PackageError(tr('plugin.validation.b135f4dde7e5', p0=exc)) from exc
            hooks.append(document)
    skills = []
    if manifest.get('skills') is not None:
        skill_root = _inside(root, manifest['skills'], directory=True)
        names = set()
        for path in sorted(skill_root.glob('*/SKILL.md')):
            # 包内声明使用正斜杠，不能将 Windows 原生分隔符交给声明校验器。
            _inside(root, path.relative_to(root).as_posix())
            skill = parse_skill_file(path)
            if skill.name in names:
                raise PackageError(tr('plugin.validation.c91790a96599', p0=skill.name))
            names.add(skill.name)
            skills.append(path)
    servers = {}
    if manifest.get('mcpServers') is not None:
        declaration = _json(_inside(root, manifest['mcpServers']), root=root)
        if not isinstance(declaration.get('mcpServers'), dict):
            raise PackageError(tr('plugin.validation.bd44f20c7210'))
        for server_name, config in declaration['mcpServers'].items():
            if not isinstance(server_name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', server_name):
                raise PackageError(tr('plugin.validation.4a6807567878', p0=server_name))
            if not isinstance(config, dict) or set(config) - {'command', 'args', 'cwd', 'env', 'env_vars', 'url', 'headers', 'enabled', 'type', 'startup_timeout_sec', 'tool_timeout_sec', 'enabled_tools', 'disabled_tools'}:
                raise PackageError(tr('plugin.validation.7aba4a3427a7', p0=server_name))
            if config.get('type', 'streamable-http' if 'url' in config else 'stdio') not in ('stdio', 'streamable-http', 'http'):
                raise PackageError(tr('plugin.validation.c9a4403265f1', p0=server_name))
            for key in ('startup_timeout_sec', 'tool_timeout_sec'):
                value = config.get(key)
                if value is not None:
                    try:
                        valid_timeout = type(value) in (int, float) and value > 0 and math.isfinite(float(value))
                    except OverflowError:
                        valid_timeout = False
                    if not valid_timeout:
                        raise PackageError(tr('plugin.validation.91124b528412', p0=key, p1=server_name))
            for key in ('enabled_tools', 'disabled_tools'):
                value = config.get(key)
                if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) and item for item in value)):
                    raise PackageError(tr('plugin.validation.91124b528412', p0=key, p1=server_name))
            if 'enabled' in config and type(config['enabled']) is not bool:
                raise PackageError(tr('plugin.validation.9d5bec288f13', p0=server_name))
            remote = 'url' in config
            if remote:
                if not isinstance(config['url'], str) or not config['url'].startswith(('http://', 'https://')) or 'command' in config:
                    raise PackageError(tr('plugin.validation.f10c7ecb8820', p0=server_name))
            else:
                command = config.get('command')
                if not isinstance(command, str) or not command.strip():
                    raise PackageError(tr('plugin.validation.471364b01eb5', p0=server_name))
                if not Path(command).is_absolute() and '${' not in command and (command.startswith('.') or '/' in command or '\\' in command):
                    _inside(root, command)
                args = config.get('args', [])
                if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                    raise PackageError(tr('plugin.validation.40969675e2a4', p0=server_name))
                if 'cwd' in config:
                    if not isinstance(config['cwd'], str) or not config['cwd']:
                        raise PackageError(tr('plugin.validation.c3f609a0903a', p0=server_name))
                    if not Path(config['cwd']).is_absolute() and '${' not in config['cwd']:
                        _inside(root, config['cwd'], directory=True)
            for key in ('env', 'headers'):
                values = config.get(key, {})
                if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                    raise PackageError(tr('plugin.validation.91124b528412', p0=key, p1=server_name))
            inherited = config.get('env_vars', [])
            if not isinstance(inherited, list) or not all(isinstance(key, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) for key in inherited):
                raise PackageError(tr('plugin.validation.001ae4c1edee', p0=server_name))
            servers[server_name] = dict(config)
    if not skills and not servers and not hooks and not apps:
        raise PackageError(tr('plugin.validation.0c8145dca39e'))
    return PackageSpec(root, name, version, description, display_name, tuple(skills), servers, tuple(hooks), apps)


def _snapshot_paths(source: Path) -> list[Path]:
    """增量检查数量与文件类型，限额内才排序，避免先物化任意大目录。"""
    paths = []
    directories = [source]
    files = 0
    directory_count = 0
    while directories:
        directory = directories.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise PackageError(tr('plugin.validation.a006be6939ac', p0=path.relative_to(source)))
                if stat.S_ISDIR(mode):
                    directory_count += 1
                    if directory_count > MAX_FILES:
                        raise PackageError(tr('plugin.validation.directoryLimit', limit=MAX_FILES))
                    directories.append(path)
                else:
                    files += 1
                    if files > MAX_FILES:
                        raise PackageError(tr('plugin.validation.d1de9c1057bd', p0=MAX_FILES))
                paths.append(path)
    # 保持原来的全路径顺序，使相同包内容的审核摘要不随遍历策略变化。
    return sorted(paths)


def _snapshot(source: Path, destination: Path | None = None) -> str:
    """复制实际文件字节并计算确定摘要，不跟随符号链接或特殊文件。"""
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in _snapshot_paths(source):
        relative = path.relative_to(source)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise PackageError(tr('plugin.validation.a006be6939ac', p0=relative))
        target = destination / relative if destination is not None else None
        if stat.S_ISDIR(mode):
            if target is not None:
                target.mkdir(parents=True, exist_ok=True)
            continue
        count += 1
        if count > MAX_FILES:
            raise PackageError(tr('plugin.validation.d1de9c1057bd', p0=MAX_FILES))
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags), 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise PackageError(tr('plugin.validation.a006be6939ac', p0=relative))
            data = handle.read(MAX_BYTES - total + 1)
        total += len(data)
        if total > MAX_BYTES:
            raise PackageError(tr('plugin.validation.ac61bc0e6bee'))
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(stat.S_IMODE(mode) & 0o777)
        digest.update(str(relative).replace('\\', '/').encode())
        digest.update(b'\0' + str(len(data)).encode() + b'\0' + data)
        executable = stat.S_IMODE(mode) & 0o111
        if executable:
            # 可执行位决定本地启动行为；仅修改 chmod 也必须形成新的审核摘要。
            digest.update(b'\0executable\0' + str(executable).encode())
    return digest.hexdigest()


class PackageStore:
    """插件安装状态与版本包；写失败保持已有状态，不迁移用户存储。"""

    def __init__(self, home: str | Path):
        self.root = Path(home).expanduser() / 'plugins'
        self.path = self.root / 'state.json'
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        document = _json(self.path)
        if set(document) != {'version', 'packages'} or type(document['version']) is not int or document['version'] != 1 or not isinstance(document['packages'], dict):
            raise PackageError(tr('plugin.validation.38d1ebd8a491', p0=self.path))
        for name, row in document['packages'].items():
            if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(row, dict) or set(row) != {'digest', 'version', 'enabled', 'connectors'}:
                raise PackageError(tr('plugin.validation.38d1ebd8a491', p0=self.path))
            if not isinstance(row['digest'], str) or not re.fullmatch('[0-9a-f]{64}', row['digest']) or not isinstance(row['version'], str) or type(row['enabled']) is not bool or not isinstance(row['connectors'], dict):
                raise PackageError(tr('plugin.validation.38d1ebd8a491', p0=self.path))
            for config in row['connectors'].values():
                if not isinstance(config, dict) or set(config) != {'enabled', 'bound'} or any(type(value) is not bool for value in config.values()):
                    raise PackageError(tr('plugin.validation.15d6489a7c55', p0=self.path))
        return document['packages']

    def _write(self, packages: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix='.state-', suffix='.tmp', dir=self.root)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump({'version': 1, 'packages': packages}, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.path)
        finally:
            Path(filename).unlink(missing_ok=True)

    def package_root(self, name: str, row: dict[str, Any]) -> Path:
        """安装目录由严格状态字段推导，不接受持久化的任意绝对路径。"""
        target = self.root / 'cache' / name / row['digest']
        current = self.root
        for part in ('cache', name, row['digest']):
            current = current / part
            if current.is_symlink():
                raise PackageError(tr('plugin.validation.9fe5fce9e8ca', p0=current))
        return target

    def entries(self) -> list[tuple[PackageSpec, dict[str, Any]]]:
        with self._lock:
            result = []
            for name, row in sorted(self._read().items()):
                spec = read_package(self.package_root(name, row))
                if spec.name != name or spec.version != row['version'] or set(spec.servers) != set(row['connectors']):
                    raise PackageError(tr('plugin.validation.cb10db54c7fd', p0=name))
                result.append((spec, {**row, 'connectors': {key: dict(value) for key, value in row['connectors'].items()}}))
            return result

    def is_enabled(self, name: str) -> bool:
        with self._lock:
            return self._read().get(name, {}).get('enabled', False)

    def install(self, source: str | Path, *, validate: Callable[[PackageSpec], None] | None = None, expected_digest: str | None = None) -> dict[str, Any]:
        """安装新包：先完整快照和验证，再提交包目录与状态。"""
        return self._put(source, validate=validate, expected_digest=expected_digest)

    def update(self, name: str, source: str | Path, *, expected_digest: str, expected_current: str,
               validate: Callable[[PackageSpec], None] | None = None) -> dict[str, Any]:
        """同一包更新采用版本比较提交，保留旧缓存和当前用户偏好。"""
        return self._put(source, validate=validate, expected_digest=expected_digest,
                         replacing=name, expected_current=expected_current)

    def _put(self, source: str | Path, *, validate: Callable[[PackageSpec], None] | None,
             expected_digest: str | None, replacing: str | None = None,
             expected_current: str | None = None) -> dict[str, Any]:
        """安装和更新共用快照、缓存验证及原子状态写入。"""
        source = Path(source).expanduser().resolve()
        if not source.is_dir() or source.is_relative_to(self.root.resolve()) or self.root.resolve().is_relative_to(source):
            raise PackageError(tr('plugin.validation.abcfa517b8c8'))
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.install-', dir=self.root) as temporary:
            staging = Path(temporary) / 'package'
            staging.mkdir()
            digest = _snapshot(source, staging)
            if expected_digest is not None and digest != expected_digest:
                raise PackageError(tr('plugin.validation.fba621048a17'))
            spec = read_package(staging)
            preview = spec.preview()
            with self._lock:
                records = self._read()
                previous = None
                old_spec = None
                if replacing is not None:
                    if spec.name != replacing:
                        raise PackageError(tr('plugin.validation.0ef4b3377538'))
                    if replacing not in records:
                        raise KeyError(replacing)
                    previous = records[replacing]
                    if previous['digest'] != expected_current:
                        raise PackageConflict(tr('plugin.validation.0bfb3b92a20b'))
                    old_spec = read_package(self.package_root(replacing, previous))
                    if old_spec.name != replacing or old_spec.version != previous['version'] or set(old_spec.servers) != set(previous['connectors']):
                        raise PackageError(tr('plugin.validation.d350c845a3f7'))
                elif spec.name in records:
                    raise FileExistsError(spec.name)
                if validate is not None:
                    validate(spec)
                row = {'digest': digest, 'version': spec.version, 'enabled': True,
                       'connectors': {name: {'enabled': config.get('enabled', True), 'bound': False} for name, config in spec.servers.items()}}
                if previous is not None and old_spec is not None:
                    row['enabled'] = previous['enabled']
                    for name, config in spec.servers.items():
                        if name in previous['connectors']:
                            prior = previous['connectors'][name]
                            row['connectors'][name] = {'enabled': prior['enabled'],
                                                      'bound': prior['bound'] and old_spec.servers[name] == config}
                target = self.package_root(spec.name, row)
                created = not target.exists()
                if not created and (target.is_symlink() or not target.is_dir() or _snapshot(target) != digest):
                    raise PackageError(tr('plugin.validation.0f1b7fa576f0', p0=target))
                if created:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    staging.rename(target)
                try:
                    self._write({**records, spec.name: row})
                except BaseException:
                    # 只回收本次独占创建且未登记的内容，不触碰其他版本或用户源。
                    if created:
                        shutil.rmtree(target)
                    raise
            return {**preview, 'digest': digest, 'enabled': row['enabled']}

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            records = self._read()
            if name not in records:
                raise KeyError(name)
            if type(enabled) is not bool:
                raise PackageError(tr('plugin.validation.4d0490d08a91'))
            self._write({**records, name: {**records[name], 'enabled': enabled}})

    def set_connector(self, package: str, server: str, *, enabled: bool | None = None, bound: bool | None = None) -> None:
        with self._lock:
            records = self._read()
            if package not in records or server not in records[package]['connectors']:
                raise KeyError(f'{package}:{server}')
            row = records[package]
            changed = dict(row['connectors'][server])
            for key, value in (('enabled', enabled), ('bound', bound)):
                if value is not None:
                    if type(value) is not bool:
                        raise PackageError(tr('plugin.validation.0a5c5784cc40', p0=key))
                    changed[key] = value
            self._write({**records, package: {**row, 'connectors': {**row['connectors'], server: changed}}})

    def uninstall(self, name: str) -> None:
        """先解绑状态；内容缓存保留，不把删除缓存作为卸载成功的必要条件。"""
        with self._lock:
            records = self._read()
            if name not in records:
                raise KeyError(name)
            self._write({key: value for key, value in records.items() if key != name})
