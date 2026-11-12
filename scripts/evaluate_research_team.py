"""显式运行提示词定义的研究回归样例；不作为 Harness 通用验收规则。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import shutil
import tempfile
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'research_decision'



def score_report(report, expected, corpus, members, *, required_members=()):
    """固定主张与字面引文评分；不把引用存在等同于完整语义支持。"""
    errors, correct, traced = [], [], []
    rows = report.get('claims') if isinstance(report, dict) else None
    if not isinstance(rows, list):
        return {'passed': False, 'errors': ['missing_claims'], 'correct': [], 'traced': []}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] not in expected or row['id'] in seen:
            errors.append('unknown_or_duplicate_claim')
            continue
        name = row['id']
        seen.add(name)
        if row.get('verdict') == expected[name]['verdict']:
            correct.append(name)
        else:
            errors.append('incorrect_verdict:' + name)
        sources = row.get('sources')
        valid = isinstance(sources, list) and bool(sources)
        found = False
        for source in sources if isinstance(sources, list) else []:
            if not isinstance(source, dict):
                valid = False
                continue
            path, quote = source.get('path'), source.get('quote')
            if not isinstance(path, str) or path not in corpus or not isinstance(quote, str) or len(quote) < 4 or quote not in corpus[path]:
                valid = False
            if path == expected[name]['source']:
                found = True
        if valid and found:
            traced.append(name)
        else:
            errors.append('invalid_source:' + name)
    for name in expected.keys() - seen:
        errors.append('missing_claim:' + name)
    origins = report.get('member_sources')
    observed = set()
    if isinstance(origins, list):
        for origin in origins:
            if (isinstance(origin, dict) and isinstance(origin.get('member'), str)
                    and isinstance(origin.get('agent_id'), str)
                    and (origin['member'], origin['agent_id']) in members):
                observed.add(origin['member'])
            else:
                errors.append('invalid_member_source')
    if not set(required_members) <= observed:
        errors.append('missing_member_sources')
    return {'passed': not errors, 'errors': errors, 'correct': correct, 'traced': traced,
            'limitations': '固定分类和字面引文检查，不证明全部理由均被引文语义支持，也不是跨框架质量排名。'}


def completed_member_sources(events, agents, session_id, run_id):
    """仅把本次直接委派且成功完成的真实运行映射为成员来源。"""
    completed = {row['agent_id'] for row in agents
                 if row.get('status') == 'completed' and not row.get('error')
                 and row.get('parent_session_id') == session_id
                 and row.get('parent_agent_id') == run_id}
    return {(row['name'], row['agent_id']) for event in events if event['type'] == 'agent/team'
            for row in event['data'].get('members', []) if row['agent_id'] in completed}


def request(base, path, body=None):
    """只访问显式指定的本地 Harness；请求结果不明时不自动重放。"""
    req = Request(base + '/api/v1' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json', 'Idempotency-Key': uuid.uuid4().hex})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def member_history(base, session_id):
    """读完历史分页，不能把默认首页当作所有成员。"""
    rows, before, seen = [], None, set()
    while True:
        path = '/sessions/' + session_id + '/subagents'
        page = request(base, path + ('?' + urlencode({'before': before}) if before else ''))
        rows.extend(page['agents'])
        if not page.get('has_more'):
            return rows
        cursor = page.get('next_before')
        if not cursor or cursor in seen:
            raise ValueError('成员历史游标不前进，无法完成取证')
        seen.add(cursor)
        before = cursor


def audit_member_reads(manifest, agents, allowed_paths, call):
    """仅按显式案例限制审计原调用；未知归属、缺失及截断均不能判通过。"""
    violations, checked, successful_reads = [], 0, []
    root = posixpath.normpath(manifest['workspace'])
    allowed = {posixpath.normpath(posixpath.join(root, path)) for path in allowed_paths}
    for agent in agents:
        if (agent.get('parent_session_id') != manifest['session_id']
                or agent.get('parent_agent_id') != manifest['run_id']):
            continue
        identifier, owner = agent['agent_id'], agent.get('child_session_id')
        def reject(code, row=None):
            violations.append({'agent_id': identifier, 'call_id': (row or {}).get('call_id'), 'code': code})
        if agent.get('status') not in {'completed', 'failed', 'interrupted'}:
            reject('unsettled_member')
            continue
        if not owner:
            reject('missing_context')
            continue
        reads_before = len(successful_reads)
        after, seen = None, set()
        while True:
            query = {'agent_session': owner, 'limit': 100}
            if after:
                query['after'] = after
            prefix = '/sessions/' + manifest['session_id'] + '/trace?'
            try:
                page = call(prefix + urlencode(query))
            except (URLError, TimeoutError, OSError):
                reject('unavailable_trace')
                break
            if page.get('context_missing') or page.get('session_id') != owner:
                reject('missing_context')
                break
            for row in page['entries']:
                if row.get('kind') != 'tool':
                    continue
                if row.get('run_attribution_ambiguous') or not row.get('run_id'):
                    reject('unverified_run', row)
                    continue
                if row['run_id'] != identifier:
                    continue
                checked += 1
                if row.get('name') != 'read':
                    reject('disallowed_tool', row)
                    continue
                try:
                    detail = call(prefix + urlencode({'agent_session': owner, 'entry': row['id'], 'part': 'input'}))
                except (URLError, TimeoutError, OSError):
                    reject('unavailable_input', row)
                    continue
                try:
                    if detail.get('offset') != 0 or detail.get('next_offset') is not None:
                        raise ValueError
                    args = json.loads(detail['text'])
                    path = args.get('path') if isinstance(args, dict) else None
                    if not isinstance(path, str) or not path:
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    reject('unverified_input', row)
                    continue
                normalized = posixpath.normpath(posixpath.join(root, path))
                if normalized not in allowed:
                    reject('disallowed_path', row)
                elif row.get('status') == 'done':
                    successful_reads.append({'agent_id': identifier, 'call_id': row['call_id'],
                                             'path': posixpath.relpath(normalized, root)})
            if not page.get('has_more'):
                break
            cursor = page.get('next_after')
            if not cursor or cursor in seen:
                reject('unverified_pagination')
                break
            seen.add(cursor)
            after = cursor
        if len(successful_reads) == reads_before:
            reject('missing_successful_read')
    return {'checked_calls': checked, 'successful_reads': successful_reads, 'violations': violations,
            'limitation': '仅核对记录中的调用、参数与完成状态，不证明读取覆盖全部资料、历史符号链接目标、全部语义支持或工具级沙箱。'}


def main():
    """运行只提交一次；后续通过同一 manifest 收集已存在的运行。"""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--collect', type=Path)
    parser.add_argument('--base-url', default='http://127.0.0.1:5273')
    parser.add_argument('--model', help='显式覆盖模型；省略时使用服务端当前默认模型')
    parser.add_argument('--case', type=Path, required=True, help='显式指定回归样例目录，不能作为通用团队规则')
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        parser.error('仅支持本地 Harness HTTP 地址')
    base = args.base_url.rstrip('/')
    case = json.loads((args.case / 'task.json').read_text(encoding='utf-8'))
    expected = json.loads((args.case / 'expected.json').read_text(encoding='utf-8'))
    read_paths = case.get('member_read_paths')
    if 'member_read_paths' in case and (not isinstance(read_paths, list) or any(
            not isinstance(path, str) or path not in {p.name for p in args.case.glob('*.md')} for path in read_paths)):
        parser.error('member_read_paths 必须是当前案例原始资料文件名列表')
    if args.run:
        destination = ROOT / '.run'
        destination.mkdir(exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix='research-eval-', dir=destination))
        for source in args.case.glob('*.md'):
            shutil.copyfile(source, workspace / source.name)
        session = request(base, '/sessions', {'cwd': str(workspace), 'title': case['title'],
            **({'model': args.model} if args.model else {}), 'agent_selection': case['team']})['session']['id']
        manifest = {'base_url': base, 'session_id': session, 'workspace': str(workspace),
            'case_hashes': {name: hashlib.sha256((args.case / name).read_bytes()).hexdigest() for name in ('task.json', 'expected.json')},
            'source_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.glob('*.md')}}
        path = workspace / 'evaluation.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        prompt = case['prompt']
        run = request(base, '/sessions/' + session + '/runs', {'text': prompt})
        manifest['run_id'] = run['id']
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(path)
        return 0
    manifest = json.loads(args.collect.read_text(encoding='utf-8'))
    for name, digest in manifest['case_hashes'].items():
        if hashlib.sha256((args.case / name).read_bytes()).hexdigest() != digest:
            parser.error('样例定义已变化；不能用新标准重评旧运行：' + name)
    if manifest['base_url'] != base:
        parser.error('收集地址必须与提交地址一致')
    if 'run_id' not in manifest:
        parser.error('提交结果未知；请从已有会话读回，不能重放创建请求')
    run = request(base, '/runs/' + manifest['run_id'])
    if run['status'] in ('queued', 'running', 'stopping'):
        print(json.dumps({'status': run['status'], 'run_id': run['id']}))
        return 0
    session = request(base, '/sessions/' + manifest['session_id'])
    agents = member_history(base, manifest['session_id'])
    members = completed_member_sources(session['events'], agents, manifest['session_id'], manifest['run_id'])
    text = (run.get('result') or {}).get('text', '')
    report = None
    for block in re.findall(r'```(?:json)?\s*\n(.*?)```', text, re.DOTALL):
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and 'claims' in value:
            report = value
    workspace = Path(manifest['workspace'])
    # 哈希与提交时一样基于原始字节，引用文本解码不能改变证据身份。
    source_bytes = {p.name: p.read_bytes() for p in workspace.glob('*.md')}
    corpus = {name: data.decode('utf-8') for name, data in source_bytes.items()}
    score = score_report(report, expected, corpus, members, required_members=case.get('required_members', []))
    tool_audit = audit_member_reads(manifest, agents, read_paths,
        lambda path: request(base, path)) if read_paths is not None else None
    if tool_audit:
        score['errors'].extend('member_read_scope:' + row['agent_id'] + ':' + str(row['call_id']) + ':' + row['code']
                               for row in tool_audit['violations'])
    if run['status'] != 'completed':
        score['errors'].append('run_not_completed')
    for name, digest in manifest['source_hashes'].items():
        if name not in source_bytes or hashlib.sha256(source_bytes[name]).hexdigest() != digest:
            score['errors'].append('source_changed:' + name)
    score['passed'] = not score['errors']
    result = {'manifest': manifest, 'status': run['status'], 'score': score, 'report': report, 'text': text, 'tool_audit': tool_audit}
    output = workspace / 'result.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'result': str(output), **score}, ensure_ascii=False))
    return 0 if score['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
