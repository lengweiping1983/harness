"""第 18 天：动手之前，先问能不能。"""
def perform(request_id, approval):
    if approval.get("request_id") != request_id or approval.get("decision") != "approve":
        return []
    return ["action-executed"]

approval = {"request_id": "write-1", "decision": "approve"}
print(perform("write-1", approval))
