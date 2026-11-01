"""核验真实 wheel 的技能字节、隔离导入与创建脚本，不调用在线模型。"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from zipfile import ZipFile


def verify(wheel: Path, *, install: bool = False) -> dict:
    """只在独立临时目录展开并验证发行资源，不安装到用户环境。"""
    root = Path(__file__).resolve().parents[1]
    source = root / '.harness' / 'skills'
    expected = {str(path.relative_to(root)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source.rglob('*') if path.is_file() and '__pycache__' not in path.parts
                and path.suffix not in {'.pyc', '.pyo'} and path.name != '.DS_Store'}
    with ZipFile(wheel) as archive:
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest()
                  for name in archive.namelist() if name.startswith('.harness/skills/') and not name.endswith('/')}
        if actual != expected:
            raise ValueError('Wheel system skills differ: ' + json.dumps({
                'missing': sorted(expected.keys() - actual.keys()), 'extra': sorted(actual.keys() - expected.keys()),
                'changed': sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])}))
        with tempfile.TemporaryDirectory(prefix='harness-wheel-check-') as temporary:
            target = Path(temporary).resolve()
            if install:
                installed = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index',
                                            '--no-deps', '--target', str(target), str(wheel)],
                                           capture_output=True, text=True, encoding='utf-8')
                if installed.returncode:
                    raise RuntimeError('Isolated wheel installation failed:\n' + installed.stderr)
                for name, digest in expected.items():
                    resource = target / name
                    if not resource.is_file() or hashlib.sha256(resource.read_bytes()).hexdigest() != digest:
                        raise ValueError('Installed system skill resource differs: ' + name)
            # 只展开经过路径约束的普通成员，拒绝绝对路径和目录穿越。
            for info in (() if install else archive.infolist()):
                path = target / info.filename
                if not path.resolve().is_relative_to(target.resolve()):
                    raise ValueError('Invalid wheel member: ' + info.filename)
                if not info.is_dir():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(info))
            code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness.skills.registry import build_default_registry
from harness.skills.paths import SYSTEM_SKILL_ROOT
from harness.skills.authoring import create_skill, validate_skill
root = Path(sys.argv[1])
assert SYSTEM_SKILL_ROOT == root / '.harness/skills', SYSTEM_SKILL_ROOT
registry = build_default_registry(cwd=str(root / 'project'), home=str(root / 'home'))
assert sorted(skill.name for skill in registry.list_all()) == json.loads(sys.argv[2])
assert all(skill.path.is_relative_to(root) for skill in registry.list_all())
created = create_skill(root / 'project/.agent/skills', 'distribution-proof', '发行版实际技能', '核对真实文件。')
assert validate_skill(created['path']) == created
import asyncio
from harness import Harness, HarnessConfig
from harness.llm.types import ToolUseBlock
from harness.tools.registry import ToolRunContext
async def check_tool():
    app = Harness(HarnessConfig(cwd=root / 'project', home=root / 'runtime-home', api_key='offline', enable_mcp=False, enable_automations=False))
    try:
        session = app.create_session()
        result = await app.tools.execute_call(ToolUseBlock(id='installed-proof', name='author_skill', input={
            'name': 'model-tool-proof', 'description': '发行版模型创建工具', 'instructions': '读取实际文件并核对输出。'}),
            ToolRunContext('installed-proof', session.id, str(root / 'project'), 1, 1))
        assert not result.is_error, result
        assert app.skills_for(session).has('model-tool-proof')
    finally:
        await app.close()
asyncio.run(check_tool())
print(json.dumps({'skills': sorted(skill.name for skill in registry.list_all()), 'created': created['name'], 'model_tool': 'author_skill'}))
'''
            result = subprocess.run([sys.executable, '-I', '-c', code, str(target), json.dumps(sorted(path.parent.name for path in source.rglob('SKILL.md')))], cwd=target,
                                    capture_output=True, text=True, encoding='utf-8')
            if result.returncode:
                raise RuntimeError('Isolated wheel verification failed:\n' + result.stderr)
            runtime = json.loads(result.stdout)
            return {'wheel': str(wheel), 'skill_files': len(actual), 'installed': install, 'runtime': runtime}


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('--install', action='store_true', help='Install offline with the current Python pip into the isolated temporary directory')
    args = parser.parse_args()
    print(json.dumps(verify(args.wheel.resolve(), install=args.install), ensure_ascii=False, indent=2))
