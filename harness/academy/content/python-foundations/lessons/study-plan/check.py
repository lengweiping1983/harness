"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

import json
for tasks, expected in (([], {"pending_count": 0, "pending_minutes": 0}), ([{"title": "a", "minutes": 0}, {"title": "b", "minutes": 12, "done": False}, {"title": "c", "minutes": 20, "done": True}], {"pending_count": 2, "pending_minutes": 12})):
    before = [task.copy() for task in tasks]
    assert json.loads(work["summarize"](tasks)) == expected, "Summarize pending tasks, including zero and empty inputs."
    assert tasks == before, "Preserve every task record."
for value in (-1, True, "15"):
    try:
        work["summarize"]([{"title": "done", "minutes": value, "done": True}])
    except ValueError:
        continue
    raise AssertionError("Validate minutes even on completed tasks.")

print("CHECK PASS")
