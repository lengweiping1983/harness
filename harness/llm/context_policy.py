"""本地工作预算与真实模型容量分开计算。"""
DEFAULT_WORKING_BUDGET = 384_000


def compact_threshold(window, working_budget=DEFAULT_WORKING_BUDGET, output_reserve=16_000):
    """未知容量仍有有限预算；已知容量沿用窗口安全余量。"""
    if window is None:
        return working_budget
    return min(working_budget, max(1, int(window * .85) - min(output_reserve, window // 4)))
