"""按依赖安装当前示例团队；同名配置冲突不覆盖，写入结果不明时只读回。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from harness.agents.parser import parse_agent_file

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('name', 'description', 'agent_type', 'system_prompt', 'model', 'skills', 'members', 'metadata')


def definition(agent):
    """仅比较声明语义，不把目录、排名或界面投影写回定义。"""
    value = agent.to_dict() if hasattr(agent, 'to_dict') else agent
    result = {key: value.get(key) for key in FIELDS}
    result['metadata'] = dict(result['metadata'] or {})
    return result


def examples(root, scenarios):
    """只从选定场景的当前声明取依赖，禁止缺失引用或重复定义。"""
    agents = {}
    for scenario in scenarios:
        for path in sorted((root/scenario).glob('*/AGENTS.md')):
            agent = parse_agent_file(path)
            if agent.name in agents:
                raise ValueError('重复示例定义：' + agent.name)
            agents[agent.name] = agent
    ordered, visiting, visited = [], set(), set()

    def visit(name):
        if name in visited:
            return
        if name in visiting:
            raise ValueError('示例引用存在循环：' + name)
        if name not in agents:
            raise ValueError('所选场景缺少引用：' + name)
        visiting.add(name)
        agent = agents[name]
        for member in agent.members:
            if member.agent:
                visit(member.agent)
                if agents[member.agent].is_team:
                    raise ValueError('成员不能引用团队：' + member.agent)
        visiting.remove(name)
        visited.add(name)
        ordered.append(definition(agent))

    for name in agents:
        visit(name)
    if not ordered:
        raise ValueError('所选场景没有当前 AGENTS.md 定义')
    return ordered


def request(base, path, payload=None):
    """不携带账户密钥，仅调用用户明确选择的本地服务。"""
    req = Request(base + '/api/v1' + path,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def install(definitions, call, *, apply=False):
    """先核对全部同名冲突，再依赖有序创建；永不自动覆盖或重放创建。"""
    def catalog():
        return {row['name']: row for row in call('/plugins/agents')['agents']}

    def matches(expected, actual):
        return actual is not None and actual.get('enabled', True) and definition(actual) == expected

    current = catalog()
    conflicts = [row['name'] for row in definitions if row['name'] in current and not matches(row, current[row['name']])]
    if conflicts:
        raise ValueError('同名配置不同或已停用，未覆盖：' + ', '.join(conflicts))
    results = []
    for row in definitions:
        name = row['name']
        if name in current:
            if not matches(row, current[name]):
                raise RuntimeError('安装期间出现同名配置冲突，未覆盖：' + name)
            results.append({'name': name, 'status': 'unchanged'})
            continue
        if not apply:
            results.append({'name': name, 'status': 'planned'})
            continue
        try:
            call('/plugins/agents/create', {**row, 'scope': 'user'})
        except (URLError, TimeoutError, OSError):
            # 创建可能已经落盘；只读回，不再发起同一次创建。
            current = catalog()
            if not matches(row, current.get(name)):
                raise RuntimeError('安装结果未确认，未重放创建：' + name) from None
            results.append({'name': name, 'status': 'confirmed_after_error'})
            continue
        current = catalog()
        if not matches(row, current.get(name)):
            raise RuntimeError('安装后读回不一致，未覆盖：' + name)
        results.append({'name': name, 'status': 'installed'})
    return results


def main():
    """显式选择场景；预览不写入，--apply 才安装缺失定义。"""
    root = ROOT/'examples'/'teams'
    choices = sorted(path.name for path in root.iterdir() if path.is_dir())
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--all', action='store_true')
    selection.add_argument('--scenario', action='append', choices=choices)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--base-url', default='http://127.0.0.1:5273')
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username or parsed.password):
        parser.error('仅支持本地 HTTP 服务地址，不接受路径、鉴权或查询参数')
    try:
        rows = examples(root, choices if args.all else list(dict.fromkeys(args.scenario)))
        result = install(rows, lambda path, payload=None: request(args.base_url.rstrip('/'), path, payload), apply=args.apply)
    except (ValueError, RuntimeError, URLError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
