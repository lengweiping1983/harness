"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

message = work.get("message")
assert isinstance(message, dict), "Use a structured message."
assert message.get("role") == "user", "Keep the user role."
assert isinstance(message.get("content"), str) and message["content"].strip(), "Keep the original task text."
assert message["role"] in output and message["content"] in output, "Display both fields."

print("CHECK PASS")
