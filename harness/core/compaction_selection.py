"""按当前模型投影的可移除预算选择摘要区间，不按节点数量猜测收益。"""


def select_region(surface, regions, project, estimate, *, cancelled=None, stop_requested=None):
    """长文本计数只在本次快照扫描期间复用，候选投影仍逐个重建。"""
    from ..llm.context_accounting import token_estimate_scan
    with token_estimate_scan():
        return _select_region(surface, regions, project, estimate, cancelled=cancelled, stop_requested=stop_requested)


def _select_region(surface, regions, project, estimate, *, cancelled=None, stop_requested=None):
    """纯快照预检；移除后重投影会自然计入技能全文和图片恢复成本。"""
    def stopped():
        return (cancelled is not None and cancelled.is_set()) or (stop_requested is not None and stop_requested())

    if stopped():
        return [], {'strategy': 'marginal-estimate-v1', 'cancelled': True}
    baseline = estimate(project(surface=surface))
    candidates = []
    selected_gain = selected_source = 0
    for index, region in enumerate(regions):
        if stopped():
            return [], {'strategy': 'marginal-estimate-v1', 'cancelled': True}
        excluded = {event.seq for event in region}
        remaining = [event for event in surface if event.seq not in excluded]
        gain = baseline - estimate(project(surface=remaining))
        if gain > selected_gain:
            candidates = [(index, region)]
            selected_gain = gain
        elif gain > 0 and gain == selected_gain:
            candidates.append((index, region))
    best, rank = [], None
    # 第一排序键已确定后，只投影仍可能胜出的区间；恢复成本仍用完整工作集计算。
    for index, region in candidates:
        if stopped():
            return [], {'strategy': 'marginal-estimate-v1', 'cancelled': True}
        source = estimate(project(surface=region))
        score = (-source, -index)
        if rank is None or score > rank:
            best, rank, selected_source = region, score, source
    return best, {'strategy': 'marginal-estimate-v1', 'candidate_count': len(regions),
                  'estimated_removable_tokens': selected_gain, 'estimated_source_tokens': selected_source}
