"""第 9 天：把做完的事告诉模型。"""
def add(a, b):
    return a + b

messages = [{"role": "user", "content": "20 + 22"}]
result = add(20, 22)
messages.append({"role": "tool", "content": result})
print(messages[-1])
