"""第 20 天：出错也要说真话。"""
def parse_count(text):
    try:
        return {"status": "ok", "count": int(text)}
    except ValueError:
        return {"status": "invalid-input", "count": None}

print(parse_count("3"))
print(parse_count("three"))
