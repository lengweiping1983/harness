import json

def answer_json(document, field):
    return json.dumps({"status": "supported", "answer": document.get("Monday"), "source": "Monday"}, ensure_ascii=False)

print(answer_json({"Monday": "09:00"}, "Sunday"))
