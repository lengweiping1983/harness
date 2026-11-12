"""第 16 天：任务进行到哪一步了。"""
def move(state, action):
    transitions = {("queued", "start"): "running", ("running", "finish"): "completed"}
    if (state, action) not in transitions:
        raise ValueError("invalid-transition")
    return transitions[(state, action)]

print(move(move("queued", "start"), "finish"))
