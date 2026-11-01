def pending_titles(tasks):
    titles = []
    for task in tasks:
        if task.get("done", False) is False:
            titles.append(task["title"])
    return titles

print(pending_titles([{"title": "read", "done": False}, {"title": "write", "done": True}]))
