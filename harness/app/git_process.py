"""宿主 Git 的唯一进程入口，隔离仓库环境并保留调用方的输出上限。"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def run_git(cwd: Path, *args: str, input: bytes | None = None,
            env: dict[str, str] | None = None, timeout: float = 60,
            output_limit: int | None = None) -> subprocess.CompletedProcess[bytes]:
    """仅采纳宿主显式覆盖；业务退出码和异常由调用方解释。"""
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    environment.update({'LC_ALL': 'C', **(env or {})})
    command = ['git', '-C', str(cwd), *args]
    streams = {'stdin': subprocess.DEVNULL} if input is None else {'input': input}
    if output_limit is None:
        return subprocess.run(command, env=environment, capture_output=True, timeout=timeout, **streams)
    # 大补丁先进入临时文件，只将预览所需字节读入内存。
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(command, env=environment, stdout=output, stderr=subprocess.PIPE,
                                timeout=timeout, **streams)
        output.seek(0)
        result.stdout = output.read(output_limit + 1)
        return result
