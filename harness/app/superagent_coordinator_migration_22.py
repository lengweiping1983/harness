"""工作台操作回执和来源媒体属于同一协调账本，历史不可覆盖。"""

MIGRATION_22 = """
CREATE TABLE coordinator_operator_requests (
 request_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), payload_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','completed')),
 receipt_json TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TRIGGER operator_request_initial BEFORE INSERT ON coordinator_operator_requests
WHEN NEW.state<>'pending' OR NEW.receipt_json IS NOT NULL
BEGIN SELECT RAISE(ABORT,'operator request must start pending'); END;
CREATE TRIGGER operator_request_immutable BEFORE UPDATE ON coordinator_operator_requests
WHEN NEW.request_id IS NOT OLD.request_id OR NEW.operation IS NOT OLD.operation
 OR NEW.payload_json IS NOT OLD.payload_json OR NEW.payload_hash IS NOT OLD.payload_hash
 OR NEW.created_at IS NOT OLD.created_at
 OR (OLD.state='completed' AND (NEW.state IS NOT OLD.state OR NEW.receipt_json IS NOT OLD.receipt_json))
 OR (NEW.state='completed' AND NEW.receipt_json IS NULL)
 OR (NEW.state='pending' AND NEW.receipt_json IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'operator request identity or receipt is immutable'); END;
CREATE TRIGGER operator_request_no_delete BEFORE DELETE ON coordinator_operator_requests
BEGIN SELECT RAISE(ABORT,'operator request history cannot be deleted'); END;
CREATE TABLE source_media (
 media_id TEXT PRIMARY KEY, trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id),
 source_instance_id TEXT NOT NULL REFERENCES source_instances(source_instance_id),
 resource_key TEXT NOT NULL, media_type TEXT NOT NULL,
 digest TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size>=0 AND byte_size<=33554432),
 content_type TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(trigger_id,resource_key)
);
CREATE TRIGGER source_media_scope BEFORE INSERT ON source_media
WHEN NOT EXISTS (SELECT 1 FROM triggers t WHERE t.trigger_id=NEW.trigger_id AND t.source_instance_id=NEW.source_instance_id)
BEGIN SELECT RAISE(ABORT,'media must belong to its original source trigger'); END;
CREATE TRIGGER source_media_no_update BEFORE UPDATE ON source_media
BEGIN SELECT RAISE(ABORT,'source media is immutable'); END;
CREATE TRIGGER source_media_no_delete BEFORE DELETE ON source_media
BEGIN SELECT RAISE(ABORT,'source media cannot be deleted implicitly'); END;
"""
