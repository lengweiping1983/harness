"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

plan = work.get("plan")
assert isinstance(plan, list) and len(plan) >= 3, "Use at least three small steps."
assert all(isinstance(item.get("step"), str) and type(item.get("done")) is bool for item in plan), "Each step has a description and explicit state."
assert any(not item["done"] for item in plan), "Do not claim every planned step is complete."

print("CHECK PASS")
