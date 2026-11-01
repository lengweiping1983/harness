"""第 30 天：把核心零件装成自己的小作品。"""
import json

def model_fixture(messages):
    if messages[-1]["role"] == "tool":
        return {"answer": messages[-1]["content"]}
    return {"tool": "add", "a": 20, "b": 22}

def run():
    messages = [{"role": "user", "content": "20 + 22"}]
    events = []
    for step in range(2):
        response = model_fixture(messages)
        events.append("model/request")
        if "answer" in response:
            return {"answer": response["answer"], "events": events + ["turn/end"]}
        if response["tool"] != "add":
            raise ValueError("tool-denied")
        result = response["a"] + response["b"]
        messages.append({"role": "tool", "content": result})
        events.append("tool/result")
    raise RuntimeError("budget-exhausted")

print(json.dumps(run()))
