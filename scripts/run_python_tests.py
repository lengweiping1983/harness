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
        # 与课程运行器共用系统接口，预检只读取本进程。
        from harness.core.macos_process import resident_memory_kib
        measured = resident_memory_kib(os.getpid())
        if type(measured) is not int or measured <= 0:
            raise ValueError(f'proc_pid_rusage 返回无效内存读数：{measured!r}')


def academy_sandbox_preflight() -> dict | None:
    """实测课程沙箱能否真正启动；返回失败回执，通过或未安装隔离器时返回 None。

    隔离器程序存在但宿主拒绝应用收紧型 profile 时，依赖真实沙箱的用例会全部
    报出与产品逻辑无关的失败。此处在用例发现前给出结构化回执，让聚合入口以
    「基础设施不可用」明确失败，而不是把环境限制误报成业务回归；未执行的用例
    不算通过，也不会降级为无隔离执行。隔离器未安装的平台沿用各用例既有 skipIf。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from harness.academy.runtime import AcademyRuntime
    from scripts.check_academy_sandbox import check_sandbox
    if AcademyRuntime.backend() == 'unavailable':
        return None
    try:
        report = check_sandbox()
    except Exception as exc:
        return {'passed': False, 'reason': 'sandbox-probe-error',
                'error': {'type': type(exc).__name__, 'message': str(exc)}}
    return None if report['passed'] else report


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
    sys.path.insert(0, str(ROOT))
    print('Python 验证环境：' + json.dumps(environment_identity(), ensure_ascii=False), flush=True)
    try:
        check_environment()
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Python 环境预检失败；测试尚未发现或执行。原始异常：', file=sys.stderr)
        traceback.print_exc()
        return 1
    sandbox = academy_sandbox_preflight()
    if sandbox is not None:
        print('Python 课程沙箱预检失败；测试尚未发现或执行。原始回执：', file=sys.stderr)
        print(json.dumps(sandbox, ensure_ascii=False), file=sys.stderr)
        return 1
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
