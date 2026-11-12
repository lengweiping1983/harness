"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["validate_steps"](2) == 2, "Accept a valid budget."
for value in (0, -1, True, "3"):
    try:
        work["validate_steps"](value)
    except ValueError:
        continue
    raise AssertionError("Reject invalid budgets, including booleans.")

print("CHECK PASS")
