"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

tool = work.get("add")
assert callable(tool), "Define a callable tool."
assert tool(20, 22) == 42 and tool(7, 8) == 15, "Calculate from arguments rather than a fixed answer."
assert tool(-2, 5) == 3, "Keep ordinary arithmetic behavior."

print("CHECK PASS")
