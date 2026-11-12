"""按所选端点的能力规范化最终请求。"""

from harness.i18n import tr
import json
import math

from .capabilities import minimum, positive


def context_margin(window):
    return min(1024, max(1, window // 100))


def normalize_request(body, caps, *, user_context=None, input_tokens=0):
    from ..core.errors import LlmContextLengthError
    body = dict(body)
    adjustments = []
    requested = minimum(body.pop('max_tokens', None), body.pop('max_completion_tokens', None),
                        body.pop('max_output_tokens', None))
    window = minimum(caps.get('context_window'), user_context)
    fit = None
    if window:
        fit = window - input_tokens - context_margin(window)
        if fit <= 0:
            raise LlmContextLengthError(tr('python.request_limits.62f1fce003a0'), limit=window, preflight=True)
    # 能力与上下文只约束显式预算，不能把模型容量变成每次请求的目标值。
    effective = minimum(requested, caps.get('max_output_tokens'), fit) if requested is not None else None
    if effective is not None:
        body['max_tokens'] = effective
    if effective != requested:
        adjustments.append({'key': 'ui.generation.adjustment.outputReduced' if positive(caps.get('max_output_tokens')) else 'ui.generation.adjustment.capabilityUnknown'})
    supported = caps.get('supported_parameters')
    for key, low, high in [('temperature', 0, 2), ('top_p', 0, 1)]:
        value = body.get(key)
        if value is None:
            body.pop(key, None)
        elif type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value) or (supported is not None and key not in supported):
            body.pop(key, None)
            adjustments.append({'key': 'ui.generation.adjustment.parameterDefault', 'params': {'p0': key}})
    effort = body.pop('reasoning_effort', None)
    if effort is not None and 'reasoning' not in body:
        body['reasoning'] = {'effort': effort}
    reasoning = body.get('reasoning')
    if reasoning is not None:
        if not isinstance(reasoning, dict) or caps.get('supports_thinking') is False or (supported is not None and not any(p in supported for p in ('reasoning', 'reasoning_effort'))):
            body.pop('reasoning', None)
            adjustments.append({'key': 'ui.generation.adjustment.reasoningDefault'})
        else:
            reasoning = dict(reasoning)
            effort = reasoning.get('effort')
            if effort is not None and ((effort not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max') and effort not in caps.get('supported_efforts', ())) or
                    ('supported_efforts' in caps and effort not in caps['supported_efforts']) or
                    (caps.get('reasoning_mandatory') and effort == 'none')):
                reasoning.pop('effort', None)
                adjustments.append({'key': 'ui.generation.adjustment.effortDefault'})
            if 'max_tokens' in reasoning:
                budget = positive(reasoning['max_tokens'])
                ceiling = minimum(effective, caps.get('max_output_tokens'), fit)
                if budget is not None and budget < (positive(caps.get('reasoning_min_tokens')) or 1):
                    budget = None
                if budget and ceiling is not None and ceiling > 1 and caps.get('supports_reasoning_budget') is not False:
                    # 为可见输出保留空间；Anthropic 要求思考预算
                    # 严格小于总输出预算。
                    budget = minimum(budget, ceiling - 1, caps.get('reasoning_max_tokens'))
                    if budget >= (positive(caps.get('reasoning_min_tokens')) or 1):
                        reasoning['max_tokens'] = budget
                        reasoning.pop('effort', None)
                    else:
                        reasoning.pop('max_tokens')
                        adjustments.append({'key': 'ui.generation.adjustment.budgetOmitted'})
                else:
                    reasoning.pop('max_tokens')
                    adjustments.append({'key': 'ui.generation.adjustment.budgetDefault'})
            for key in ('enabled', 'exclude'):
                if key in reasoning and type(reasoning[key]) is not bool:
                    reasoning.pop(key)
            if reasoning:
                body['reasoning'] = reasoning
            else:
                body.pop('reasoning', None)
    # 无效的可选扩展值不能破坏 JSON 序列化。
    for key in list(body):
        if key in ('model', 'messages', 'tools', 'stream', 'tool_choice'):
            continue
        try:
            json.dumps(body[key], allow_nan=False)
        except (ValueError, TypeError, OverflowError):
            body.pop(key)
            adjustments.append({'key': 'ui.generation.adjustment.parameterOmitted', 'params': {'p0': key}})
    return body, {'requested_max_tokens': requested, 'max_tokens': effective, 'context_window': window,
                  'model_max_output_tokens': caps.get('max_output_tokens'), 'capability_sources': caps.get('sources', []),
                  'adjustments': adjustments}
