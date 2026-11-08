"""原生协调模型调用和本机指令同属协调账本，不另建业务队列。"""

MIGRATION_21 = """
CREATE TABLE outbox_reply_targets (
 message_id TEXT PRIMARY KEY REFERENCES outbox_messages(message_id),
 trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id)
);
CREATE TRIGGER outbox_reply_target_insert BEFORE INSERT ON outbox_reply_targets
WHEN NOT EXISTS (
 SELECT 1 FROM outbox_messages m JOIN ticket_inputs i ON i.ticket_id=m.ticket_id
 WHERE m.message_id=NEW.message_id AND i.trigger_id=NEW.trigger_id AND i.context_only=0 AND i.state<>'handed_back'
)
BEGIN SELECT RAISE(ABORT,'outbox target must bind an authorized work input'); END;
CREATE TRIGGER outbox_reply_target_update BEFORE UPDATE ON outbox_reply_targets
BEGIN SELECT RAISE(ABORT,'outbox reply target is immutable'); END;
CREATE TRIGGER outbox_reply_target_delete BEFORE DELETE ON outbox_reply_targets
BEGIN SELECT RAISE(ABORT,'outbox reply targets cannot be deleted'); END;
CREATE TABLE coordinator_model_calls (
 call_key TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('assess','plan')),
 session_id TEXT NOT NULL, prompt TEXT NOT NULL, prompt_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','submitting','accepted','completed','unknown')),
 run_id TEXT, output_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE coordinator_local_events (
 source_id TEXT NOT NULL, event_id TEXT NOT NULL, payload_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL, occurred_at TEXT NOT NULL, PRIMARY KEY(source_id,event_id)
);
CREATE TRIGGER coordinator_local_event_update BEFORE UPDATE ON coordinator_local_events
BEGIN SELECT RAISE(ABORT,'local source event identity is immutable'); END;
CREATE TRIGGER coordinator_local_event_delete BEFORE DELETE ON coordinator_local_events
BEGIN SELECT RAISE(ABORT,'local source events cannot be deleted'); END;
CREATE TABLE coordinator_requests (
 request_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','accepted','rejected')),
 receipt_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TRIGGER coordinator_model_initial BEFORE INSERT ON coordinator_model_calls
WHEN NEW.state<>'prepared' OR NEW.run_id IS NOT NULL OR NEW.output_json IS NOT NULL
BEGIN SELECT RAISE(ABORT,'coordinator model calls must start prepared'); END;
CREATE TRIGGER coordinator_request_initial BEFORE INSERT ON coordinator_requests
WHEN NEW.state<>'pending' OR NEW.receipt_json IS NOT NULL
BEGIN SELECT RAISE(ABORT,'coordinator requests must start pending'); END;
CREATE TRIGGER coordinator_model_delete BEFORE DELETE ON coordinator_model_calls
BEGIN SELECT RAISE(ABORT,'coordinator model calls cannot be deleted'); END;
CREATE TRIGGER coordinator_request_delete BEFORE DELETE ON coordinator_requests
BEGIN SELECT RAISE(ABORT,'coordinator requests cannot be deleted'); END;
CREATE TRIGGER coordinator_model_identity BEFORE UPDATE ON coordinator_model_calls
WHEN NEW.call_key IS NOT OLD.call_key OR NEW.role IS NOT OLD.role
 OR NEW.session_id IS NOT OLD.session_id OR NEW.prompt IS NOT OLD.prompt
 OR NEW.prompt_hash IS NOT OLD.prompt_hash
 OR (OLD.run_id IS NOT NULL AND NEW.run_id IS NOT OLD.run_id)
 OR (OLD.state='completed' AND (NEW.state<>'completed' OR NEW.output_json IS NOT OLD.output_json))
 OR (OLD.state<>'prepared' AND NEW.state='prepared')
BEGIN SELECT RAISE(ABORT,'coordinator model call identity is immutable'); END;
CREATE TRIGGER coordinator_request_identity BEFORE UPDATE ON coordinator_requests
WHEN NEW.request_id IS NOT OLD.request_id OR NEW.payload_json IS NOT OLD.payload_json
 OR NEW.payload_hash IS NOT OLD.payload_hash
 OR (OLD.state<>'pending' AND (NEW.state IS NOT OLD.state OR NEW.receipt_json IS NOT OLD.receipt_json))
BEGIN SELECT RAISE(ABORT,'coordinator request identity is immutable'); END;
"""
