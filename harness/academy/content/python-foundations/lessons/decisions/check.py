"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

for minutes in (0, 19, 20, 21, 60):
    assert work["ready"](minutes) is (minutes >= 20), "Include exactly 20 and return a bool."

print("CHECK PASS")
