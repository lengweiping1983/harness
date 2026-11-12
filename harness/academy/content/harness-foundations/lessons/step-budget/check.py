"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["run"](1) == {"status": "budget-exhausted", "attempts": 1}, "Respect a one-step budget."
assert work["run"](0) == {"status": "budget-exhausted", "attempts": 0}, "Zero budget performs no work."

print("CHECK PASS")
