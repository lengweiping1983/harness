"""第 13 天：小书包先装最重要的东西。"""
def assemble(task, optional, budget):
    context = task
    if len(task) + len(optional) <= budget:
        context += optional
    return context

print(assemble("task: read", " | optional: a joke", 15))
