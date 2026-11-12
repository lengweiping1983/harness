"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert callable(work.get("duration")), "Define duration(blocks)."
for blocks in (0, 1, 2, 5):
    assert work["duration"](blocks) == blocks * 25, "Return a duration for each input."

print("CHECK PASS")
