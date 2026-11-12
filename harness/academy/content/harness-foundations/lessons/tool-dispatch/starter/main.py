"""第 8 天：按名字找到正确工具。"""
def add(a, b):
    return a + b

tools = {"add": add}

def execute(name, a, b):
    if name not in tools:
        raise ValueError("unknown-tool")
    return tools[name](a, b)

print(execute("add", 20, 22))
