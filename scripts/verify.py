"""验证后端与 Web 并完整构建；桌面和浏览器仅由各自显式参数准入。"""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / 'scripts' / 'verification_policy.json'
ORDER = ('check', 'build', 'python', 'web-unit', 'runtime', 'browser', 'desktop')


def git(root: Path, *args: str) -> bytes:
    """保留以空字节分隔的文件名，Git 失败时禁止静默缩小范围。"""
    return subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout


def changed_files(root: Path, base: str) -> list[str]:
    """合并基线、暂存、未暂存及未跟踪文件，重命名按删除和新增处理。"""
    baseline = git(root, 'merge-base', 'HEAD', base).decode().strip()
    paths: set[str] = set()
    for args in (('diff', '--cached', '--name-only', '--no-renames', '-z', baseline, '--'),
                 ('diff', '--name-only', '--no-renames', '-z', '--'),
                 ('ls-files', '--others', '--exclude-standard', '-z')):
        paths.update(os.fsdecode(item) for item in git(root, *args).split(b'\0') if item)
    return sorted(paths)


def source_fingerprint(root: Path) -> str:
    """检测验证期间源码、资源、HEAD 或未提交内容变化，忽略派生产物。"""
    digest = hashlib.sha256(git(root, 'rev-parse', 'HEAD'))
    paths = set(git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0'))
    for raw in sorted(paths - {b''}):
        path = root / os.fsdecode(raw)
        digest.update(raw + b'\0')
        if path.is_symlink():
            data = b'link:' + os.fsencode(os.readlink(path))
        elif path.is_file():
            data = b'file:' + str(path.stat().st_mode).encode() + b':' + path.read_bytes()
        elif path.is_dir():
            # 子模块内部状态无法用父仓库文件指纹证明，明确拒绝签发成功回执。
            raise ValueError(f'验证输入包含目录或子模块，无法核验：{path}')
        else:
            data = b'deleted'
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def load_policy(path: Path = POLICY) -> dict:
    """校验策略结构及任务名称，损坏策略必须失败。"""
    policy = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(policy, dict) or set(policy) != {'always', 'full', 'rules'}:
        raise ValueError('验证策略字段不完整')
    if policy['always'] != ['check', 'build'] or not isinstance(policy['full'], list) or policy['full'] != ['python', 'web-unit']:
        raise ValueError('验证策略必须保留静态检查、完整构建及所有常规任务')
    if not isinstance(policy['rules'], list):
        raise ValueError('验证规则必须是列表')
    for rule in policy['rules']:
        if not isinstance(rule, dict) or set(rule) != {'patterns', 'tasks', 'reason'} or not rule['patterns'] or not isinstance(rule['reason'], str) or not rule['reason']:
            raise ValueError('无效验证规则')
        if not isinstance(rule['patterns'], list) or not all(isinstance(pattern, str) and pattern for pattern in rule['patterns']):
            raise ValueError('无效路径模式')
        if not isinstance(rule['tasks'], list) or not all(isinstance(task, str) and task in ('check', 'build', 'python', 'web-unit', 'full') for task in rule['tasks']):
            raise ValueError('未知验证任务')
    return policy


def plan_checks(paths: list[str], policy: dict, *, full: bool = False, desktop: bool = False, browser: bool = False, fallback: str = '') -> dict:
    """规则取并集；未知路径、空变更或不可用基线均扩大到全量。"""
    selected = set(policy['always'])
    reasons = []
    if full or fallback or not paths:
        selected.update(policy['full'])
        reasons.append({'path': '*', 'reason': fallback or ('显式全量' if full else '无工作区变更，验证当前完整版本')})
    for path in paths:
        matches = [rule for rule in policy['rules'] if any(fnmatchcase(path, pattern) for pattern in rule['patterns'])]
        if not matches:
            selected.update(policy['full'])
            reasons.append({'path': path, 'reason': '未识别路径，升级全量'})
        for rule in matches:
            selected.update(policy['full'] if 'full' in rule['tasks'] else rule['tasks'])
            reasons.append({'path': path, 'reason': rule['reason']})
    if browser:
        # 路径、全量及基线回退均不能代替用户对浏览器或桌面的明确选择。
        selected.add('browser')
    if desktop:
        selected.add('desktop')
    return {'files': paths, 'tasks': [task for task in ORDER if task in selected], 'reasons': reasons}


def commands(task: str, python: str, npm: str) -> list[list[str]]:
    """复用现有执行入口，生产构建始终保留 tsc、Vite 和资源发布。"""
    return {
        'check': [[python, 'scripts/check_text_encoding.py'], [python, 'scripts/lint.py'], [python, 'scripts/generate_protocol.py', '--check'],
                  [python, 'scripts/generate_i18n.py', '--check']],
        'build': [[npm, '--prefix', 'web', 'run', 'build']],
        'python': [[python, '-W', 'error::RuntimeWarning', 'scripts/run_python_tests.py']],
        'web-unit': [[npm, '--prefix', 'web', 'run', 'test:unit']],
        'runtime': [[npm, '--prefix', 'web', 'run', 'test:browser', '--', 'runtime-smoke.mjs']],
        'browser': [[npm, '--prefix', 'web', 'run', 'test:browser']],
        'desktop': [[npm, '--prefix', 'desktop', 'test']],
    }[task]


def build_manifest(root: Path) -> dict[str, str]:
    """逐文件核对完整构建目录与发布目录，遗漏、篡改及额外资源均失败。"""
    def scan(directory: Path) -> dict[str, str]:
        result = {}
        for path in sorted(directory.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'构建产物不能包含符号链接：{path}')
            if path.is_file():
                result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if 'index.html' not in result or not any(name.startswith('assets/') for name in result):
            raise ValueError(f'生产构建缺少入口或资源：{directory}')
        return result

    staged = scan(root / '.run' / 'web-build')
    published = scan(root / 'web' / 'dist')
    if staged != published:
        raise ValueError('完整构建与发布目录的资源清单或内容不一致')
    return published


def run_plan(plan: dict, root: Path, python: str, npm: str, *, expected_input: str | None = None) -> dict:
    """记录执行与未执行项，输入变化或任一失败都不能签发通过回执。"""
    before = source_fingerprint(root)
    if expected_input is not None and before != expected_input:
        raise ValueError('规划期间源码输入发生变化，请重新验证')
    env = {**os.environ, 'HARNESS_PYTHON': python, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    # 真实服务必须测试本轮发布的产物，不能被调用者的分发目录覆盖。
    env['HARNESS_WEB_DIST'] = str(root / 'web' / 'dist')
    results = []
    passed = True
    manifest = None
    for task in plan['tasks']:
        for command in commands(task, python, npm):
            if not passed:
                results.append({'task': task, 'command': command, 'status': 'not-run'})
                continue
            print(f'\n验证 {task}: {subprocess.list2cmdline(command)}', flush=True)
            started = time.monotonic()
            try:
                code = subprocess.run(command, cwd=root, env=env).returncode
            except OSError as error:
                print(f'启动失败：{error}', file=sys.stderr)
                code = 1
            results.append({'task': task, 'command': command, 'status': 'passed' if code == 0 else 'failed',
                            'exit_code': code, 'seconds': round(time.monotonic() - started, 3)})
            passed = code == 0
            if passed and task == 'build':
                try:
                    manifest = build_manifest(root)
                except (OSError, ValueError) as error:
                    results[-1].update(status='failed', error=str(error))
                    passed = False
    unchanged = source_fingerprint(root) == before
    if not unchanged:
        print('验证期间源码输入发生变化，本轮结果不能用于当前版本交付；请重新验证。', file=sys.stderr)
    build_unchanged = False
    if manifest is not None:
        try:
            build_unchanged = build_manifest(root) == manifest
        except (OSError, ValueError):
            build_unchanged = False
        if not build_unchanged:
            print('验证期间生产构建产物发生变化，请重新验证。', file=sys.stderr)
    return {**plan, 'input_fingerprint': before, 'inputs_unchanged': unchanged, 'build_manifest': manifest,
            'build_unchanged': build_unchanged, 'passed': passed and unchanged and build_unchanged, 'results': results}


def save_report(report: dict) -> Path:
    """原子替换本地回执，开始和失败状态不能遗留旧的通过结果。"""
    destination = ROOT / 'output' / 'verification.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(destination)
    return destination


def main() -> int:
    """提供稳定交付入口和只读计划，不允许用手填文件列表缩小交付范围。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='HEAD', help='与 HEAD 的共同祖先作为变更基线')
    parser.add_argument('--full', action='store_true', help='执行全部后端和 Web 常规回归，不启动桌面或浏览器')
    parser.add_argument('--desktop', action='store_true', help='明确追加桌面回归')
    parser.add_argument('--browser', action='store_true', help='明确追加浏览器回归')
    parser.add_argument('--explain', action='store_true', help='仅输出计划，不执行或签发通过回执')
    args = parser.parse_args()
    try:
        if not args.explain:
            save_report({'passed': False, 'status': 'running'})
        expected_input = None if args.explain else source_fingerprint(ROOT)
        fallback = ''
        try:
            paths = changed_files(ROOT, args.base)
        except subprocess.CalledProcessError:
            paths, fallback = [], 'Git 基线不可用，升级全量'
        plan = plan_checks(paths, load_policy(), full=args.full, desktop=args.desktop, browser=args.browser, fallback=fallback)
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        if args.explain:
            return 0
        npm = shutil.which('npm')
        if not npm:
            raise ValueError('完整构建需要 npm，请先准备开发依赖')
        report = run_plan(plan, ROOT, sys.executable, npm, expected_input=expected_input)
        destination = save_report(report)
        print(f'\n验证结果：{"通过" if report["passed"] else "失败"}；回执：{destination}')
        return 0 if report['passed'] else 1
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        if not args.explain:
            save_report({'passed': False, 'status': 'failed', 'error': str(error)})
        print(f'验证失败：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
