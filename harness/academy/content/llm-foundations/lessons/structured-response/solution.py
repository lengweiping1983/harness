def validate_response(value, document):
    if not isinstance(value, dict) or set(value) != {"status", "answer", "source"}:
        return False
    if value["status"] == "unknown":
        return value["answer"] is None and value["source"] is None
    if value["status"] != "supported" or not isinstance(value["source"], str):
        return False
    field = value["source"]
    return field in document and type(value["answer"]) is type(document[field]) and value["answer"] == document[field]

print(validate_response({"status": "supported", "answer": "10:00", "source": "Monday"}, {"Monday": "09:00"}))
