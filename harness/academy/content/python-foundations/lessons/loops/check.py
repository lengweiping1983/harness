"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

for values in ([], [7], [10, 20, 5], [0, 0, 3]):
    before = values.copy()
    assert work["total_minutes"](values) == sum(values), "Include every duration exactly once."
    assert values == before, "Keep the input list unchanged."
assert work["total_minutes"]([2]) == 2, "Start a fresh total on each call."

print("CHECK PASS")
