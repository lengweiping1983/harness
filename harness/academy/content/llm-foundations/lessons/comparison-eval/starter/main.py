def score(cases, predictions):
    correct = 0
    for case in cases:
        if case["id"] in predictions and predictions[case["id"]] == case["expected"]:
            correct = correct + 1
    return {"correct": correct, "total": len(cases)}

cases = [{"id": "a", "expected": "supported"}, {"id": "b", "expected": "unknown"}]
print(score(cases, {"a": "supported"}))
