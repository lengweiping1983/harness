"""第 21 天：没收到回信，先查回执。"""
receipts = {}

def submit(request_id, value):
    if request_id in receipts:
        if receipts[request_id] != value:
            raise ValueError("request-conflict")
        return receipts[request_id]
    receipts[request_id] = value
    return value

print(submit("one", 42))
print(submit("one", 42))
