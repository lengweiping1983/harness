"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

tasks = [{"title": "a", "done": False}, {"title": "b", "done": True}, {"title": "c"}]
before = [task.copy() for task in tasks]
assert work["pending_titles"](tasks) == ["a", "c"], "Read done and preserve record order."
assert work["pending_titles"]([]) == [], "Accept an empty collection."
assert tasks == before, "Do not edit input records."

print("CHECK PASS")
