"""查询业务数据 → 明确审批 → 幂等更新 → 审计，完全离线运行。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import Harness, HarnessConfig, IdentityContext


class DemoTransport:
    """演示固定操作流程，真实接入时删去 transport 参数使用配置模型。"""
    def __init__(self):
        self.step = 0

    async def post_stream(self, url, *, headers, body, timeout):
        self.step += 1
        actions = [("query_order", {"order_id": "demo-001"}), ("approve_order", {"order_id": "demo-001", "expected_status": "pending", "idempotency_key": "demo-write-001"})]
        if self.step <= 2:
            name, args = actions[self.step - 1]
            delta = {"tool_calls": [{"index": 0, "id": f"call_{self.step}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": "业务操作已结束；请查看结构化结果与审计记录。"}, "stop"
        for payload in ({"choices": [{"index": 0, "delta": delta}]}, {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}):
            yield ("data: " + json.dumps(payload) + "\n\n").encode()
        yield b"data: [DONE]\n\n"


async def run(approve_demo=False):
    with tempfile.TemporaryDirectory(prefix="harness-business-") as directory:
        root = Path(directory)
        db = sqlite3.connect(root / "business.sqlite3")
        db.executescript("CREATE TABLE orders(id TEXT PRIMARY KEY,status TEXT); INSERT INTO orders VALUES('demo-001','pending'); CREATE TABLE writes(key TEXT PRIMARY KEY, fingerprint TEXT, result TEXT); CREATE TABLE audit(user_id TEXT,call_id TEXT,operation TEXT,result TEXT);")
        db.commit()
        harness = Harness(HarnessConfig(api_key="offline", home=root, cwd=root, permission_policy="request_approval"), transport=DemoTransport(), identity=IdentityContext(user_id="demo-user", roles=("order-reviewer",)))
        schema = {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}

        @harness.tool("query_order", "查询订单，不修改数据", schema, effect="read", idempotent=True)
        def query(args, ctx):
            row = db.execute("SELECT id,status FROM orders WHERE id=?", (args["order_id"],)).fetchone()
            return {"order_id": row[0], "status": row[1]} if row else {"error": "not-found"}

        write_schema = {"type": "object", "properties": {**schema["properties"], "expected_status": {"type": "string"}, "idempotency_key": {"type": "string"}}, "required": ["order_id", "expected_status", "idempotency_key"]}

        @harness.tool("approve_order", "审批后更新订单状态；参数变化必须重新审批", write_schema, effect="external_write", approval_required=True, idempotent=True)
        def write(args, ctx):
            identity = ctx.services["identity"]
            if "order-reviewer" not in identity.roles:
                raise PermissionError("宿主身份没有审批角色")
            with db:
                old = db.execute("SELECT fingerprint,result FROM writes WHERE key=?", (args["idempotency_key"],)).fetchone()
                if old:
                    if old[0] != json.dumps(args, sort_keys=True):
                        raise ValueError("业务幂等键已用于不同订单")
                    return json.loads(old[1])
                changed = db.execute("UPDATE orders SET status='approved' WHERE id=? AND status=?", (args["order_id"], args["expected_status"]))
                if changed.rowcount != 1:
                    raise ValueError("业务状态已变更，请重新查询并审批")
                result = {"order_id": args["order_id"], "status": "approved"}
                encoded = json.dumps(result)
                db.execute("INSERT INTO writes VALUES(?,?,?)", (args["idempotency_key"], json.dumps(args, sort_keys=True), encoded))
                db.execute("INSERT INTO audit VALUES(?,?,?,?)", (identity.user_id, ctx.call_id, "approve_order", encoded))
                return result

        async def answer(question):
            print(question["questions"][0]["question"])
            accepted = approve_demo or await asyncio.to_thread(input, "批准演示订单更新？输入 yes：") == "yes"
            await harness.questions.answer_and_notify(question["question_id"], [{"id": "permission", "selected": ["允许本次" if accepted else "拒绝"]}])
        harness.bus.on("user_question/ask", answer)
        session = harness.create_session()
        try:
            await harness.run_async(session, "查询 demo-001；获得明确授权后更新为 approved。")
            output = {"orders": db.execute("SELECT * FROM orders").fetchall(), "audit": db.execute("SELECT * FROM audit").fetchall(), "permission_events": [e.to_dict() for e in session.events_of_type("note")]}
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return output
        finally:
            await harness.close()
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-demo", action="store_true", help="明确批准临时 SQLite 演示订单的更新")
    asyncio.run(run(parser.parse_args().approve_demo))
