"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

records = [{"topic": "hours", "text": "abc"}, {"topic": "other", "text": "x"}, {"topic": "hours", "text": "toolong"}, {"topic": "hours", "text": "de"}]
before = [row.copy() for row in records]
assert work["select_context"](records, "hours", 5) == ["abc", "de"], "Continue after an oversized record and preserve complete text."
assert work["select_context"](records, "hours", 0) == [], "Respect a zero character budget."
assert work["select_context"]([], "hours", 5) == [], "Accept empty context."
assert records == before, "Preserve source records."

print("CHECK PASS")
