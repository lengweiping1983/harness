"""Git 暂存与本地提交，绑定真实仓库并校验已审查的暂存版本。"""
from __future__ import annotations

from harness.core.text_encoding import diagnostic_text
import codecs
import hashlib
import re
import subprocess
from pathlib import Path
from harness.i18n import tr
from ..core.errors import HarnessError


def snapshot(service, target: Path, branch: str | None) -> dict:
    """读取暂存 blob 和 HEAD，内容变化会使审查版本失效。"""
    head_result=service.run_git(target,'rev-parse','--verify','HEAD',allowed_returncodes=(0,128))
    head=head_result.stdout.decode('ascii').strip() if head_result.returncode==0 else None
    entries=service._git_bytes(target,'ls-files','--stage','-z')
    revision=hashlib.sha256((str(head)+'\0'+str(branch)).encode()+entries).hexdigest()
    names=service._git_bytes(target,'diff','--cached','--name-status','-z','--no-renames','--no-ext-diff','--no-textconv').decode('utf-8').split('\0')
    staged=[{'status':names[index],'path':names[index+1]} for index in range(0,len(names)-1,2)]
    conflicts=bool(service._git_bytes(target,'ls-files','--unmerged','-z'))
    return {'head':head,'index_revision':revision,'staged_files':staged,'conflicts':conflicts}


def checked(service, cwd: str, expected_revision: str) -> dict:
    """不根据客户端路径决定目标，过期暂存版本要求重新审查。"""
    current=service.repository(cwd)
    if not isinstance(expected_revision,str) or current['index_revision']!=expected_revision:
        raise HarnessError(tr('ui.git.error.indexChanged'),code='git/index-changed')
    return current


def stage(service, cwd: str, paths: list[str], expected_revision: str, *, unstage: bool=False) -> dict:
    current=checked(service,cwd,expected_revision)
    if not isinstance(paths,list) or not 1<=len(paths)<=10000 or any(not isinstance(path,str) for path in paths):
        raise HarnessError(tr('ui.git.error.invalid'),code='git/invalid')
    candidates={item['path']:item for item in current['files']}
    if len(set(paths))!=len(paths) or any(path not in candidates for path in paths):
        raise HarnessError(tr('ui.git.error.fileChanged'),code='git/file-changed')
    selected=list(paths)
    for path in paths:
        original=candidates[path].get('original_path')
        if original and original not in selected and (unstage or candidates[path]['status'][0] not in {'R','C'}):
            selected.append(original)
    literal='\0'.join(':(top,literal)'+path for path in selected).encode('utf-8')+b'\0'
    root=Path(current['root'])
    if unstage:
        if current['head']:service._git_bytes(root,'restore','--staged','--source=HEAD','--pathspec-from-file=-','--pathspec-file-nul',input=literal)
        else:service._git_bytes(root,'rm','--cached','--ignore-unmatch','-f','--pathspec-from-file=-','--pathspec-file-nul',input=literal)
    else:
        service._git_bytes(root,'add','-A','--pathspec-from-file=-','--pathspec-file-nul',input=literal)
    return service.repository(cwd)


def commit(service, cwd: str, message: str, expected_revision: str) -> dict:
    if not isinstance(message,str) or not message.strip() or len(message)>8000 or '\0' in message:
        raise HarnessError(tr('ui.git.error.invalid'),code='git/invalid')
    current=checked(service,cwd,expected_revision)
    if current['conflicts']:
        raise HarnessError(tr('ui.git.error.conflicts'),code='git/conflicts')
    if not current['staged_files']:
        raise HarnessError(tr('ui.git.error.emptyIndex'),code='git/empty-index')
    # 默认 commit 只提交暂存区；提交信息走标准输入，不拼接 shell 命令。
    try:
        output=service._git_bytes(Path(current['root']),'commit','--file=-',input=message.strip().encode('utf-8'))
    except subprocess.TimeoutExpired as exc:
        # 钩子可能在提交创建之后阻塞，不能把超时描述为确定未提交。
        raise HarnessError(tr('git.commit_timeout'),code='git/commit-uncertain') from exc
    # post-commit 钩子可能再次移动 HEAD，成功标识必须来自本次 Git 摘要。
    summaries=re.findall(rb'(?m)^\[[^\]\r\n]+ ([0-9a-f]{7,64})\]',output)
    if not summaries:
        raise HarnessError(tr('git.commit_unknown'),code='git/commit-uncertain')
    try:
        committed=service.git(Path(current['root']),'rev-parse','--verify',summaries[-1].decode('ascii')+'^{commit}').strip()
    except HarnessError as exc:
        raise HarnessError(tr('git.commit_unknown'),code='git/commit-uncertain') from exc
    try:
        updated=service.repository(cwd)
    except HarnessError as exc:
        # 提交已创建，后续目录读取失败不能伪装成明确拒绝。
        raise HarnessError(tr('git.commit_unknown'),code='git/commit-uncertain') from exc
    return {'commit':committed,'output':output.decode('utf-8'),'repository':updated}


def staged_patch(service, cwd: str, path: str, expected_revision: str) -> dict:
    current=checked(service,cwd,expected_revision)
    if path not in {file['path'] for file in current['staged_files']}:
        raise HarnessError(tr('ui.git.error.fileChanged'),code='git/file-changed')
    from .review import ReviewService
    result=ReviewService()._git(Path(current['root']),'diff','--cached','--no-ext-diff','--no-textconv','--no-color','--no-renames','--',':(top,literal)'+path,output_limit=256000)
    if result.returncode:
        raise HarnessError(diagnostic_text(result.stderr),code='git/failed')
    raw=result.stdout
    truncated=len(raw)>256000
    binary=b'Binary files ' in raw or b'\0' in raw
    try:text=codecs.getincrementaldecoder('utf-8')().decode(raw[:256000],final=not truncated)
    except UnicodeError:binary=True;text=''
    return {'path':path,'diff':'' if binary else text[:256000],'truncated':truncated,'binary':binary}
