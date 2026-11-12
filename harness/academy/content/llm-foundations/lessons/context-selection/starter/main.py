def select_context(records, topic, limit):
    selected = []
    used = 0
    for record in records:
        text = record["text"]
        if record["topic"] == topic and used + len(text) <= limit:
            selected.append(text)
            used = used + len(text)
    return selected

print(select_context([{"topic": "hours", "text": "Monday 09:00"}], "hours", 20))
