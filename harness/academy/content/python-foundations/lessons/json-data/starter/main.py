import json

def round_trip(record):
    encoded = json.dumps(record, ensure_ascii=False)
    return json.loads(encoded)

print(round_trip({"title": "学习", "minutes": 0}))
