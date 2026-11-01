"""第 14 天：太长了，先写一张便签。"""
events = ["read failed", "read again succeeded", "answer drafted"]
summary = "A read failed, then succeeded; the answer is a draft."
context = {"summary": summary, "source_count": len(events)}
print(context)
print("Original evidence:", events)
