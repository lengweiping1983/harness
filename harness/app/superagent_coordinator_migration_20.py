"""回复传输绑定独立于不可变 Channel 地址，修订严格关联当前来源。"""

MIGRATION_20 = """
CREATE TABLE channel_reply_bindings (
 channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id) ON DELETE RESTRICT,
 source_revision INTEGER NOT NULL CHECK(source_revision>=1),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 updated_at TEXT NOT NULL
);
CREATE TRIGGER channel_reply_bindings_insert_guard
BEFORE INSERT ON channel_reply_bindings
WHEN NEW.aggregate_revision<>1 OR NOT EXISTS (
 SELECT 1 FROM channels c JOIN source_instances s ON s.source_instance_id=c.source_instance_id
 WHERE c.channel_id=NEW.channel_id AND s.config_revision=NEW.source_revision
 AND (NEW.enabled=0 OR s.enabled=1)
)
BEGIN SELECT RAISE(ABORT,'channel reply binding requires the current source revision'); END;
CREATE TRIGGER channel_reply_bindings_update_guard
BEFORE UPDATE ON channel_reply_bindings
WHEN NEW.channel_id IS NOT OLD.channel_id OR NEW.aggregate_revision<>OLD.aggregate_revision+1
 OR NOT EXISTS (
 SELECT 1 FROM channels c JOIN source_instances s ON s.source_instance_id=c.source_instance_id
 WHERE c.channel_id=NEW.channel_id AND s.config_revision=NEW.source_revision
 AND (NEW.enabled=0 OR s.enabled=1)
)
BEGIN SELECT RAISE(ABORT,'channel reply binding revision is invalid'); END;
CREATE TRIGGER channel_reply_bindings_immutable_delete
BEFORE DELETE ON channel_reply_bindings
BEGIN SELECT RAISE(ABORT,'channel reply binding history cannot be removed'); END;
"""
