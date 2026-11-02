"""显式发现 Python 用例；空套件、真实失败和发现错误均返回非零。"""
from __future__ import annotations

import argparse
import faulthandler
from functools import partial
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest

ROOT = Path(__file__).resolve().parent.parent


class DiagnosticTestResult(unittest.TextTestResult):
    """按单个用例重置堆栈诊断，健康的长模块不受总时长误判。"""

    def __init__(self, *args, traceback_timeout=120, **kwargs):
        super().__init__(*args, **kwargs)
        self.traceback_timeout = traceback_timeout
        self._module = None

    def startTest(self, test):
        module = test.__class__.__module__
        if module != self._module:
            self.stream.writeln(f'\nPython 模块：{module}')
            self.stream.flush()
            self._module = module
        super().startTest(test)
        faulthandler.dump_traceback_later(self.traceback_timeout, repeat=True, file=self.stream)

    def stopTestRun(self):
        try:
            faulthandler.cancel_dump_traceback_later()
        finally:
            super().stopTestRun()

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.stream.writeln(f'\nPython 跳过：{test.id()}：{reason}')
        self.stream.flush()


def positive_seconds(value: str) -> float:
    """拒绝无效诊断间隔，不能静默禁用长等待的堆栈证据。"""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError('堆栈诊断间隔必须是有限正数')
    return seconds


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
    parser.add_argument('--traceback-timeout', type=positive_seconds, default=120,
                        help='单个用例超过此秒数后重复输出全部线程堆栈；不终止或跳过用例（默认 120）')
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
    count = suite.countTestCases()
    if count == 0:
        print('Python 测试发现失败：没有注册用例', file=sys.stderr)
        return 1
    print(f'Python 测试发现：{count} 个用例；长等待堆栈间隔：{args.traceback_timeout:g} 秒', flush=True)
    try:
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1, durations=10,
            resultclass=partial(DiagnosticTestResult, traceback_timeout=args.traceback_timeout)).run(suite)
    finally:
        faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
