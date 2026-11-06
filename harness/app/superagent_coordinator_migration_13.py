"""为 V2 Worker 与 Attention 聚合补齐更新时间字段。"""

MIGRATION_13 = """
ALTER TABLE workers ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
UPDATE workers SET updated_at=created_at WHERE updated_at='';
ALTER TABLE attentions ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
UPDATE attentions SET updated_at=created_at WHERE updated_at='';
"""
