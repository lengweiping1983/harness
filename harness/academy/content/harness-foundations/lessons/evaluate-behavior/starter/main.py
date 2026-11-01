"""第 29 天：答对一次还不够。"""
def evaluate(answer, authorized):
    return {"task_result": answer == 42, "permission_respected": authorized is True}

print(evaluate(42, True))
print(evaluate(42, False))
