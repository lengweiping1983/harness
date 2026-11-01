def validate_response(value, document):
    return isinstance(value, dict)

print(validate_response({"status": "supported", "answer": "10:00", "source": "Monday"}, {"Monday": "09:00"}))
