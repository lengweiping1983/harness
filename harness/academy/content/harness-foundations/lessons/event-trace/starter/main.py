"""第 23 天：给每一步留一串脚印。"""
import json

events = []

def record(kind):
    events.append(kind)

record("request/start")
record("tool/result")
record("request/end")
print(json.dumps({"events": events}))
