"""有界、可恢复的摘要事务；检查点不进入消息表面。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
from uuid import uuid4

from harness.i18n import tr
from .errors import HarnessError, LlmError
from ..llm.compaction_scope import active_compaction
from ..llm.context_accounting import estimate_tokens
from ..llm.retry import retry_delay
from ..llm.failure_diagnostics import failure_diagnostics
from ..session.summary_chunks import summary_chunks, summary_record


NODE_ATTEMPTS = 3
EXTRA_REQUESTS = 6


def fingerprint(value):
    """只根据实际输入生成稳定指纹，不绑定模型或凭据。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class CompactionExecutor:
    """串行处理摘要节点，持久记录每个实际请求及其结算。"""

    def __init__(self, agent, config, *, session=None, compaction_id=None, restart=False, context_guard=None):
        self.agent, self.config = agent, config
        self.session, self.compaction_id = session, compaction_id
        self.restart = restart
        self.context_guard = context_guard
        self.job = None
        self.nodes = {}
        self.cached = {}
        self.cache_sources = {}
        self.cache_jobs = set()
        self.wanted_nodes = set()
        self.current = None
        self.reserved = 0
        self.extra = 0
        self.stage = 'chunks'
        self.level = 0
        self.completed = 0
        self.total = 0
        self.request_started = False
        self.failures = {}

    def corrupt(self):
        return HarnessError(tr('context.compaction.checkpointInvalid'), code='agent/compaction-checkpoint')

    def validate_context(self):
        """真实约束变化后，不把当前响应登记到旧语义指纹下。"""
        if self.context_guard is not None and not self.context_guard():
            raise HarnessError(tr('context.compaction.stale'), code='agent/compaction-stale')

    async def append(self, kind, data):
        if self.session is not None:
            await self.session.append(kind, {'job_id': self.job, **data})

    async def prepare(self, identity):
        key = fingerprint(identity)
        context_key = fingerprint(identity[0])
        tasks = {}
        known_jobs = set()
        reusable = set()
        self.cached, self.cache_sources, self.failures = {}, {}, {}
        events = self.session.events if self.session is not None else ()
        for event in events:
            if event.type == 'compaction/task':
                data = event.data
                if (type(data.get('version')) is not int or data['version'] != 1
                        or not isinstance(data.get('job_id'), str) or not data['job_id']):
                    raise self.corrupt()
                for field in ('fingerprint', 'context_fingerprint'):
                    value = data.get(field)
                    if (not isinstance(value, str) or len(value) != 64
                            or any(char not in '0123456789abcdef' for char in value)):
                        raise self.corrupt()
                if data['job_id'] in known_jobs:
                    raise self.corrupt()
                known_jobs.add(data['job_id'])
                context = data.get('context_fingerprint')
                if context == context_key:
                    reusable.add(data['job_id'])
                if data['fingerprint'] == key:
                    if context != context_key:
                        raise self.corrupt()
                    tasks[data['job_id']] = data
                    reusable.add(data['job_id'])
        latest = next(reversed(tasks), None)
        charges = {job: {} for job in tasks}
        self.cache_jobs = reusable
        self.job = latest if latest and not self.restart else uuid4().hex
        # 同一输入的旧轮次成功节点可复用，但请求和预算属于各自轮次。
        for event in events:
            data = event.data
            if event.type != 'compaction/node':
                continue
            if not isinstance(data.get('job_id'), str) or data['job_id'] not in known_jobs:
                raise self.corrupt()
            if data['job_id'] not in reusable:
                continue
            node, status = data.get('node_id'), data.get('status')
            if data['job_id'] not in tasks and node not in self.wanted_nodes:
                continue
            if not isinstance(node, str) or status not in {'started', 'settled', 'success'}:
                raise self.corrupt()
            if status == 'success':
                text = data.get('summary')
                if not isinstance(text, str) or not text.strip() or data.get('summary_hash') != fingerprint(text):
                    raise self.corrupt()
                if node in self.wanted_nodes:
                    self.cached[node] = text
                    self.cache_sources[node] = data['job_id']
            else:
                if data['job_id'] not in tasks:
                    continue
                attempt = data.get('attempt')
                charge = data.get('charge')
                if type(attempt) is not int or not 1 <= attempt <= NODE_ATTEMPTS or type(charge) is not int or charge < 0:
                    raise self.corrupt()
                attempts = charges[data['job_id']].setdefault(node, {})
                if status == 'started':
                    if attempt in attempts or attempt != len(attempts) + 1:
                        raise self.corrupt()
                    attempts[attempt] = charge
                elif attempt not in attempts:
                    raise self.corrupt()
                else:
                    attempts[attempt] = charge
                    if data['job_id'] == self.job and data.get('error_code'):
                        self.failures[node] = (data['error_code'], data.get('error_details', {}))
        self.nodes = charges.get(self.job, {})
        self.extra = sum(max(0, len(attempts) - 1) for attempts in self.nodes.values())
        if self.extra > EXTRA_REQUESTS:
            raise self.corrupt()
        if latest is None or self.restart:
            await self.append('compaction/task', {'version': 1, 'fingerprint': key,
                              'context_fingerprint': context_key,
                              'compaction_id': self.compaction_id, 'previous_job_id': latest})
        self.cache_jobs.add(self.job)
        budget = self.agent.budget
        if budget is not None:
            accounted = getattr(budget, '_compaction_accounted', {})
            for job, nodes in charges.items():
                # 其他任务只提供合格成功节点；历史账目保留，不转移到新任务重复扣费。
                if job not in tasks:
                    continue
                saved = sum(sum(attempts.values()) for attempts in nodes.values())
                budget.consume(max(0, saved - accounted.get(job, 0)))
                accounted[job] = saved
            budget._compaction_accounted = accounted

    def load_stage_cache(self, pieces):
        """只索引当前阶段需要的节点，避免载入所有历史任务的摘要。"""
        self.wanted_nodes = {fingerprint([self.stage, self.level, index, piece]) for index, piece in enumerate(pieces)}
        self.cached, self.cache_sources = {}, {}
        for event in self.session.events if self.session is not None else ():
            data = event.data
            if (event.type != 'compaction/node' or data.get('status') != 'success'
                    or data.get('job_id') not in self.cache_jobs or data.get('node_id') not in self.wanted_nodes):
                continue
            text = data.get('summary')
            if not isinstance(text, str) or not text.strip() or data.get('summary_hash') != fingerprint(text):
                raise self.corrupt()
            self.cached[data['node_id']] = text
            self.cache_sources[data['node_id']] = data['job_id']

    async def progress(self, **extra):
        if self.session is not None and self.compaction_id is not None:
            data = {'compaction_id': self.compaction_id, 'job_id': self.job,
                    'completed_chunks': self.completed, 'total_chunks': self.total,
                    'stage': self.stage, 'merge_level': self.level, **extra}
            await self.session.append('compaction/progress', data)
            await self.agent._emit('compaction/progress', {'session_id': self.session.id, **data})

    def exhausted(self, cause=None):
        attempts = len(self.nodes.get(self.current, {}))
        scope = 'node' if attempts >= NODE_ATTEMPTS else 'job'
        message = tr('context.compaction.nodeExhausted' if scope == 'node' else 'context.compaction.recoveryExhausted')
        cause_code, cause_details = self.failures.get(self.current, (None, {}))
        return HarnessError(message, code='agent/compaction-recovery-exhausted',
                            job_id=self.job, node_id=self.current, compaction_recovery_exhausted=True,
                            exhausted_scope=scope, node_attempts=attempts, max_node_attempts=NODE_ATTEMPTS,
                            cause_details=failure_diagnostics(cause) if cause is not None else cause_details,
                            cause_code=getattr(cause, 'code', None) or cause_code, completed_chunks=self.completed,
                            total_chunks=self.total, extra_requests=self.extra, max_extra_requests=EXTRA_REQUESTS)

    async def before_request(self):
        """紧邻物理请求落盘；崩溃时以预留值保守计费，不刷新额度。"""
        self.agent._check_cancelled()
        self.validate_context()
        attempts = self.nodes.setdefault(self.current, {})
        count = len(attempts)
        if count >= NODE_ATTEMPTS or (count and self.extra >= EXTRA_REQUESTS):
            raise self.exhausted()
        await self.append('compaction/node', {'node_id': self.current, 'status': 'started',
                          'attempt': count + 1, 'charge': self.reserved})
        attempts[count + 1] = self.reserved
        self.request_started = True
        self.extra += bool(count)
        await self.progress(current_chunk=self.index + 1, attempt=count + 1, max_attempts=NODE_ATTEMPTS)

    async def settle(self, charge, source, error=None):
        attempts = self.nodes.get(self.current, {})
        if not self.request_started or not attempts:
            return
        attempt = len(attempts)
        await self.append('compaction/node', {'node_id': self.current, 'status': 'settled',
                          'attempt': attempt, 'charge': charge, 'usage_source': source,
                          'error_code': getattr(error, 'code', None),
                          'error_details': failure_diagnostics(error)})
        attempts[attempt] = charge
        self.failures[self.current] = (getattr(error, 'code', None), failure_diagnostics(error))
        if self.agent.budget is not None:
            accounted = getattr(self.agent.budget, '_compaction_accounted', {})
            accounted[self.job] = sum(sum(row.values()) for row in self.nodes.values())
            self.agent.budget._compaction_accounted = accounted

    @staticmethod
    def retryable(exc):
        if not isinstance(exc, LlmError) or exc.details.get('status') in {400, 401, 402, 403, 404, 422}:
            return False
        return exc.code in {'llm/text-corruption', 'llm/invalid-utf8', 'llm/no-answer', 'llm/invalid-tool-response',
                            'llm/summary-truncated', 'llm/output-limit'} or (exc.transient and exc.code not in {
                                'llm/context-length', 'llm/model-capacity', 'llm/stream-output-limit',
                                'llm/thinking-limit'})

    async def node(self, piece, index):
        from ..llm.types import UserMessage
        self.index = index
        self.agent._check_cancelled()
        self.validate_context()
        self.current = fingerprint([self.stage, self.level, index, piece])
        if self.current in self.cached:
            text = self.cached[self.current]
            if self.stage != 'merge' and estimate_tokens([UserMessage(text)]) >= estimate_tokens([UserMessage(piece)]):
                raise self.corrupt()
            await self.append('compaction/reuse', {'node_id': self.current,
                'source_job_id': self.cache_sources[self.current], 'summary_hash': fingerprint(text),
                'stage': self.stage, 'merge_level': self.level, 'index': index})
            return text
        while True:
            attempts = self.nodes.get(self.current, {})
            if len(attempts) >= NODE_ATTEMPTS or (attempts and self.extra >= EXTRA_REQUESTS):
                raise self.exhausted()
            self.agent._check_cancelled()
            before = len(attempts)
            self.request_started = False
            token = active_compaction.set(self)
            try:
                # 自定义适配器没有物理请求钩子，按一次调用保守计入一次请求。
                if not getattr(self.agent.adapter, 'supports_compaction_scope', False):
                    self.reserved = estimate_tokens([UserMessage(piece)]) + (self.config.max_tokens or self.agent.summary_max_tokens or 8192)
                    await self.before_request()
                summary = await self.agent._summarize_transcript(piece, self.config, session=self.session)
                self.validate_context()
                if not summary.strip():
                    raise LlmError(tr('context.compaction.invalidSummary'), code='llm/no-answer')
                # 合并尾段可能扩写；由整层收益判断，不能用局部变化否决整体缩减。
                if self.stage != 'merge' and estimate_tokens([UserMessage(summary)]) >= estimate_tokens([UserMessage(piece)]):
                    raise HarnessError(tr('context.compaction.noReduction'), code='agent/compaction-capacity')
                await self.append('compaction/node', {'node_id': self.current, 'status': 'success',
                                  'summary': summary, 'summary_hash': fingerprint(summary)})
                self.cached[self.current] = summary
                self.cache_sources[self.current] = self.job
                return summary
            except LlmError as exc:
                if not self.retryable(exc):
                    # 压缩内的终态错误不能由持续目标自动开启新轮次。
                    exc.details['compaction_terminal'] = True
                    raise
                attempts = self.nodes.get(self.current, {})
                if len(attempts) == before:
                    exc.details['compaction_terminal'] = True
                    raise
                if len(attempts) >= NODE_ATTEMPTS or self.extra >= EXTRA_REQUESTS:
                    raise self.exhausted(exc) from exc
                delay = retry_delay(exc.details, min(2, len(attempts)) + random.uniform(0, .2))
                await self.progress(current_chunk=index + 1, attempt=len(attempts), max_attempts=NODE_ATTEMPTS,
                                    retry_in_seconds=delay)
                await asyncio.sleep(delay)
            finally:
                active_compaction.reset(token)

    async def run(self, lines, chunk_chars, identity):
        from ..llm.types import UserMessage
        try:
            pieces = summary_chunks(lines, chunk_chars)
        except ValueError as exc:
            raise HarnessError(str(exc), code='agent/compaction-capacity') from exc
        self.stage, self.level, self.completed = 'chunks', 0, 0
        self.wanted_nodes = {fingerprint([self.stage, self.level, index, piece]) for index, piece in enumerate(pieces)}
        await self.prepare([identity, lines, chunk_chars])
        summaries = []
        self.total = len(pieces)
        await self.progress()
        for index, piece in enumerate(pieces):
            summaries.append(await self.node(piece, index))
            self.completed = len(summaries)
            await self.progress()
        combined = '\n'.join(summaries)
        for level in range(3):
            if len(summaries) == 1:
                return combined
            self.stage, self.level = 'merge', level + 1
            records = [summary_record(index, 'summary', {'text': text})
                       for index, text in enumerate(summaries)]
            pieces = summary_chunks(records, chunk_chars)
            self.load_stage_cache(pieces)
            self.total, self.completed = len(pieces), 0
            await self.progress()
            merged = []
            for index, piece in enumerate(pieces):
                merged.append(await self.node(piece, index))
                self.completed = len(merged)
                await self.progress()
            updated = '\n'.join(merged)
            if estimate_tokens([UserMessage(updated)]) >= estimate_tokens([UserMessage(combined)]):
                raise HarnessError(tr('context.compaction.noReduction'), code='agent/compaction-capacity')
            summaries, combined = merged, updated
        if len(summaries) != 1:
            raise HarnessError(tr('context.compaction.noReduction'), code='agent/compaction-capacity')
        return combined


async def recover_committed(session, emit):
    """补齐已提交摘要或中断精简的终态，不重新请求模型或执行工具。"""
    start = session.latest('compaction/start')
    if start is None:
        return False
    if any(event.type == 'compaction/end' and event.data.get('compaction_id') == start.seq
           for event in session.events if event.seq > start.seq):
        return False
    if start.data.get('stage') == 'tool-result-projection':
        from ..session.tool_projection import model_result_content
        planned = start.data.get('planned_results')
        if type(planned) is not int or planned < 1:
            raise HarnessError(tr('context.compaction.checkpointInvalid'), code='agent/compaction-checkpoint')
        count, removed = 0, 0
        for event in session.events[start.seq + 1:]:
            if event.type != 'tool/result' or not event.source_event_seqs:
                continue
            if len(event.source_event_seqs) != 1:
                raise HarnessError(tr('context.compaction.checkpointInvalid'), code='agent/compaction-checkpoint')
            source = session.event_at(event.source_event_seqs[0])
            if source is None or source.type != 'tool/result':
                raise HarnessError(tr('context.compaction.checkpointInvalid'), code='agent/compaction-checkpoint')
            before = {item['tool_use_id']: model_result_content(item) for item in source.data.get('results', ())}
            for item in event.data.get('results', ()):
                previous, current = before.get(item['tool_use_id']), model_result_content(item)
                if isinstance(previous, str) and isinstance(current, str) and len(current) < len(previous):
                    count += 1
                    removed += len(previous) - len(current)
        if count > planned:
            raise HarnessError(tr('context.compaction.checkpointInvalid'), code='agent/compaction-checkpoint')
        data = {'compaction_id': start.seq, 'turn': start.data.get('turn'), 'recovered': True,
                'after_prune': True, 'pruned_results': count, 'chars_removed': removed,
                **({'ok': True} if count == planned else {'cancelled': True})}
        await session.append('compaction/end', data)
        await emit('compaction/end', {'session_id': session.id, **data})
        # 仅补齐旧任务事实；本次请求继续按自己的设置进行容量检查。
        return False
    committed = next((event for event in session.surface if event.type == 'user/message'
                      and event.data.get('source') == 'compaction'
                      and event.data.get('compaction_id') == start.seq), None)
    if committed is None:
        return False
    data = {'compaction_id': start.seq, 'turn': start.data.get('turn'), 'ok': True, 'recovered': True}
    await session.append('compaction/end', data)
    await emit('compaction/end', {'session_id': session.id, **data})
    return True
