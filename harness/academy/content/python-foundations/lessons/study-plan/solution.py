import json

def validate_minutes(value):
    if type(value) is not int or value < 0:
        raise ValueError("minutes")
    return value

def summarize(tasks):
    count = 0
    total = 0
    for task in tasks:
        minutes = validate_minutes(task["minutes"])
        if task.get("done", False) is False:
            count = count + 1
            total = total + minutes
    return json.dumps({"pending_count": count, "pending_minutes": total})

print(summarize([{"title": "read", "minutes": 15, "done": False}]))
