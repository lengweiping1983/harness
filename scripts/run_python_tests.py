"""显式发现 Python 用例；空套件、真实失败和发现错误均返回非零。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest

ROOT = Path(__file__).resolve().parent.parent


def check_environment() -> None:
    """在隔离目录核对必需语义，不按宿主应用或解释器路径猜测限制。"""
    with tempfile.TemporaryDirectory(prefix='harness-python-preflight-') as directory:
        target = Path(directory) / 'existing'
        target.mkdir()
        target.mkdir(exist_ok=True)
        (target / 'nested').mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'darwin':
        # 课程运行器通过同一命令监督内存，预检只读取本进程。
        measured = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())],
                                  capture_output=True, timeout=5, check=True)
        if not measured.stdout.strip().isdigit():
            raise ValueError(f'/bin/ps 返回无效内存读数：{measured.stdout!r}')


def environment_identity() -> dict:
    """输出实际解释器及启动注入来源，为失败归因保留证据。"""
    customization = sys.modules.get('sitecustomize')
    return {'executable': sys.executable, 'version': sys.version, 'base_prefix': sys.base_prefix,
            'platform': sys.platform, 'utf8_mode': sys.flags.utf8_mode,
            'sitecustomize': getattr(customization, '__file__', None),
            'open_supports_dir_fd': os.open in os.supports_dir_fd}


def main() -> int:
    """发现配置失效或没有用例时明确失败，避免静默跳过回归。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    print('Python 验证环境：' + json.dumps(environment_identity(), ensure_ascii=False), flush=True)
    try:
        check_environment()
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Python 环境预检失败；测试尚未发现或执行。原始异常：', file=sys.stderr)
        traceback.print_exc()
        return 1
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT))
    if suite.countTestCases() == 0:
        print('Python 测试发现失败：没有注册用例', file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
