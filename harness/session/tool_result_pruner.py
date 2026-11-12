# -*- coding: utf-8 -*-
"""超限工具结果的裁剪。

服务以 ``thresholdChars: 8192``、``headChars: 4096``、``tailChars: 1024``
挂载。这里保留的关键性质：

* **不依赖模型**：裁剪过程绝不发起 LLM 调用；
* **可安全重放**：被裁剪的节点用表面 ``replace`` 原地替换，
  原始事件的 seq 保留在 ``source_event_seqs`` 里；
* **头/中/尾三段式**：保留超预算工具结果的头部与尾部，丢掉中段，
  并在删除处打上标记，让模型知道内容是被省略了，而不是本来就不存在；
* **调用独立**：同一事件中的各个工具结果分别使用预算，保留调用身份与
  错误状态，长日志不会挤掉另一工具的短结果；
* **图片完整**：当前回合的图片不裁剪；后续回合只整体省略图像数据，
  保留路径和原始事件，不能把半截 base64 送入视觉通道；
* **缓存前缀保真**：AgentLoop 只在上下文达到压缩阈值或强制容量恢复时
  调用裁剪，避免每步修改旧结果而破坏历史前缀缓存。

在总结之前先裁掉超限工具结果的中段，可能直接省掉一次总结调用——
这正是它在长编码会话中如此重要的原因。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..i18n import tr, resources
from ..llm.vision import IMAGE_TOOL_NAMES, INLINE_IMAGE, omit_image_data

#: 编码 agent 工具输出的低摩擦默认值（沿用原实现的数值）。
DEFAULT_THRESHOLD_CHARS = 8192
DEFAULT_HEAD_CHARS = 4096
DEFAULT_TAIL_CHARS = 1024


def code_point_length(text: str) -> int:
    """按 Unicode 码点计数，保证裁剪边界不会把代理对劈开。"""
    return len(text)


@dataclass(frozen=True, slots=True)
class PruneBudget:
    """已解析、不可变的字符预算。"""

    threshold_chars: int = DEFAULT_THRESHOLD_CHARS
    head_chars: int = DEFAULT_HEAD_CHARS
    tail_chars: int = DEFAULT_TAIL_CHARS


def resolve_budget(
    threshold_chars: int | None = None,
    head_chars: int | None = None,
    tail_chars: int | None = None,
) -> PruneBudget:
    """解析并校验裁剪预算（显式失败，绝不静默降级）。"""
    threshold = DEFAULT_THRESHOLD_CHARS if threshold_chars is None else threshold_chars
    head = DEFAULT_HEAD_CHARS if head_chars is None else head_chars
    tail = DEFAULT_TAIL_CHARS if tail_chars is None else tail_chars
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0:
        raise ValueError(
            tr('tool.prune.invalidThreshold', value=repr(threshold))
        )
    for name, value in (("headChars", head), ("tailChars", tail)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                tr('tool.prune.invalidEdge', name=name, value=repr(value))
            )
    emitted = head + max(len(resources(language)['tool.prune.marker']) for language in ('en', 'zh-CN')) + tail
    if emitted > threshold:
        raise ValueError(
            tr('tool.prune.invalidTotal', emitted=emitted, threshold=threshold)
        )
    return PruneBudget(threshold_chars=threshold, head_chars=head, tail_chars=tail)


def prune_texts(texts: list[str], budget: PruneBudget) -> list[str] | None:
    """对一组文本块做头/中/尾三段式裁剪。

    与原实现算法一致：非文本块保持原有顺序；标记只出现一次，
    插入到第一个与被删除区间相交的块上；当内容本来就没超预算时返回 ``None``。
    """
    prune_marker = tr('tool.prune.marker')
    total = sum(code_point_length(t) for t in texts)
    if total <= budget.threshold_chars:
        return None
    removed_start = budget.head_chars
    removed_end = total - budget.tail_chars
    out: list[str] = []
    consumed = 0
    marker_inserted = False
    for text in texts:
        length = code_point_length(text)
        block_start = consumed
        block_end = block_start + length
        head_end = min(length, max(0, removed_start - block_start))
        tail_start = min(length, max(0, removed_end - block_start))
        intersects = block_start < removed_end and block_end > removed_start
        marker = prune_marker if (intersects and not marker_inserted) else ""
        if marker:
            marker_inserted = True
        pruned = text[:head_end] + marker + text[tail_start:]
        if pruned:
            out.append(pruned)
        consumed = block_end
    if not marker_inserted:
        # 预算已校验过、且输入确实超阈值时走不到这里；
        # 万一这个不变量被破坏，宁可保留原内容，也不要把它改坏。
        return texts
    after = sum(code_point_length(t) for t in out)
    if after > budget.threshold_chars or after >= total:
        return texts
    return out


@dataclass(slots=True)
class PrunedEntry:
    """一条已落盘的替换记录。"""

    original_seq: int
    replacement_seq: int
    call_id: str
    chars_before: int
    chars_after: int


@dataclass(slots=True)
class PruneResult:
    """一次 :meth:`ToolResultPruner.prune_session` 的汇总结果。"""

    pruned: list[PrunedEntry] = field(default_factory=list)
    chars_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.pruned)


class ToolResultPruner:
    """确定性的、不依赖模型的超限工具结果裁剪。

    :param budget: 解析后的字符预算；默认值与原实现的基础配置一致
        （8192 / 4096 / 1024）。
    :param emit_events: 是否在每次替换旁边记录影子价 ``compaction/prune``
        事件（原实现始终记录）。
    """

    def __init__(self, budget: PruneBudget | None = None, *, emit_events: bool = True) -> None:
        self.budget = budget or resolve_budget()
        self.emit_events = emit_events

    # ------------------------------------------------------------------
    def prune_event_data(self, data: Mapping[str, Any], *, image_call_ids: set[str] | None = None,
                         preserve_images: bool = True) -> tuple[dict[str, Any], int, int] | None:
        """裁剪单条 ``tool/result`` 负载。

        :returns: ``(new_data, chars_before, chars_after)``；
            若结果本来就没超预算则返回 ``None``。
        """
        # 技能是执行约束，不能用普通日志的首尾裁剪删除中间规则。
        if data.get('name') == 'skill' or data.get('skill_loads'):
            return None
        results = data.get("results")
        if not isinstance(results, (list, tuple)) or not results:
            return None
        new_results = []
        before = after = 0
        changed = False
        for item in results:
            if isinstance(item, Mapping) and isinstance(item.get("content"), str):
                from .tool_projection import model_result_content
                original_model_content = model_result_content(item)
                text = original_model_content
                before += code_point_length(text)
                image_result = image_call_ids is not None and item.get('tool_use_id') in image_call_ids
                if image_result and INLINE_IMAGE.search(text):
                    if preserve_images:
                        after += code_point_length(text)
                        new_results.append(item)
                        continue
                    text = omit_image_data(text)
                # 各项是独立的 ToolResultBlock，不是同一次调用的文本片段。
                # 拼接后裁剪会丢失较短的结果，
                # 还可能丢失内容与调用标识对应所需的位置。
                pruned = prune_texts([text], self.budget)
                projected = pruned[0] if pruned is not None else text
                if 'model_content' in item and projected != original_model_content:
                    # 不能让第二次裁剪删掉首次投影中的原件取回入口。
                    # 极小预算无法容纳完整入口时，留给后续摘要安全处理。
                    if self.budget.threshold_chars < 512:
                        projected = original_model_content
                    else:
                        from .tool_projection import project_tool_result
                        replacement = project_tool_result(item['content'], name='tool-result-projection',
                            call_id=item['tool_use_id'], max_chars=self.budget.threshold_chars)
                        projected = replacement.get('model_content', original_model_content)
                if projected != original_model_content:
                    item = {**item, ("model_content" if 'model_content' in item else "content"): projected}
                    if isinstance(item.get('projection'), Mapping):
                        item['projection'] = {**item['projection'], 'model_chars': len(projected)}
                    changed = True
                after += code_point_length(model_result_content(item))
            new_results.append(item)
        if not changed:
            return None
        new_data = dict(data)
        new_data["results"] = new_results
        new_data["pruned"] = True
        return new_data, before, after

    # ------------------------------------------------------------------
    async def prune_session(self, session: Any, *, emit: Any = None, before_seq: int | None = None, eligible_seqs: set[int] | None = None) -> PruneResult:
        """重写当前表面上每一个超预算的 ``tool/result`` 节点。

        每次替换除被裁剪的内容外都保留原始事件负载，并且紧挨在前面写一条
        ``compaction/prune`` 影子价事件，这样纯消费方无需维护逐节点状态，
        就能算出被省略掉的文本量。
        """
        from .events import ReplaceOp

        result = PruneResult()
        image_call_ids: set[str] = set()
        candidates = []
        # 表面顺序保留调用与结果的位置；同一 ID 后续用于普通工具时不能继承图片身份。
        for event in session.surface:
            if event.type == 'assistant/message':
                for block in event.data.get('content', ()):
                    if block.get('type') == 'tool_use':
                        call_id = block['id']
                        if block.get('name') in IMAGE_TOOL_NAMES:
                            image_call_ids.add(call_id)
                        else:
                            image_call_ids.discard(call_id)
            elif (event.type == 'tool/result' and
                  (before_seq is None or event.seq < before_seq) and
                  (eligible_seqs is None or event.seq in eligible_seqs)):
                candidates.append((event, {item.get('tool_use_id') for item in event.data.get('results', ())
                                           if isinstance(item, Mapping) and item.get('tool_use_id') in image_call_ids}))
        turn_start = session.latest('turn/start')
        seen_before = turn_start.seq if turn_start is not None and session.open_turn is not None else 0
        for event, event_image_call_ids in candidates:
            # SessionEvent.data 是只读的 mapping 代理，不是 dict。
            data = event.data
            if not isinstance(data, Mapping):
                continue
            # 替换节点写入时间不能把旧轮次结果重新判成当前轮次。
            origin = event
            while origin.source_event_seqs:
                source = session.event_at(min(origin.source_event_seqs))
                if source is None or source.seq >= origin.seq:
                    break
                origin = source
            outcome = self.prune_event_data(data, image_call_ids=event_image_call_ids,
                                            preserve_images=origin.seq >= seen_before)
            if outcome is None:
                continue
            new_data, before, after = outcome
            if self.emit_events:
                await session.append(
                    "compaction/prune",
                    {
                        "shadowed_range": {"start": event.seq, "end": event.seq},
                        "shadowed_seqs": [event.seq],
                        "chars_removed": before - after,
                    },
                )
            replacement = await session.append(
                "tool/result",
                new_data,
                surface_op=ReplaceOp(start_seq=event.seq, end_seq=event.seq),
                source_event_seqs=[event.seq],
            )
            result.pruned.append(
                PrunedEntry(
                    original_seq=event.seq,
                    replacement_seq=getattr(replacement, "seq", event.seq),
                    call_id=str(data.get("call_id", "")),
                    chars_before=before,
                    chars_after=after,
                )
            )
            result.chars_removed += before - after
        if emit is not None and result.changed:
            emit(
                {
                    "session_id": getattr(session, "id", ""),
                    "pruned": len(result.pruned),
                    "chars_removed": result.chars_removed,
                }
            )
        return result
