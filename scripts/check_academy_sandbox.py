"""在临时项目中实测课程沙箱启动；失败返回非零，不执行业务测试。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.academy.runtime import AcademyRuntime

MARKER = 'harness-academy-sandbox-ready\n'


def check_sandbox() -> dict:
    """复用真实课程解释器、隔离配置及监督器，只写入本次临时目录。"""
    backend = AcademyRuntime.backend()
    report = {'passed': False, 'backend': backend, 'executable': sys.executable,
              'python': sys.version, 'platform': sys.platform}
    if backend == 'unavailable':
        return {**report, 'reason': 'sandbox-unavailable'}
    with tempfile.TemporaryDirectory(prefix='harness-academy-preflight-') as directory:
        root = Path(directory)
        (root / 'probe.py').write_text(f'print({MARKER.rstrip()!r})\n', encoding='utf-8')
        result = AcademyRuntime(root, timeout=3, output_limit=4096).run(['python', 'probe.py'])
    passed = (result.get('status') == 'passed' and type(result.get('exit_code')) is int
              and result['exit_code'] == 0 and result.get('stdout') == MARKER and result.get('stderr') == ''
              and result.get('truncated') is False and result.get('memory_limited') is False
              and result.get('sandbox') == backend)
    return {**report, 'passed': passed, 'reason': 'ready' if passed else 'sandbox-probe-failed', 'execution': result}


def main() -> int:
    """输出真实回执及原异常，不按诊断正文猜测原因或改用无隔离执行。"""
    try:
        report = check_sandbox()
    except Exception as exc:
        traceback.print_exc()
        report = {'passed': False, 'reason': 'sandbox-probe-error',
                  'error': {'type': type(exc).__name__, 'message': str(exc)}}
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
