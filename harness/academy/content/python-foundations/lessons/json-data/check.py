"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

for record in ({"title": "学习", "minutes": 0}, {"title": "read", "minutes": 15}, {}):
    before = record.copy()
    result = work["round_trip"](record)
    assert result == before and isinstance(result, dict), "Return the parsed record with its values intact."
    assert record == before, "Do not edit the input record."

print("CHECK PASS")
