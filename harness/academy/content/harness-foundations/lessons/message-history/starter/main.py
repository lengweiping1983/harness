"""第 5 天：把两句话排好队。"""
messages = [{"role": "user", "content": "What is a Harness?"}]
messages.append({"role": "assistant", "content": "It connects a model to a task."})
print(messages)
print("Latest:", messages[-1]["content"])
