"""第 10 天：终于连起来：行动后再看一眼。"""
def model_fixture(messages):
    if messages[-1]["role"] == "tool":
        return {"answer": messages[-1]["content"]}
    return {"tool": "add"}

def run():
    messages = [{"role": "user", "content": "20 + 22"}]
    for step in range(2):
        response = model_fixture(messages)
        if "answer" in response:
            return response["answer"]
        result = 20 + 22
        messages.append({"role": "tool", "content": result})

print(run())
