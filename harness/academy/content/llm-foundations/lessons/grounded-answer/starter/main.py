def answer_from(document, field):
    if field in document:
        return {"status": "supported", "answer": document[field], "source": field}
    return {"status": "unknown", "answer": None, "source": None}

print(answer_from({"Monday": "09:00", "count": 0}, "Sunday"))
