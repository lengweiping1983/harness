"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

import json

events = work["events"]
assert isinstance(events, list) and all(isinstance(event, str) for event in events), "Keep event names in an ordered list."
assert events[0] == "request/start" and events[-1] == "request/end", "Keep request boundaries visible."
assert "tool/result" in events, "Keep the tool observation in the trace."
assert json.loads(output)["events"] == events, "Export the actual events as a JSON object for the request theater."
before = list(events)
work["record"]("test/event")
assert events == before + ["test/event"], "Append an event without rewriting earlier events."

print("CHECK PASS")
