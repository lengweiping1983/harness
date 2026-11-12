"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

cases = [{"id": "a", "expected": "supported"}, {"id": "b", "expected": "unknown"}, {"id": "c", "expected": "supported"}]
assert work["score"](cases, {"a": "supported", "b": "guess"}) == {"correct": 1, "total": 3}, "Count every case including a missing prediction."
assert work["score"](cases, {"a": "supported", "b": "unknown", "c": "supported"}) == {"correct": 3, "total": 3}
assert work["score"]([], {}) == {"correct": 0, "total": 0}, "Keep the empty evaluation explicit."

print("CHECK PASS")
