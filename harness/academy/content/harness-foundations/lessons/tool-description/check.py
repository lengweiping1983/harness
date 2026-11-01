"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

tool = work.get("tool")
assert isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"].strip(), "Give the tool a stable name."
assert isinstance(tool.get("description"), str) and len(tool["description"].strip()) >= 8, "Explain the purpose and result clearly."
assert isinstance(tool.get("parameters"), list) and tool["parameters"], "Declare the required inputs."

print("CHECK PASS")
