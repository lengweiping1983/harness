"""第 15 天：记忆不是把一切塞进提示词。"""
memory = {"preferred_language": "zh-CN", "explanation_style": "example-first"}

def recall(key):
    return memory.get(key, "unknown")

print(recall("explanation_style"))
print(recall("birthday"))
