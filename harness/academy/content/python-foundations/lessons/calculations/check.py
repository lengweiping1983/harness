"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

for name in ("unit_minutes", "blocks"):
    assert type(work.get(name)) is int and work[name] > 0, "Use positive integer inputs."
assert work.get("total_minutes") == work["unit_minutes"] * work["blocks"], "Calculate the total from both inputs."
assert str(work["total_minutes"]) in output, "Print the calculated total."

print("CHECK PASS")
