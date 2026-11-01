"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["recall"]("preferred_language") == "zh-CN", "Retrieve the requested memory."
assert work["recall"]("not-recorded") == "unknown", "Do not invent absent memories."

print("CHECK PASS")
