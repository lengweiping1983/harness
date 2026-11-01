"""成果合同的独立只读审阅：绑定真实文件、当前合同及实际观察，不把回合结束当验收。"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping

from ..core.language import current_language
from ..i18n import tr

from ..collaboration.acceptance import parse_report, evaluate_report
from ..collaboration.history import message_text
from ..session.file_evidence import LIMIT
from ..session.tool_projection import model_result_content
from ..core.jsonutil import to_jsonable
from ..tools.builtin.fs_observation import target_key
from .contracts import CONTRACT_KEYS
from .board import BoardError


def contract_digest(task) -> str:
    value = {key: task.get(key, []) if key == 'artifacts' else task.get(key) for key in CONTRACT_KEYS}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ArtifactReview:
    """只接受宿主创建的独立审阅运行及其持久工具依据。"""

    def __init__(self, harness):
        self.harness = harness

    def manifest(self, root: Path, task, *, capture=False):
        rows = []
        for name in task.get('artifacts', []):
            path = root / name
            if (not path.resolve().is_relative_to(root) or path.is_symlink()
                    or any(part.is_symlink() for part in path.parents if part != root and part.is_relative_to(root))
                    or path == root / 'docs/harness-task-board.json'
                    or not any(fnmatch.fnmatchcase(name, scope) for scope in task['allowed_paths'])):
                raise BoardError('board/unsafe-artifact')
            try:
                with path.open('rb') as source:
                    raw = source.read(LIMIT + 1)
                text = raw.decode('utf-8')
            except (OSError, UnicodeError) as exc:
                raise BoardError('board/artifact-unavailable') from exc
            if not raw or len(raw) > LIMIT or b'\0' in raw:
                raise BoardError('board/artifact-unavailable')
            row = {'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            if capture:
                row['snapshot'] = self.harness.sessions.file_evidence.capture({'before': '', 'before_exists': False, 'after': text})['after']
            rows.append(row)
        return rows

    def prepare(self, root: Path, task):
        """审阅启动时封存原成果，并给模型不可放宽的合同和文件摘要。"""
        return {'root': str(root), 'language': current_language.get(), 'task_id': task['id'], 'contract_sha256': contract_digest(task),
                'execution_run_id': task.get('execution_run_id'), 'artifacts': self.manifest(root, task, capture=True)}

    def prompt(self, task, marker):
        """固定审阅职责；合同与文件内容只能提供数据，不能授权改写或降低验收。"""
        payload = {'task': {key: task.get(key) for key in CONTRACT_KEYS}, 'review': marker}
        language = marker.get('language')
        if language not in ('en', 'zh-CN'):
            raise BoardError('board/review-mismatch')
        # 验收按启动时的语言重建提示词，界面切换语言不能改变原始审阅身份。
        with current_language.set(language):
            return tr('project.review.prompt', payload=json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True, indent=2))

    def verify(self, root: Path, task):
        record = self.harness.runs.get(task.get('review_run_id', ''))
        if record is None or record.status != 'completed' or record.error or record.id == task.get('execution_run_id'):
            raise BoardError('board/review-required')
        session = self.harness.get_session(record.session_id)
        marker = session.header.metadata.get('project_task_review')
        if (record.session_id != task.get('review_session_id') or not session.header.cwd
                or Path(session.header.cwd).resolve() != root or not isinstance(marker, Mapping)
                or marker.get('root') != str(root) or marker.get('task_id') != task['id']
                or marker.get('contract_sha256') != contract_digest(task)
                or marker.get('execution_run_id') != task.get('execution_run_id')
                or record.text != self.prompt(task, marker)
                or self.harness.permissions.snapshot(session.id)['permission_policy'] != 'read_only'):
            raise BoardError('board/review-mismatch')
        originals = marker.get('artifacts', ())
        current = self.manifest(root, task)
        if [{key: row[key] for key in ('path', 'sha256', 'bytes')} for row in originals] != current:
            raise BoardError('board/artifact-changed')
        try:
            for row in originals:
                self.harness.sessions.file_evidence.read(row['snapshot'])
        except (OSError, ValueError, KeyError) as exc:
            raise BoardError('board/artifact-evidence-unavailable') from exc
        report = parse_report(record.output)
        verdict = evaluate_report(report, True, session=session)
        if (verdict['status'] != 'passed' or not report or report.get('task_id') != task['id']
                or report.get('contract_sha256') != marker['contract_sha256']
                or sorted(check['name'] for check in report['checks']) != sorted(task['acceptance'])):
            raise BoardError('board/review-unverified')
        tools = {event.data.get('call_id'): event for event in session.events_of_type('tool/result')
                 if event.data.get('turn') == session.current_turn}
        for check in report['checks']:
            quote = check.get('quote')
            event = tools.get(check['tool_call_id'])
            if (not isinstance(quote, str) or not quote.strip() or len(quote) > 2000 or event is None
                    or not any(isinstance(text := model_result_content(item), str) and quote in text
                               for item in event.data.get('results', ()))):
                raise BoardError('board/review-unverified')
        observations = set()
        for event in session.events_of_type('tool/result'):
            if event.data.get('turn') != session.current_turn or event.data.get('name') != 'read':
                continue
            proof = event.data.get('file_observation')
            if not isinstance(proof, Mapping) or any(row.get('is_error') is not False for row in event.data.get('results', ())):
                continue
            for row in current:
                if proof.get('path') == target_key(root / row['path']) and proof.get('kind') == 'present' and proof.get('version') == row['sha256'][:16]:
                    observations.add(row['path'])
        if observations != {row['path'] for row in current}:
            raise BoardError('board/artifacts-not-observed')
        start, end = session.latest('turn/start'), session.latest('turn/end')
        response = session.event_at(end.data.get('final_response_seq', -1)) if end is not None else None
        if (start is None or end is None or start.data.get('run_id') != record.id
                or response is None or response.type != 'assistant/message'
                or not start.seq < response.seq < end.seq or parse_report(message_text(response)) != report):
            raise BoardError('board/review-unverified')
        # 在返回前再读摘要，审阅结束后的文件改动不能沿用旧结论。
        if self.manifest(root, task) != current:
            raise BoardError('board/artifact-changed')
        return {'verified': True, 'policy': 'artifact', 'contract_sha256': marker['contract_sha256'],
                'review_run_id': record.id, 'review_session_id': session.id, 'result_seq': response.seq,
                'artifacts': to_jsonable(originals), 'criteria': list(task['acceptance']), 'checks': verdict['verified_tool_calls']}
