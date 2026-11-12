"""将明确选择的 Git 插件物化为普通源码，再交给唯一包服务。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .packages import MAX_BYTES, MAX_FILES, PackageError, _snapshot


def validate_source(source: dict) -> dict:
    """校验远程声明，不在目录读取期间访问网络。"""
    url = source.get('url')
    if not isinstance(url, str) or len(url) > 4096 or any(ord(char) < 32 for char in url):
        raise PackageError(tr('plugin.validation.25dcbc57c898'))
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as exc:
        raise PackageError(tr('plugin.validation.25dcbc57c898')) from exc
    if parsed.scheme not in ('https', 'ssh', 'file') or parsed.query or parsed.fragment or parsed.password:
        raise PackageError(tr('plugin.validation.b43f64da6c71'))
    if parsed.scheme != 'file' and (not parsed.hostname or parsed.scheme == 'https' and parsed.username):
        raise PackageError(tr('plugin.validation.e314fe108b8e'))
    if parsed.scheme == 'file' and (parsed.netloc or not Path(url2pathname(parsed.path)).is_absolute()):
        raise PackageError(tr('plugin.validation.d122536a1b8b'))
    ref, sha = source.get('ref'), source.get('sha')
    if ref is not None and (not isinstance(ref, str) or not ref or len(ref) > 256 or ref.startswith('-') or any(char.isspace() or ord(char) < 32 for char in ref)):
        raise PackageError(tr('plugin.validation.9081fb6566b9'))
    if sha is not None and (not isinstance(sha, str) or not re.fullmatch('[a-fA-F0-9]{40}', sha)):
        raise PackageError(tr('plugin.validation.f8ff3518c42a'))
    path = source.get('path', '.') if source['source'] == 'git-subdir' else '.'
    if not isinstance(path, str) or not path or '\\' in path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
        raise PackageError(tr('plugin.validation.74b427249f63'))
    return {'source': source['source'], 'url': url, 'ref': ref, 'sha': sha, 'path': path}


def materialize(source: dict, storage: Path) -> Path:
    """固定提交并提取普通文件，不运行仓库代码、过滤器或安装脚本。"""
    source = validate_source(source)
    storage.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_COUNT': '0'}
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='.git-plugin-', dir=storage) as temporary:
        root = Path(temporary)
        repository = root / 'repo'
        def git(*args, output=None):
            """无 shell、禁止交互，错误不回显可能含认证内容的远端输出。"""
            try:
                result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'protocol.ext.allow=never',
                                         '-c', 'protocol.file.allow=always', *args], cwd=root, env=env,
                                        stdin=subprocess.DEVNULL, stdout=output or subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=25, check=True)
                return result.stdout
            except (OSError, subprocess.SubprocessError) as exc:
                raise PackageError(tr('plugin.validation.9af1c4a643d2')) from exc
        git('init', '--bare', str(repository))
        selector = source['sha'] or source['ref'] or 'HEAD'
        git('-C', str(repository), 'fetch', '--depth=1', '--no-tags', '--', source['url'], selector)
        commit = git('-C', str(repository), 'rev-parse', '--verify', 'FETCH_HEAD^{commit}').decode().strip()
        if source['sha'] and commit.lower() != source['sha'].lower():
            raise PackageError(tr('plugin.validation.267af2148e63'))
        archive = root / 'source.tar'
        with archive.open('wb') as output:
            git('-C', str(repository), 'archive', '--format=tar', commit, '--', source['path'], output=output)
        if archive.stat().st_size > MAX_BYTES + MAX_FILES * 2048:
            raise PackageError(tr('plugin.validation.b7388a922ce7'))
        extracted = root / 'files'
        extracted.mkdir()
        total = count = 0
        with tarfile.open(archive) as bundle:
            for member in bundle:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or '..' in relative.parts or not (member.isfile() or member.isdir()):
                    raise PackageError(tr('plugin.validation.1b52cc30769c'))
                target = extracted.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                count += 1
                total += member.size
                if count > MAX_FILES or total > MAX_BYTES:
                    raise PackageError(tr('plugin.validation.fa3abc85ed39'))
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as data, target.open('xb') as output:
                    shutil.copyfileobj(data, output)
                target.chmod(member.mode & 0o777)
        plugin = extracted / source['path']
        if not plugin.is_dir():
            raise PackageError(tr('plugin.validation.3dfe200f9c11'))
        digest = _snapshot(plugin)
        target = storage / hashlib.sha256((source['url'] + '\0' + commit + '\0' + source['path'] + '\0' + digest).encode()).hexdigest()
        if target.exists():
            if _snapshot(target) != digest:
                raise PackageError(tr('plugin.validation.a19c0d3bd513'))
        else:
            plugin.rename(target)
        return target
