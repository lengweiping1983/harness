"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

dispatch = work["dispatch"]
assert dispatch({"name": "multiply", "arguments": {"a": 0, "b": 7}}) == 0
assert dispatch({"name": "multiply", "arguments": {"a": -3, "b": 4}}) == -12
for request in ({"name": "other", "arguments": {"a": 1, "b": 2}}, {"name": "multiply", "arguments": {"a": True, "b": 2}}, {"name": "multiply", "arguments": {"a": 1, "b": 2, "command": "run"}}, {"name": "multiply", "arguments": {}}, {"name": "multiply", "arguments": []}, {"name": "multiply"}, []):
    try:
        dispatch(request)
    except ValueError:
        continue
    raise AssertionError("Reject an invalid request before execution.")

print("CHECK PASS")
