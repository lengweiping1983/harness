"""第 11 天：给循环一个停止线。"""
def run(max_steps=3):
    attempts = 0
    for step in range(max_steps):
        attempts += 1
    return {"status": "budget-exhausted", "attempts": attempts}

print(run())
