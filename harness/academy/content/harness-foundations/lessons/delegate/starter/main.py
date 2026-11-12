"""第 27 天：把一小步交给另一位助手。"""
def child_permissions(parent, requested):
    return sorted(set(parent) & set(requested))

child = {
    "task": "Summarize one page without changing it.",
    "permissions": child_permissions(["read"], ["read", "write"]),
}
print(child)
