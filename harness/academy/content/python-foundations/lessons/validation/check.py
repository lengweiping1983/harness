"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

for value in (0, 1, 25):
    assert work["validate_minutes"](value) == value, "Preserve valid values including zero."
for value in (-1, True, False, "20", None, 2.5):
    try:
        work["validate_minutes"](value)
    except ValueError:
        continue
    raise AssertionError("Reject invalid types and negative values with ValueError.")

print("CHECK PASS")
