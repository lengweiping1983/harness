"""检查自有源码 UTF-8 编码及运行库的隐式文本编码，不扫描 research。"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.json', '.md', '.toml', '.yml', '.yaml',
                 '.txt', '.ps1', '.sh', '.html', '.css', '.ini', '.cfg', '.xml', '.svg', '.lock'}
TEXT_NAMES = {'Makefile', 'LICENSE', 'MANIFEST.in', '.editorconfig', '.gitignore', '.python-version'}


def text_io_issues(source: str, name: str) -> list[str]:
    """运行库及生成脚本显式声明文件/捕获编码；测试由统一解释器入口管理。"""
    problems = []
    for node in ast.walk(ast.parse(source, filename=name)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        method = function.attr if isinstance(function, ast.Attribute) else function.id if isinstance(function, ast.Name) else ''
        keywords = {item.arg: item.value for item in node.keywords}
        missing = method in {'read_text', 'write_text'} and 'encoding' not in keywords and len(node.args) < (1 if method == 'read_text' else 2)
        missing |= method in {'run', 'Popen', 'check_output'} and 'encoding' not in keywords and any(
            isinstance(keywords.get(key), ast.Constant) and keywords[key].value is True for key in ('text', 'universal_newlines'))
        if isinstance(function, ast.Name) and method == 'open':
            mode = keywords.get('mode', node.args[1] if len(node.args) > 1 else ast.Constant('r'))
            missing |= isinstance(mode, ast.Constant) and isinstance(mode.value, str) and 'b' not in mode.value and 'encoding' not in keywords
        if method in {'decode', 'encode'}:
            encoding = node.args[0] if node.args else keywords.get('encoding', ast.Constant('utf-8'))
            policy = keywords.get('errors', node.args[1] if len(node.args) > 1 else None)
            if isinstance(encoding, ast.Constant) and str(encoding.value).lower().replace('_', '-').replace('-', '') == 'utf8' and isinstance(policy, ast.Constant) and policy.value in {'replace', 'ignore'}:
                problems.append(f'{name}:{node.lineno}: UTF-8 不允许静默替换或忽略非法字节')
        if missing:
            problems.append(f'{name}:{node.lineno}: 文本 I/O 必须显式指定编码')
    return problems


def check_repository(root: Path = ROOT) -> tuple[list[str], list[dict]]:
    """逐文件检查版本控制及新增源码，二进制与排除目录不作为文本读取。"""
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root)
    problems, records = [], []
    for raw_name in sorted(set(names.split(b'\0')) - {b''}):
        name = raw_name.decode('utf-8')
        path = root / name
        if 'research' in Path(name).parts or not path.is_file() or path.is_symlink():
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            records.append({'file': name, 'kind': 'binary-or-nontext'})
            continue
        data = path.read_bytes()
        record = {'file': name, 'kind': 'text', 'sha256': hashlib.sha256(data).hexdigest()}
        try:
            if data.startswith((b'\xef\xbb\xbf', b'\xff\xfe', b'\xfe\xff')):
                raise ValueError('源码必须为不带 BOM 的 UTF-8')
            text = data.decode('utf-8')
            record['encoding'] = 'utf-8'
            if path.suffix == '.py' and name.startswith(('harness/', 'scripts/')):
                issues = text_io_issues(text, name)
                problems.extend(issues)
                record['implicit_io'] = issues
        except (UnicodeDecodeError, ValueError, SyntaxError) as exc:
            problems.append(f'{name}: {exc}')
            record['error'] = str(exc)
        records.append(record)
    return problems, records


def main() -> int:
    """可选输出本地审查记录；统一验证默认只检查，不改版本控制文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    problems, records = check_repository()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'files': records, 'problems': problems}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for problem in problems:
        print(problem)
    print(f'UTF-8 编码检查：{len(records)} 个文件，{len(problems)} 项问题')
    return int(bool(problems))


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
