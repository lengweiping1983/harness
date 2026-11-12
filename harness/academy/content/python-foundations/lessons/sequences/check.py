"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

for tasks, expected in (([], "rest"), (["read"], "read"), (["test", "write"], "test")):
    before = tasks.copy()
    assert work["first_task"](tasks) == expected, "Return the first task or rest for an empty list."
    assert tasks == before, "Read without removing tasks."

print("CHECK PASS")
