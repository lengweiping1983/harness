"""从权威事件日志选择一个回合，供审阅和恢复共享归属规则。"""

from harness.i18n import tr
from collections.abc import Mapping, Sequence

from .events import SessionEvent


def turn_events(events: Sequence[SessionEvent], *, turn: int | None = None,
                run_id: str | None = None) -> list[SessionEvent]:
    """唯一开始事件确定范围；缺失返回空，重复明确拒绝，不推导运行身份。"""
    if (turn is None) == (run_id is None):
        raise ValueError(tr('runtime.validation.86916878f703'))
    anchors = [index for index, event in enumerate(events)
               if event.type == 'turn/start' and isinstance(event.data, Mapping)
               and type(event.data.get('turn')) is int
               and 1 <= event.data['turn'] <= 2**53 - 1
               and (event.data.get('run_id') == run_id if run_id is not None
                    else event.data['turn'] == turn)]
    if len(anchors) > 1:
        raise ValueError(tr('runtime.validation.41e574737789'))
    if not anchors:
        return []
    start = anchors[0]
    anchor = events[start].data
    end = len(events)
    for index in range(start + 1, len(events)):
        if events[index].type == 'turn/start':
            end = index
            break
        if events[index].type == 'turn/end':
            end = index + 1
            break
    # 缺省归属沿用日志位置；显式的其他回合或运行不能成为当前回合证据。
    scoped = []
    for event in events[start:end]:
        data = event.data
        if isinstance(data, Mapping):
            if 'turn' in data and (type(data['turn']) is not int or data['turn'] != anchor['turn']):
                continue
            if data.get('run_id') is not None and data['run_id'] != anchor.get('run_id'):
                continue
        scoped.append(event)
    return scoped
