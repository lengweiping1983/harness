"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

request = work.get("request")
assert request["jsonrpc"] == "2.0" and type(request["id"]) is int, "Keep protocol version and request identity."
assert request["method"] == "tools/call", "This exercise is a tool call."
assert isinstance(request["params"]["name"], str) and isinstance(request["params"]["arguments"], dict), "Keep tool name and structured arguments."

print("CHECK PASS")
