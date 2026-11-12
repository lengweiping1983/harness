"""第 24 天：不知道花了多少，就写未知。"""
def estimated_cost(tokens):
    if tokens is None:
        return None
    return round(tokens * 0.001, 3)

print(estimated_cost(10))
print(estimated_cost(0))
print(estimated_cost(None))
