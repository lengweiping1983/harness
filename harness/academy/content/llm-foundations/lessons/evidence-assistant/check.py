"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

import json
for document, field, expected in (({"Monday": "09:00"}, "Monday", {"status": "supported", "answer": "09:00", "source": "Monday"}), ({"Monday": "09:00"}, "Sunday", {"status": "unknown", "answer": None, "source": None}), ({"count": 0}, "count", {"status": "supported", "answer": 0, "source": "count"}), ({"名字": "学习"}, "名字", {"status": "supported", "answer": "学习", "source": "名字"})):
    before = document.copy()
    raw = work["answer_json"](document, field)
    assert isinstance(raw, str), "Return JSON text."
    value = json.loads(raw)
    assert value == expected, "Answer the requested field or report unknown."
    assert type(value["answer"]) is type(expected["answer"]), "Preserve the source value type."
    assert document == before, "Keep source evidence unchanged."

print("CHECK PASS")
