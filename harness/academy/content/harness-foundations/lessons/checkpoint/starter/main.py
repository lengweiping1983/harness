"""第 22 天：改坏之前，留一张快照。"""
from copy import deepcopy

state = {"files": {"agent.py": "print(42)"}}
checkpoint = deepcopy(state)
state["files"]["agent.py"] = "broken code"
restored = deepcopy(checkpoint)
print("Current:", state)
print("Restored:", restored)
