"""第 12 天：模型只能看见你递给它的内容。"""
history = ["task: summarize", "file: notes.txt", "tool: read complete", "answer: draft"]
context = history[-2:]
print("History:", history)
print("Context:", context)
