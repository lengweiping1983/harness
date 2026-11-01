"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["estimated_cost"](10) == 0.01, "Calculate from known usage."
assert work["estimated_cost"](0) == 0, "A measured zero is a zero."
assert work["estimated_cost"](None) is None, "Missing data stays unknown."

print("CHECK PASS")
