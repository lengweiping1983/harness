def dispatch(request):
    if not isinstance(request, dict) or set(request) != {"name", "arguments"} or request["name"] != "multiply":
        raise ValueError("tool")
    arguments = request["arguments"]
    if not isinstance(arguments, dict) or set(arguments) != {"a", "b"}:
        raise ValueError("arguments")
    if type(arguments["a"]) is not int or type(arguments["b"]) is not int:
        raise ValueError("integer")
    return arguments["a"] * arguments["b"]

print(dispatch({"name": "multiply", "arguments": {"a": 3, "b": 4}}))
