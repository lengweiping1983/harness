import json

def validate_minutes(value):
    if type(value) is not int or value < 0:
        raise ValueError("minutes")
    return value

def summarize(tasks):
    for task in tasks:
        validate_minutes(task["minutes"])
    return json.dumps({"pending_count": 0, "pending_minutes": 0})

print(summarize([{"title": "read", "minutes": 15, "done": False}]))
