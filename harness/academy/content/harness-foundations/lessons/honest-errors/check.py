"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["parse_count"]("3") == {"status": "ok", "count": 3}, "Parse valid input."
assert work["parse_count"]("wrong") == {"status": "invalid-input", "count": None}, "Make invalid input explicit."

print("CHECK PASS")
