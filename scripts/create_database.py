"""为离线重建创建一个全新的当前数据库，不覆盖已有目标。"""
from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.core.sqlite import prepare_database

SCHEMAS = {
    'runs': 'harness.core.run_storage',
    'jobs': 'harness.tools.job_archive',
    'automations': 'harness.app.automations',
    'requests': 'harness.session.request_traces',
    'execution': 'harness.execution.store',
    'auth': 'harness.mcp_server.auth',
    'memory': 'harness.app.memory',
}


def main() -> None:
    """目标文件必须不存在；数据库结构直接使用对应业务模块的唯一声明。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=SCHEMAS)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    script = importlib.import_module(SCHEMAS[args.kind]).SCHEMA
    target = args.path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    identity = os.fstat(descriptor)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(target)) as db:
            prepare_database(db, script)
    except BaseException:
        # 只撤回本次创建且身份未变的文件，不触碰其他进程替换的目标。
        try:
            current = target.stat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                target.unlink()
        except FileNotFoundError:
            pass
        raise
    print(target)


if __name__ == '__main__':
    main()
