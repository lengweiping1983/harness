"""工作节点的阶段交接协议；模型报告不能代替原运行与冻结文件事实。"""
from __future__ import annotations

from .superagent_inbox import _error, _text


def validate_checkpoint(value):
    """显式就绪才允许解锁依赖，继续和阻塞都不能伪装为完成。"""
    if (not isinstance(value, dict) or set(value) != {'status', 'summary', 'next_steps'}
            or value['status'] not in ('ready', 'continue', 'blocked')
            or not isinstance(value['next_steps'], list) or len(value['next_steps']) > 16):
        raise ValueError
    _text(value['summary'], 'checkpoint.summary', limit=8192)
    for item in value['next_steps']:
        _text(item, 'checkpoint.next_steps', limit=2048)
    if bool(value['next_steps']) != (value['status'] == 'continue'):
        raise ValueError
    return value


def work_heads(jobs):
    """每个逻辑节点只选择最后阶段，早期冻结产物不能提前解锁依赖。"""
    result = {}
    for job in jobs:
        if job['role'] == 'work' and (job['node'] not in result or job['attempt'] > result[job['node']]['attempt']):
            result[job['node']] = job
    return result


def status_jobs(jobs):
    """每个工作节点仅展示最近两阶段；隐藏历史须已冻结，不能藏掉活动工作。"""
    heads = work_heads(jobs)
    result = []
    for job in jobs:
        if job['role'] != 'work' or job['attempt'] >= heads[job['node']]['attempt'] - 1:
            result.append(job)
        elif job['state'] != 'checkpointed':
            raise _error('corrupt')
    return result
