"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert all(isinstance(work.get(key), str) and work[key].strip() for key in ("goal", "format_hint", "prompt")), "Keep all three text values."
assert work["goal"] in work["prompt"] and work["format_hint"] in work["prompt"], "The prompt must include the goal and output format."
assert work["prompt"] in output, "Show the assembled prompt."

print("CHECK PASS")
