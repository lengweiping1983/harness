"""第 26 天：工具也可以说同一种语言。"""
import json

request = {
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "add", "arguments": {"a": 20, "b": 22}},
}
print(json.dumps(request))
