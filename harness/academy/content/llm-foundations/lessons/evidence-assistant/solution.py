import json

def answer_json(document, field):
    if field in document:
        result = {"status": "supported", "answer": document[field], "source": field}
    else:
        result = {"status": "unknown", "answer": None, "source": None}
    return json.dumps(result, ensure_ascii=False)

print(answer_json({"Monday": "09:00"}, "Sunday"))
