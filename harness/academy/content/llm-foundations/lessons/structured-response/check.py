"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

validate = work["validate_response"]
document = {"Monday": "09:00", "count": 0}
assert validate({"status": "supported", "answer": "09:00", "source": "Monday"}, document) is True
assert validate({"status": "unknown", "answer": None, "source": None}, document) is True
for value in ([], {}, {"status": "supported", "answer": "10:00", "source": "Monday"}, {"status": "supported", "answer": False, "source": "count"}, {"status": "supported", "answer": 0, "source": []}, {"status": "unknown", "answer": "guess", "source": None}, {"status": "other", "answer": None, "source": None}, {"status": "unknown", "answer": None, "source": None, "command": "run"}):
    assert validate(value, document) is False, "Reject bad shape, status, source, or evidence."

print("CHECK PASS")
