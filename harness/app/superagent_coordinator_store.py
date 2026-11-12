"""下一代 Superagent 的协调账本；不接管 Harness Runtime 的原生执行事实。"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

from ..core.errors import HarnessError
from harness.i18n import tr

from .superagent_coordinator_migration_12 import MIGRATION_12
from .superagent_coordinator_migration_13 import MIGRATION_13
from .superagent_coordinator_migration_14 import MIGRATION_14
from .superagent_coordinator_migration_15 import MIGRATION_15
from .superagent_coordinator_migration_16 import MIGRATION_16
from .superagent_coordinator_migration_17 import MIGRATION_17
from .superagent_coordinator_migration_18 import MIGRATION_18
from .superagent_coordinator_migration_19 import MIGRATION_19
from .superagent_coordinator_migration_20 import MIGRATION_20
from .superagent_coordinator_migration_21 import MIGRATION_21
from .superagent_coordinator_migration_22 import MIGRATION_22
from .superagent_coordinator_migration_23 import MIGRATION_23

_APPLICATION_ID = int.from_bytes(b"SUPA", "big")
_MAX_EFFECT_BATCH = 100

def _text(value: object, name: str) -> str:
    """验证稳定身份字段。"""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value

def _json(value: object) -> str:
    """生成稳定 JSON，并拒绝非有限数字和非法 Unicode。"""
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        result.encode("utf-8")
        return result
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise HarnessError(tr("storage.schema.invalid"), code="superagent/coordinator-invalid") from exc

def _time(value: str, name: str) -> str:
    """将带时区时间规范化为 UTC 毫秒精度。"""
    _text(value, name)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

_MIGRATION_1 = """
CREATE TABLE instances (
 instance_id TEXT PRIMARY KEY,
 singleton INTEGER NOT NULL UNIQUE DEFAULT 1 CHECK(singleton=1),
 identity_json TEXT NOT NULL,
 identity_hash TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE aggregate_revisions (
 aggregate_type TEXT NOT NULL CHECK(length(trim(aggregate_type))>0),
 aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id))>0),
 revision INTEGER NOT NULL CHECK(revision>=0),
 PRIMARY KEY(aggregate_type,aggregate_id)
);
CREATE TABLE audit_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE CHECK(length(trim(event_id))>0),
 aggregate_type TEXT NOT NULL CHECK(length(trim(aggregate_type))>0),
 aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id))>0),
 event_type TEXT NOT NULL CHECK(length(trim(event_type))>0),
 schema_version INTEGER NOT NULL CHECK(schema_version>=1),
 payload_json TEXT NOT NULL,
 correlation_id TEXT,
 causation_id TEXT,
 occurred_at TEXT NOT NULL,
 actor_json TEXT NOT NULL
);
CREATE INDEX audit_events_aggregate ON audit_events(aggregate_type,aggregate_id,seq);
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN
 SELECT RAISE(ABORT,'audit events are append-only');
END;
CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN
 SELECT RAISE(ABORT,'audit events are append-only');
END;
CREATE TABLE effects (
 effect_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL CHECK(length(trim(kind))>0),
 aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id))>0),
 idempotency_key TEXT NOT NULL CHECK(length(trim(idempotency_key))>0),
 payload_json TEXT NOT NULL,
 receipt_json TEXT,
 state TEXT NOT NULL CHECK(state IN ('pending','leased','retry_wait','completed','failed','unknown','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 due_at TEXT NOT NULL,
 lease_owner TEXT,
 lease_until TEXT,
 generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0),
 last_error TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(kind,idempotency_key),
 CHECK((state='leased' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL)
    OR (state<>'leased' AND lease_owner IS NULL AND lease_until IS NULL))
);
CREATE INDEX effects_due ON effects(state,due_at,kind);
"""

_MIGRATION_2 = """
CREATE TABLE tickets (
 ticket_id TEXT PRIMARY KEY CHECK(length(trim(ticket_id))>0),
 mandate_id TEXT NOT NULL UNIQUE CHECK(length(trim(mandate_id))>0),
 origin_trigger_id TEXT NOT NULL CHECK(length(trim(origin_trigger_id))>0),
 title TEXT NOT NULL CHECK(length(trim(title))>0),
 state TEXT NOT NULL CHECK(state IN ('open','ended')),
 outcome TEXT CHECK(outcome IN ('completed','merged','closed','dropped','stopped','revoked','failed')),
 ended_by TEXT CHECK(ended_by IN ('human','executor','policy','system')),
 ended_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 CHECK((state='open' AND outcome IS NULL AND ended_by IS NULL AND ended_at IS NULL)
    OR (state='ended' AND outcome IS NOT NULL AND ended_by IS NOT NULL AND ended_at IS NOT NULL))
);
CREATE TABLE missions (
 mission_id TEXT PRIMARY KEY CHECK(length(trim(mission_id))>0),
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 sequence INTEGER NOT NULL CHECK(sequence>=1),
 state TEXT NOT NULL CHECK(state IN ('planning','running','integrating','reviewing','cancelling','completed','blocked','unknown','rejected','suppressed','cancelled')),
 outcome TEXT,
 goal_ref TEXT,
 policy_version_at_start TEXT,
 source_revision_at_start INTEGER CHECK(source_revision_at_start IS NULL OR source_revision_at_start>=1),
 plan_revision INTEGER NOT NULL DEFAULT 0 CHECK(plan_revision>=0),
 repo TEXT NOT NULL CHECK(length(trim(repo))>0),
 base_commit TEXT NOT NULL CHECK(length(trim(base_commit))>0),
 round_no INTEGER NOT NULL DEFAULT 0 CHECK(round_no>=0),
 budgets_json TEXT NOT NULL CHECK(json_valid(budgets_json)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 UNIQUE(ticket_id,sequence)
);
CREATE INDEX missions_ticket_state ON missions(ticket_id,state,sequence);
CREATE TABLE mission_plans (
 mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
 plan_revision INTEGER NOT NULL CHECK(plan_revision>=1),
 plan_json TEXT NOT NULL CHECK(json_valid(plan_json)),
 plan_hash TEXT NOT NULL CHECK(length(plan_hash)=64),
 acceptance_json TEXT NOT NULL CHECK(json_valid(acceptance_json)),
 acceptance_hash TEXT NOT NULL CHECK(length(acceptance_hash)=64),
 created_at TEXT NOT NULL,

 created_by_kind TEXT NOT NULL CHECK(created_by_kind IN ('host','human','assignment')),
 created_by_ref TEXT NOT NULL CHECK(length(trim(created_by_ref))>0),
 PRIMARY KEY(mission_id,plan_revision)
);
CREATE TABLE jobs (
 job_id TEXT PRIMARY KEY CHECK(length(trim(job_id))>0),
 mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
 plan_revision INTEGER NOT NULL CHECK(plan_revision>=0),
 node_id TEXT NOT NULL CHECK(length(trim(node_id))>0),
 role TEXT NOT NULL CHECK(role IN ('plan','work','integrate','review')),
 profile_id TEXT,
 instruction_ref TEXT,
 instruction_hash TEXT,
 state TEXT NOT NULL CHECK(state IN ('pending','ready','running','checkpointed','done','blocked','unknown','cancelled')),
 spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
 result_hash TEXT,
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 UNIQUE(mission_id,plan_revision,node_id),
 UNIQUE(job_id,mission_id,plan_revision)
);
CREATE INDEX jobs_mission_state ON jobs(mission_id,state,plan_revision);
CREATE TABLE job_dependencies (
 job_id TEXT NOT NULL,
 depends_on_job_id TEXT NOT NULL,
 mission_id TEXT NOT NULL,
 plan_revision INTEGER NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 requires_delivery INTEGER NOT NULL CHECK(requires_delivery IN (0,1)),
 PRIMARY KEY(job_id,depends_on_job_id),
 UNIQUE(job_id,ordinal),
 CHECK(job_id<>depends_on_job_id),
 FOREIGN KEY(job_id,mission_id,plan_revision) REFERENCES jobs(job_id,mission_id,plan_revision) ON DELETE RESTRICT,
 FOREIGN KEY(depends_on_job_id,mission_id,plan_revision) REFERENCES jobs(job_id,mission_id,plan_revision) ON DELETE RESTRICT
);
CREATE TABLE assignments (
 assignment_id TEXT PRIMARY KEY CHECK(length(trim(assignment_id))>0),
 job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
 attempt_no INTEGER NOT NULL CHECK(attempt_no>=1),
 worker_id TEXT,
 session_id TEXT,
 workspace_id TEXT,
 run_id TEXT,
 submission_key TEXT NOT NULL UNIQUE CHECK(length(trim(submission_key))>0),

 state TEXT NOT NULL CHECK(state IN ('pending','submitting','accepted','running','capturing','stopping','succeeded','failed','interrupted','rejected','unknown','cancelled')),
 report_json TEXT NOT NULL CHECK(json_valid(report_json)),
 started_at TEXT,
 ended_at TEXT,
 error_ref TEXT,
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 UNIQUE(job_id,attempt_no),
 CHECK(ended_at IS NULL OR started_at IS NOT NULL)
);
CREATE INDEX assignments_job_state ON assignments(job_id,state,attempt_no);
CREATE TRIGGER assignments_run_id_immutable
BEFORE UPDATE OF run_id ON assignments
WHEN OLD.run_id IS NOT NULL AND NEW.run_id IS NOT OLD.run_id
BEGIN
 SELECT RAISE(ABORT,'assignment run identity is immutable');
END;
CREATE TRIGGER assignments_identity_immutable
BEFORE UPDATE OF assignment_id,job_id,attempt_no,submission_key ON assignments
BEGIN
 SELECT RAISE(ABORT,'assignment identity is immutable');
END;
"""

_MIGRATION_3 = """
CREATE TABLE outbox_messages (
 message_id TEXT PRIMARY KEY CHECK(length(trim(message_id))>0),
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 channel_id TEXT NOT NULL CHECK(length(trim(channel_id))>0),
 kind TEXT NOT NULL CHECK(length(trim(kind))>0),
 body_ref TEXT NOT NULL CHECK(length(trim(body_ref))>0),
 body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
 idempotency_key TEXT NOT NULL UNIQUE CHECK(length(trim(idempotency_key))>0),
 state TEXT NOT NULL CHECK(state IN ('pending','sending','retry_wait','sent','dropped','withdrawn','unknown')),
 provider_id TEXT,
 provider_key TEXT,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 sent_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 UNIQUE(provider_id,provider_key),
 CHECK((state='sent' AND provider_id IS NOT NULL AND provider_key IS NOT NULL AND sent_at IS NOT NULL)
    OR (state<>'sent' AND sent_at IS NULL))
);
CREATE INDEX outbox_due ON outbox_messages(state,created_at);
CREATE TRIGGER outbox_messages_identity_immutable
BEFORE UPDATE OF message_id,ticket_id,channel_id,kind,body_ref,body_hash,idempotency_key ON outbox_messages
BEGIN
 SELECT RAISE(ABORT,'outbox message identity is immutable');
END;
CREATE TRIGGER outbox_provider_identity_immutable
BEFORE UPDATE OF provider_id,provider_key ON outbox_messages
WHEN (OLD.provider_id IS NOT NULL AND NEW.provider_id IS NOT OLD.provider_id)
  OR (OLD.provider_key IS NOT NULL AND NEW.provider_key IS NOT OLD.provider_key)
BEGIN
 SELECT RAISE(ABORT,'outbox provider identity is immutable');
END;
"""

_MIGRATION_4 = """
ALTER TABLE mission_plans ADD COLUMN sealed INTEGER NOT NULL DEFAULT 1 CHECK(sealed IN (0,1));
CREATE TRIGGER mission_plans_sequential
BEFORE INSERT ON mission_plans
WHEN NEW.plan_revision <> COALESCE(
    (SELECT MAX(plan_revision) + 1 FROM mission_plans WHERE mission_id=NEW.mission_id), 1
)
BEGIN
 SELECT RAISE(ABORT,'plan revisions must be sequential');
END;
CREATE TRIGGER missions_plan_pointer_guard
BEFORE UPDATE OF plan_revision ON missions
WHEN NEW.plan_revision<>OLD.plan_revision
 AND (
  NEW.plan_revision<>OLD.plan_revision+1
  OR NOT EXISTS (
   SELECT 1 FROM mission_plans
   WHERE mission_id=NEW.mission_id AND plan_revision=NEW.plan_revision AND sealed=0
  )
 )
BEGIN
 SELECT RAISE(ABORT,'mission plan pointer must advance to the next unsealed revision');
END;
CREATE TRIGGER mission_plans_immutable_update
BEFORE UPDATE ON mission_plans
WHEN NOT (
 OLD.sealed=0 AND NEW.sealed=1
 AND EXISTS (
  SELECT 1 FROM missions
  WHERE mission_id=OLD.mission_id AND plan_revision=OLD.plan_revision
 )
 AND EXISTS (
  SELECT 1 FROM jobs
  WHERE mission_id=OLD.mission_id AND plan_revision=OLD.plan_revision
 )
 AND NEW.mission_id IS OLD.mission_id
 AND NEW.plan_revision IS OLD.plan_revision
 AND NEW.plan_json IS OLD.plan_json
 AND NEW.plan_hash IS OLD.plan_hash
 AND NEW.acceptance_json IS OLD.acceptance_json
 AND NEW.acceptance_hash IS OLD.acceptance_hash
 AND NEW.created_at IS OLD.created_at
 AND NEW.created_by_kind IS OLD.created_by_kind
 AND NEW.created_by_ref IS OLD.created_by_ref
)
BEGIN

 SELECT RAISE(ABORT,'plan revisions are immutable except for initial sealing');
END;
CREATE TRIGGER mission_plans_immutable_delete
BEFORE DELETE ON mission_plans BEGIN
 SELECT RAISE(ABORT,'plan revisions are immutable');
END;
CREATE TRIGGER jobs_plan_zero_identity
BEFORE INSERT ON jobs
WHEN NEW.plan_revision=0 AND (NEW.role<>'plan' OR NEW.node_id<>'__system__plan')
BEGIN
 SELECT RAISE(ABORT,'plan revision zero is reserved for the host planner');
END;
CREATE TRIGGER jobs_plan_revision_exists
BEFORE INSERT ON jobs
WHEN NEW.plan_revision>0 AND NOT EXISTS (
 SELECT 1 FROM mission_plans
 WHERE mission_id=NEW.mission_id AND plan_revision=NEW.plan_revision AND sealed=0
)
BEGIN
 SELECT RAISE(ABORT,'job plan revision is missing or already sealed');
END;

CREATE TRIGGER jobs_plan_identity_immutable
BEFORE UPDATE OF job_id,mission_id,plan_revision,node_id ON jobs BEGIN
 SELECT RAISE(ABORT,'job identity is immutable');
END;
CREATE TRIGGER jobs_plan_spec_immutable
BEFORE UPDATE OF role,profile_id,instruction_ref,instruction_hash,spec_hash ON jobs BEGIN
 SELECT RAISE(ABORT,'job planned specification is immutable');
END;
CREATE TRIGGER job_dependencies_require_unsealed_plan
BEFORE INSERT ON job_dependencies
WHEN NOT EXISTS (
 SELECT 1 FROM mission_plans
 WHERE mission_id=NEW.mission_id AND plan_revision=NEW.plan_revision AND sealed=0
)
BEGIN
 SELECT RAISE(ABORT,'plan revision is missing or already sealed');
END;
CREATE TRIGGER job_dependencies_no_cycle
BEFORE INSERT ON job_dependencies
BEGIN
 SELECT CASE WHEN EXISTS (
  WITH RECURSIVE reachable(job_id) AS (
   SELECT depends_on_job_id FROM job_dependencies
   WHERE job_id=NEW.depends_on_job_id
     AND mission_id=NEW.mission_id AND plan_revision=NEW.plan_revision
   UNION
   SELECT dependency.depends_on_job_id

   FROM job_dependencies AS dependency

   JOIN reachable ON dependency.job_id=reachable.job_id
   WHERE dependency.mission_id=NEW.mission_id
     AND dependency.plan_revision=NEW.plan_revision
  )
  SELECT 1 FROM reachable WHERE job_id=NEW.job_id
 ) THEN RAISE(ABORT,'job dependency cycle') END;
END;
CREATE TRIGGER job_dependencies_immutable_update
BEFORE UPDATE ON job_dependencies BEGIN
 SELECT RAISE(ABORT,'plan dependencies are immutable');
END;
CREATE TRIGGER job_dependencies_immutable_delete
BEFORE DELETE ON job_dependencies BEGIN
 SELECT RAISE(ABORT,'plan dependencies are immutable');
END;
"""

_MIGRATION_5 = """
CREATE TABLE outbox_effect_links (
 message_id TEXT PRIMARY KEY REFERENCES outbox_messages(message_id) ON DELETE RESTRICT,
 effect_id TEXT NOT NULL UNIQUE REFERENCES effects(effect_id) ON DELETE RESTRICT,
 created_at TEXT NOT NULL
);
CREATE INDEX outbox_effect_links_effect ON outbox_effect_links(effect_id);
CREATE TRIGGER outbox_effect_links_immutable_update
BEFORE UPDATE ON outbox_effect_links BEGIN
 SELECT RAISE(ABORT,'outbox effect links are immutable');
END;
CREATE TRIGGER outbox_effect_links_immutable_delete
BEFORE DELETE ON outbox_effect_links BEGIN
 SELECT RAISE(ABORT,'outbox effect links are immutable');
END;
"""

_MIGRATION_6 = """
CREATE TRIGGER effects_identity_immutable_update
BEFORE UPDATE OF effect_id,kind,aggregate_id,idempotency_key,payload_json,created_at ON effects BEGIN
 SELECT RAISE(ABORT,'effect identity and payload are immutable');
END;
CREATE TRIGGER effects_immutable_delete
BEFORE DELETE ON effects BEGIN
 SELECT RAISE(ABORT,'durable effects cannot be deleted');
END;
"""

_MIGRATION_7 = """
CREATE UNIQUE INDEX missions_one_active_per_ticket
ON missions(ticket_id)
WHERE state IN ('planning','running','integrating','reviewing','cancelling','unknown');
CREATE UNIQUE INDEX assignments_one_active_per_job
ON assignments(job_id)
WHERE state IN ('pending','submitting','accepted','running','capturing','stopping','unknown');
"""

_MIGRATION_8 = """
ALTER TABLE effects ADD COLUMN correlation_id TEXT;
ALTER TABLE effects ADD COLUMN last_event_id TEXT
    REFERENCES audit_events(event_id) ON DELETE RESTRICT;
CREATE INDEX audit_events_correlation ON audit_events(correlation_id,seq);
CREATE INDEX effects_correlation ON effects(correlation_id);
CREATE TRIGGER audit_events_causation_exists
BEFORE INSERT ON audit_events
WHEN NEW.causation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM audit_events
    WHERE event_id=NEW.causation_id AND correlation_id IS NEW.correlation_id
)
BEGIN
 SELECT RAISE(ABORT,'audit causation must reference an event in the same correlation');
END;
CREATE TRIGGER effects_correlation_immutable_update
BEFORE UPDATE OF correlation_id ON effects
WHEN NEW.correlation_id IS NOT OLD.correlation_id
BEGIN
 SELECT RAISE(ABORT,'effect correlation identity is immutable');
END;
CREATE TRIGGER effects_last_event_valid_update
BEFORE UPDATE OF last_event_id ON effects
WHEN NEW.last_event_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM audit_events
    WHERE event_id=NEW.last_event_id AND correlation_id IS NEW.correlation_id
)
BEGIN
 SELECT RAISE(ABORT,'effect last event must belong to its correlation');
END;
"""


_MIGRATION_9 = """
CREATE TABLE source_instances (
 source_instance_id TEXT PRIMARY KEY CHECK(length(trim(source_instance_id))>0),
 kind TEXT NOT NULL CHECK(length(trim(kind))>0),
 kind_api_version INTEGER NOT NULL CHECK(kind_api_version>=1),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 config_json TEXT NOT NULL CHECK(json_valid(config_json)),
 config_revision INTEGER NOT NULL CHECK(config_revision>=1),
 identity_json TEXT NOT NULL CHECK(json_valid(identity_json)),
 identity_hash TEXT NOT NULL CHECK(length(identity_hash)=64),
 legacy_source_id TEXT CHECK(legacy_source_id IS NULL OR length(trim(legacy_source_id))>0),
 health_json TEXT NOT NULL CHECK(json_valid(health_json)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(kind,identity_hash)
);
CREATE TRIGGER source_instances_identity_immutable
BEFORE UPDATE OF source_instance_id,kind,kind_api_version,identity_json,identity_hash ON source_instances BEGIN
 SELECT RAISE(ABORT,'source instance identity is immutable');
END;
CREATE TRIGGER source_instances_config_revision_guard
BEFORE UPDATE OF config_json,enabled,config_revision ON source_instances
WHEN (NEW.config_json IS NOT OLD.config_json OR NEW.enabled IS NOT OLD.enabled)
 AND NEW.config_revision<>OLD.config_revision+1 BEGIN
 SELECT RAISE(ABORT,'source instance configuration requires the next revision');
END;
CREATE TABLE source_cursors (
 source_instance_id TEXT NOT NULL REFERENCES source_instances(source_instance_id) ON DELETE RESTRICT,
 stream_key TEXT NOT NULL CHECK(length(trim(stream_key))>0),
 cursor_json TEXT NOT NULL CHECK(json_valid(cursor_json)),
 cursor_revision INTEGER NOT NULL CHECK(cursor_revision>=1),
 updated_at TEXT NOT NULL,
 PRIMARY KEY(source_instance_id,stream_key)
);
CREATE TRIGGER source_cursors_revision_guard
BEFORE UPDATE OF cursor_json,cursor_revision ON source_cursors
WHEN NEW.cursor_revision<>OLD.cursor_revision+1 BEGIN
 SELECT RAISE(ABORT,'source cursor update requires the next revision');
END;
CREATE TABLE threads (
 thread_id TEXT PRIMARY KEY CHECK(length(trim(thread_id))>0),
 source_instance_id TEXT NOT NULL REFERENCES source_instances(source_instance_id) ON DELETE RESTRICT,
 external_thread_id TEXT NOT NULL CHECK(length(trim(external_thread_id))>0),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 source_state TEXT NOT NULL CHECK(source_state IN ('open','closed','unknown','unsupported')),
 source_outcome TEXT CHECK(source_outcome IN ('merged','closed')),
 provider_revision TEXT,
 source_observed_at TEXT,
 last_event_at TEXT,
 aggregate_revision INTEGER NOT NULL DEFAULT 1 CHECK(aggregate_revision>=1),
 lifecycle_revision INTEGER NOT NULL DEFAULT 1 CHECK(lifecycle_revision>=1),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(source_instance_id,external_thread_id),
 UNIQUE(thread_id,source_instance_id),
 CHECK((source_state='closed' AND source_outcome IS NOT NULL)
    OR (source_state<>'closed' AND source_outcome IS NULL))
);
CREATE TRIGGER threads_identity_immutable
BEFORE UPDATE OF thread_id,source_instance_id,external_thread_id,created_at ON threads BEGIN
 SELECT RAISE(ABORT,'thread identity is immutable');
END;
CREATE TABLE triggers (
 trigger_id TEXT PRIMARY KEY CHECK(length(trim(trigger_id))>0),
 source_instance_id TEXT NOT NULL REFERENCES source_instances(source_instance_id) ON DELETE RESTRICT,
 external_event_id TEXT NOT NULL CHECK(length(trim(external_event_id))>0),
 event_kind TEXT NOT NULL CHECK(length(trim(event_kind))>0),
 actor_json TEXT NOT NULL CHECK(json_valid(actor_json)),
 occurred_at TEXT NOT NULL,
 received_at TEXT NOT NULL,
 addressed TEXT NOT NULL CHECK(addressed IN ('mention','direct','none','unknown')),
 primary_thread_id TEXT NOT NULL,
 body_json TEXT NOT NULL CHECK(json_valid(body_json)),
 body_ref TEXT NOT NULL CHECK(length(trim(body_ref))>0),
 body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 meta_ref TEXT NOT NULL CHECK(length(trim(meta_ref))>0),
 meta_hash TEXT NOT NULL CHECK(length(meta_hash)=64),
 source_revision INTEGER NOT NULL CHECK(source_revision>=1),
 adapter_version TEXT NOT NULL CHECK(length(trim(adapter_version))>0),
 verification_state TEXT NOT NULL CHECK(verification_state IN ('verified','local_attested')),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 UNIQUE(source_instance_id,external_event_id),
 UNIQUE(trigger_id,source_instance_id),
 FOREIGN KEY(primary_thread_id,source_instance_id)
   REFERENCES threads(thread_id,source_instance_id) ON DELETE RESTRICT
);
CREATE INDEX triggers_source_received ON triggers(source_instance_id,received_at,trigger_id);
CREATE INDEX triggers_primary_thread ON triggers(primary_thread_id,occurred_at,trigger_id);
CREATE TRIGGER triggers_immutable_update
BEFORE UPDATE ON triggers BEGIN
 SELECT RAISE(ABORT,'source triggers are immutable');
END;
CREATE TRIGGER triggers_immutable_delete
BEFORE DELETE ON triggers BEGIN
 SELECT RAISE(ABORT,'source triggers are immutable');
END;
CREATE TABLE trigger_threads (
 trigger_id TEXT NOT NULL,
 source_instance_id TEXT NOT NULL,
 thread_id TEXT NOT NULL,
 relation TEXT NOT NULL CHECK(relation IN ('primary','related')),
 PRIMARY KEY(trigger_id,thread_id),
 FOREIGN KEY(trigger_id,source_instance_id)
   REFERENCES triggers(trigger_id,source_instance_id) ON DELETE RESTRICT,
 FOREIGN KEY(thread_id,source_instance_id)
   REFERENCES threads(thread_id,source_instance_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX trigger_threads_one_primary ON trigger_threads(trigger_id) WHERE relation='primary';
CREATE INDEX trigger_threads_thread ON trigger_threads(thread_id,trigger_id);
CREATE TABLE channels (
 channel_id TEXT PRIMARY KEY CHECK(length(trim(channel_id))>0),
 source_instance_id TEXT NOT NULL,
 thread_id TEXT NOT NULL,
 address_json TEXT NOT NULL CHECK(json_valid(address_json)),
 address_hash TEXT NOT NULL CHECK(length(address_hash)=64),
 capabilities_json TEXT NOT NULL CHECK(json_valid(capabilities_json)),
 created_at TEXT NOT NULL,
 UNIQUE(source_instance_id,thread_id,address_hash),
 FOREIGN KEY(thread_id,source_instance_id)
   REFERENCES threads(thread_id,source_instance_id) ON DELETE RESTRICT
);
CREATE TRIGGER channels_identity_immutable_update
BEFORE UPDATE ON channels BEGIN
 SELECT RAISE(ABORT,'channel addresses are immutable');
END;
CREATE TRIGGER channels_identity_immutable_delete
BEFORE DELETE ON channels BEGIN
 SELECT RAISE(ABORT,'channel addresses are immutable');
END;
CREATE TABLE trigger_dispositions (
 trigger_id TEXT PRIMARY KEY REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 state TEXT NOT NULL CHECK(state IN ('evaluation.queued','evaluation.started','decision.recorded','ticket.fanout','ticket.context_only','filtered')),
 evaluation_id TEXT,
 decision_id TEXT,
 fanout_json TEXT NOT NULL CHECK(json_valid(fanout_json)),
 context_json TEXT NOT NULL CHECK(json_valid(context_json)),
 updated_at TEXT NOT NULL
);
CREATE TABLE trigger_disposition_events (
 event_id TEXT PRIMARY KEY CHECK(length(trim(event_id))>0),
 trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 event_type TEXT NOT NULL CHECK(length(trim(event_type))>0),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 occurred_at TEXT NOT NULL,
 audit_event_id TEXT NOT NULL REFERENCES audit_events(event_id) ON DELETE RESTRICT
);
CREATE INDEX trigger_disposition_events_trigger ON trigger_disposition_events(trigger_id,occurred_at,event_id);
CREATE TRIGGER trigger_disposition_events_immutable_update
BEFORE UPDATE ON trigger_disposition_events BEGIN
 SELECT RAISE(ABORT,'trigger disposition events are immutable');
END;
CREATE TRIGGER trigger_disposition_events_immutable_delete
BEFORE DELETE ON trigger_disposition_events BEGIN
 SELECT RAISE(ABORT,'trigger disposition events are immutable');
END;
CREATE TABLE policy_versions (
 policy_version TEXT PRIMARY KEY CHECK(length(trim(policy_version))>0),
 revision INTEGER NOT NULL UNIQUE CHECK(revision>=1),
 mode TEXT NOT NULL CHECK(mode IN ('off','observe','auto')),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64),
 created_at TEXT NOT NULL,
 created_by_json TEXT NOT NULL CHECK(json_valid(created_by_json))
);
CREATE TRIGGER policy_versions_immutable_update
BEFORE UPDATE ON policy_versions BEGIN
 SELECT RAISE(ABORT,'policy revisions are immutable');
END;
CREATE TRIGGER policy_versions_immutable_delete
BEFORE DELETE ON policy_versions BEGIN
 SELECT RAISE(ABORT,'policy revisions are immutable');
END;
CREATE TABLE policy_rules (
 policy_version TEXT NOT NULL REFERENCES policy_versions(policy_version) ON DELETE RESTRICT,
 rule_id TEXT NOT NULL CHECK(length(trim(rule_id))>0),
 rule_json TEXT NOT NULL CHECK(json_valid(rule_json)),
 PRIMARY KEY(policy_version,rule_id)
);
CREATE TABLE source_policies (
 policy_version TEXT NOT NULL REFERENCES policy_versions(policy_version) ON DELETE RESTRICT,
 source_instance_id TEXT NOT NULL REFERENCES source_instances(source_instance_id) ON DELETE RESTRICT,
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 PRIMARY KEY(policy_version,source_instance_id)
);
CREATE TRIGGER policy_rules_immutable_update
BEFORE UPDATE ON policy_rules BEGIN
 SELECT RAISE(ABORT,'policy rule snapshots are immutable');
END;
CREATE TRIGGER policy_rules_immutable_delete
BEFORE DELETE ON policy_rules BEGIN
 SELECT RAISE(ABORT,'policy rule snapshots are immutable');
END;
CREATE TRIGGER source_policies_immutable_update
BEFORE UPDATE ON source_policies BEGIN
 SELECT RAISE(ABORT,'source policy snapshots are immutable');
END;
CREATE TRIGGER source_policies_immutable_delete
BEFORE DELETE ON source_policies BEGIN
 SELECT RAISE(ABORT,'source policy snapshots are immutable');
END;
CREATE TABLE policy_current (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 policy_version TEXT NOT NULL REFERENCES policy_versions(policy_version) ON DELETE RESTRICT,
 revision INTEGER NOT NULL CHECK(revision>=1)
);
CREATE TABLE evaluations (
 evaluation_id TEXT PRIMARY KEY CHECK(length(trim(evaluation_id))>0),
 thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE RESTRICT,
 policy_version TEXT NOT NULL REFERENCES policy_versions(policy_version) ON DELETE RESTRICT,
 state TEXT NOT NULL CHECK(state IN ('running','decided','unavailable','cancelled')),
 trigger_batch_hash TEXT NOT NULL CHECK(length(trigger_batch_hash)=64),
 primary_trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 strength TEXT NOT NULL CHECK(strength IN ('explicit','inferred')),
 actor_json TEXT NOT NULL CHECK(json_valid(actor_json)),
 mandate_basis_json TEXT NOT NULL CHECK(json_valid(mandate_basis_json)),
 thread_lifecycle_revision INTEGER NOT NULL CHECK(thread_lifecycle_revision>=1),
 started_at TEXT NOT NULL,
 deadline_at TEXT NOT NULL,
 completed_at TEXT,
 retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count>=0),
 outcome_json TEXT CHECK(outcome_json IS NULL OR json_valid(outcome_json)),
 FOREIGN KEY(thread_id) REFERENCES threads(thread_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX evaluations_one_running_per_thread
ON evaluations(thread_id) WHERE state='running';
CREATE TABLE evaluation_triggers (
 evaluation_id TEXT NOT NULL REFERENCES evaluations(evaluation_id) ON DELETE RESTRICT,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 gate_json TEXT NOT NULL CHECK(json_valid(gate_json)),
 PRIMARY KEY(evaluation_id,trigger_id),
 UNIQUE(evaluation_id,ordinal)
);
CREATE TRIGGER evaluation_triggers_immutable_update
BEFORE UPDATE ON evaluation_triggers BEGIN
 SELECT RAISE(ABORT,'evaluation trigger snapshots are immutable');
END;
CREATE TRIGGER evaluation_triggers_immutable_delete
BEFORE DELETE ON evaluation_triggers BEGIN
 SELECT RAISE(ABORT,'evaluation trigger snapshots are immutable');
END;
CREATE TABLE decisions (
 decision_id TEXT PRIMARY KEY CHECK(length(trim(decision_id))>0),
 evaluation_id TEXT NOT NULL UNIQUE REFERENCES evaluations(evaluation_id) ON DELETE RESTRICT,
 thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE RESTRICT,
 policy_version TEXT NOT NULL REFERENCES policy_versions(policy_version) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('accept','reopen','stop','ignore','unavailable')),
 reason_json TEXT NOT NULL CHECK(json_valid(reason_json)),
 audit_json TEXT NOT NULL CHECK(json_valid(audit_json)),
 ticket_id TEXT REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 created_at TEXT NOT NULL,
 CHECK((kind IN ('accept','reopen','stop') AND ticket_id IS NOT NULL)
    OR (kind IN ('ignore','unavailable') AND ticket_id IS NULL))
);
CREATE TRIGGER decisions_immutable_update
BEFORE UPDATE ON decisions BEGIN
 SELECT RAISE(ABORT,'decisions are immutable');
END;
CREATE TRIGGER decisions_immutable_delete
BEFORE DELETE ON decisions BEGIN
 SELECT RAISE(ABORT,'decisions are immutable');
END;
CREATE TABLE mandates (
 mandate_id TEXT PRIMARY KEY CHECK(length(trim(mandate_id))>0),
 decision_id TEXT NOT NULL UNIQUE REFERENCES decisions(decision_id) ON DELETE RESTRICT,
 ticket_id TEXT NOT NULL UNIQUE REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 strength TEXT NOT NULL CHECK(strength IN ('explicit','inferred')),
 policy_version TEXT NOT NULL REFERENCES policy_versions(policy_version) ON DELETE RESTRICT,
 trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 source_instance_id TEXT NOT NULL REFERENCES source_instances(source_instance_id) ON DELETE RESTRICT,
 actor_json TEXT NOT NULL CHECK(json_valid(actor_json)),
 basis_json TEXT NOT NULL CHECK(json_valid(basis_json)),
 scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
 granted_at TEXT NOT NULL,
 expires_at TEXT
);
CREATE TRIGGER mandates_immutable_update
BEFORE UPDATE ON mandates BEGIN
 SELECT RAISE(ABORT,'mandates are immutable');
END;
CREATE TRIGGER mandates_immutable_delete
BEFORE DELETE ON mandates BEGIN
 SELECT RAISE(ABORT,'mandates are immutable');
END;
CREATE TABLE mandate_events (
 event_id TEXT PRIMARY KEY CHECK(length(trim(event_id))>0),
 mandate_id TEXT NOT NULL REFERENCES mandates(mandate_id) ON DELETE RESTRICT,
 event_type TEXT NOT NULL CHECK(event_type IN ('affected','revalidated','revoked','expired')),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 occurred_at TEXT NOT NULL,
 actor_json TEXT NOT NULL CHECK(json_valid(actor_json))
);
CREATE INDEX mandate_events_mandate ON mandate_events(mandate_id,occurred_at,event_id);
CREATE TRIGGER mandate_events_immutable_update
BEFORE UPDATE ON mandate_events BEGIN
 SELECT RAISE(ABORT,'mandate events are immutable');
END;
CREATE TRIGGER mandate_events_immutable_delete
BEFORE DELETE ON mandate_events BEGIN
 SELECT RAISE(ABORT,'mandate events are immutable');
END;
CREATE TABLE ticket_subscriptions (
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE RESTRICT,
 source_instance_id TEXT NOT NULL REFERENCES source_instances(source_instance_id) ON DELETE RESTRICT,
 mode TEXT NOT NULL CHECK(mode IN ('work','context')),
 owns_thread INTEGER NOT NULL CHECK(owns_thread IN (0,1)),
 state TEXT NOT NULL CHECK(state IN ('active','dropped')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(ticket_id,thread_id),
 FOREIGN KEY(thread_id,source_instance_id)
   REFERENCES threads(thread_id,source_instance_id) ON DELETE RESTRICT
);
CREATE TRIGGER ticket_subscriptions_identity_immutable
BEFORE UPDATE OF ticket_id,thread_id,source_instance_id,mode,owns_thread,created_at ON ticket_subscriptions BEGIN
 SELECT RAISE(ABORT,'ticket subscription identity is immutable');
END;
CREATE TABLE ticket_inputs (
 input_id TEXT PRIMARY KEY CHECK(length(trim(input_id))>0),
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('trigger')),
 context_only INTEGER NOT NULL CHECK(context_only IN (0,1)),
 state TEXT NOT NULL CHECK(state IN ('waiting','delivered','read','handed_back')),
 created_at TEXT NOT NULL,
 handback_reason TEXT,
 delivered_at TEXT,
 read_at TEXT,
 UNIQUE(ticket_id,trigger_id),
 CHECK((state='handed_back' AND handback_reason IS NOT NULL)
    OR (state<>'handed_back' AND handback_reason IS NULL))
);
CREATE INDEX ticket_inputs_trigger ON ticket_inputs(trigger_id,state,ticket_id);
CREATE TRIGGER ticket_inputs_identity_immutable
BEFORE UPDATE OF input_id,ticket_id,trigger_id,kind,context_only,created_at ON ticket_inputs BEGIN
 SELECT RAISE(ABORT,'ticket input identity is immutable');
END;
"""

_MIGRATION_10 = """
CREATE TRIGGER source_instances_legacy_mapping_immutable
BEFORE UPDATE OF legacy_source_id ON source_instances
WHEN OLD.legacy_source_id IS NOT NULL AND NEW.legacy_source_id IS NOT OLD.legacy_source_id
BEGIN
 SELECT RAISE(ABORT,'legacy source mapping is immutable once bound');
END;
"""

_MIGRATION_11 = """
CREATE UNIQUE INDEX source_instances_legacy_source_unique
ON source_instances(legacy_source_id)
WHERE legacy_source_id IS NOT NULL;
"""

MIGRATIONS = (
    (1, _MIGRATION_1), (2, _MIGRATION_2), (3, _MIGRATION_3),
    (4, _MIGRATION_4), (5, _MIGRATION_5), (6, _MIGRATION_6),
    (7, _MIGRATION_7), (8, _MIGRATION_8), (9, _MIGRATION_9),
    (10, _MIGRATION_10), (11, _MIGRATION_11), (12, MIGRATION_12),
    (13, MIGRATION_13), (14, MIGRATION_14),
    (15, MIGRATION_15), (16, MIGRATION_16), (17, MIGRATION_17), (18, MIGRATION_18),
    (19, MIGRATION_19),
    (20, MIGRATION_20),
    (21, MIGRATION_21),
    (22, MIGRATION_22), (23, MIGRATION_23),
)

class CoordinatorStore:
    """独立协调账本；每次操作使用短连接和明确事务。"""

    def __init__(self, path: str | Path, *, instance_id: str, identity: object):
        if str(path) == ":memory:":
            raise ValueError("CoordinatorStore requires a file-backed database")
        self.path = Path(path)
        self.instance_id = _text(instance_id, "instance_id")
        self.identity_json = _json(identity)
        self.identity_hash = _hash(self.identity_json)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare()
        self._migrate()
        with self.transaction(write=True) as db:
            row = db.execute("SELECT instance_id,identity_json,identity_hash FROM instances").fetchone()
            if row is None:
                populated = any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                                for table in ("aggregate_revisions", "audit_events", "effects",
                                              "tickets", "missions", "jobs", "assignments", "outbox_messages", "workers", "evidence", "attentions", "source_instances",
                                              "threads", "triggers", "policy_versions", "evaluations", "decisions", "mandates"))
                if populated:
                    raise HarnessError(tr("storage.schema.invalid"), code="superagent/instance-missing")
                db.execute(
                    "INSERT INTO instances(instance_id,identity_json,identity_hash,created_at) VALUES(?,?,?,?)",
                    (self.instance_id, self.identity_json, self.identity_hash, _now()),
                )
            else:
                if _hash(row["identity_json"]) != row["identity_hash"]:
                    raise HarnessError(tr("storage.schema.invalid"), code="superagent/instance-corrupt")
                if tuple(row) != (self.instance_id, self.identity_json, self.identity_hash):
                    raise HarnessError(tr("storage.schema.invalid"), code="superagent/instance-conflict")

    @classmethod
    def open_existing(cls, path, *, instance_id, identity, read_only=False, current_schema=True):
        """本机读取／命令只连接当前账本，不创建目录、迁移或修补原数据。"""
        store = cls.__new__(cls)
        store.path = Path(path)
        store.instance_id = instance_id
        store.identity_json = _json(identity)
        store.identity_hash = _hash(store.identity_json)
        store._read_only = read_only
        store._attached = True
        if not store.path.is_file() or store.path.is_symlink():
            raise HarnessError(tr("superagent.status-unavailable"), code="superagent/status-unavailable")
        with store.transaction() as db:
            if db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/coordinator-invalid")
            history = db.execute("SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall()
            expected = [(v, _hash(script)) for v, script in MIGRATIONS]
            actual = [(row[0], row[1]) for row in history]
            if (not actual or actual != expected[:len(actual)] or len(actual) > len(expected)
                    or current_schema and actual != expected
                    or db.execute('PRAGMA user_version').fetchone()[0] != actual[-1][0]):
                raise HarnessError(tr("superagent.corrupt"), code="superagent/schema-version")
            row = db.execute("SELECT instance_id,identity_json,identity_hash FROM instances").fetchone()
            if row is None or tuple(row) != (instance_id, store.identity_json, store.identity_hash):
                raise HarnessError(tr("superagent.conflict"), code="superagent/instance-conflict")
        return store

    def _connect(self) -> sqlite3.Connection:
        if getattr(self, '_attached', False):
            mode = 'ro' if self._read_only else 'rw'
            db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=' + mode, uri=True, timeout=10, isolation_level=None)
        else:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.create_function(
            "superagent_sha256", 1,
            lambda value: _hash(value) if isinstance(value, str) else None,
            deterministic=True,
        )
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _prepare(self) -> None:
        """拒绝把非空的陌生 SQLite 文件当作新账本。"""
        with closing(self._connect()) as db:
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            tables = {r["name"] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if app_id not in (0, _APPLICATION_ID):
                raise HarnessError(tr("storage.schema.invalid"), code="superagent/database-identity")
            if "schema_migrations" not in tables and tables:
                raise HarnessError(tr("storage.schema.invalid"), code="superagent/schema-uninitialized")
            db.execute("PRAGMA journal_mode=WAL")

    def _migrate(self) -> None:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                tables = {r["name"] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )}
                actual = db.execute("PRAGMA user_version").fetchone()[0]
                if "schema_migrations" not in tables:
                    if tables or actual != 0:
                        raise HarnessError(tr("storage.schema.invalid"), code="superagent/schema-uninitialized")
                    db.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)")
                    rows = {}
                else:
                    rows = {r["version"]: r["checksum"] for r in db.execute(
                        "SELECT version,checksum FROM schema_migrations"
                    )}
                    if not rows and tables - {"schema_migrations"}:
                        raise HarnessError(tr("storage.schema.invalid"), code="superagent/schema-history-missing")

                highest = max(rows, default=0)
                if sorted(rows) != list(range(1, highest + 1)):
                    raise HarnessError(tr("storage.schema.invalid"), code="superagent/schema-gap")
                if actual != highest:
                    raise HarnessError(tr("storage.schema.invalid"), code="superagent/schema-version")
                known = {version: _hash(script) for version, script in MIGRATIONS}
                if set(rows) - set(known):
                    raise HarnessError(tr("storage.schema.invalid"), code="superagent/schema-future")
                for version, script in MIGRATIONS:
                    checksum = known[version]
                    if version in rows:

                        if rows[version] != checksum:

                            raise HarnessError(tr("storage.schema.invalid"), code="superagent/schema-checksum")
                        continue
                    pending = ""
                    for line in script.splitlines():
                        pending += line + "\n"
                        if sqlite3.complete_statement(pending):
                            if pending.strip():
                                db.execute(pending)
                            pending = ""
                    if pending.strip():
                        raise ValueError("incomplete migration SQL")
                    db.execute(
                        "INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                        (version, checksum, _now()),
                    )
                db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                db.execute(f"PRAGMA user_version={max(known, default=0)}")
                db.commit()
            except BaseException:
                if db.in_transaction:
                    db.rollback()

                raise

    def unit_of_work(self, *, actor: object, correlation_id: str | None = None) -> "CoordinatorUnitOfWork":
        """创建聚合写入、审计和 Effect 共用的工作单元。"""
        return CoordinatorUnitOfWork(self, actor=actor, correlation_id=correlation_id)

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """开启短事务；写事务使用 BEGIN IMMEDIATE。"""
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _require_transaction(db: sqlite3.Connection) -> None:
        if not db.in_transaction:
            raise RuntimeError("CoordinatorStore operation requires an active transaction")

    def revision(self, aggregate_type: str, aggregate_id: str) -> int | None:

        _text(aggregate_type, "aggregate_type")
        _text(aggregate_id, "aggregate_id")
        with self.transaction() as db:
            row = db.execute(
                "SELECT revision FROM aggregate_revisions WHERE aggregate_type=? AND aggregate_id=?",
                (aggregate_type, aggregate_id),
            ).fetchone()

        return None if row is None else row["revision"]

    @staticmethod
    def compare_and_swap(db: sqlite3.Connection, aggregate_type: str, aggregate_id: str,
                         expected_revision: int) -> bool:
        """在调用方事务内原子递增聚合版本。"""
        CoordinatorStore._require_transaction(db)
        _text(aggregate_type, "aggregate_type")
        _text(aggregate_id, "aggregate_id")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if expected_revision == 0:
            try:
                db.execute("INSERT INTO aggregate_revisions VALUES(?,?,1)", (aggregate_type, aggregate_id))
                return True
            except sqlite3.IntegrityError:
                return False
        return db.execute(
            "UPDATE aggregate_revisions SET revision=revision+1 WHERE aggregate_type=? AND aggregate_id=? AND revision=?",
            (aggregate_type, aggregate_id, expected_revision),
        ).rowcount == 1

    @staticmethod
    def append_audit(db: sqlite3.Connection, *, aggregate_type: str, aggregate_id: str,
                     event_type: str, payload: object, actor: object, schema_version: int = 1,
                     correlation_id: str | None = None, causation_id: str | None = None,
                     event_id: str | None = None) -> str:
        """在当前事务中追加不可变审计事件。"""
        CoordinatorStore._require_transaction(db)
        _text(aggregate_type, "aggregate_type")
        _text(aggregate_id, "aggregate_id")
        _text(event_type, "event_type")
        if type(schema_version) is not int or schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        identity = "audit_" + uuid4().hex if event_id is None else _text(event_id, "event_id")
        if correlation_id is not None:
            _text(correlation_id, "correlation_id")
        if causation_id is not None:
            _text(causation_id, "causation_id")
        db.execute(
            "INSERT INTO audit_events(event_id,aggregate_type,aggregate_id,event_type,schema_version,payload_json,"
            "correlation_id,causation_id,occurred_at,actor_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (identity, aggregate_type, aggregate_id, event_type, schema_version, _json(payload),
             correlation_id, causation_id, _now(), _json(actor)),
        )
        return identity

    @staticmethod
    def _append_effect_event(db: sqlite3.Connection, effect, *, event_type: str,
                             payload: object, actor: object,
                             causation_id: str | None = None) -> str:
        """追加 Effect 审计事件，并推进其因果链游标。"""
        CoordinatorStore._require_transaction(db)
        cause = effect["last_event_id"] if causation_id is None else _text(causation_id, "causation_id")
        event_id = CoordinatorStore.append_audit(
            db, aggregate_type="effect", aggregate_id=effect["effect_id"],
            event_type=event_type, payload=payload, actor=actor,
            correlation_id=effect["correlation_id"], causation_id=cause,
        )
        db.execute(
            "UPDATE effects SET last_event_id=? WHERE effect_id=?",
            (event_id, effect["effect_id"]),
        )
        return event_id

    @staticmethod
    def enqueue_effect(db: sqlite3.Connection, *, kind: str, aggregate_id: str,
                       idempotency_key: str, payload: object, due_at: str | None = None,
                       correlation_id: str | None = None, causation_id: str | None = None,
                       actor: object | None = None) -> str:
        """在当前事务中写入幂等副作用意图和对应审计事件。"""
        CoordinatorStore._require_transaction(db)
        _text(kind, "kind")
        _text(aggregate_id, "aggregate_id")
        _text(idempotency_key, "idempotency_key")
        encoded = _json(payload)
        due = _now() if due_at is None else _time(due_at, "due_at")
        correlation = "corr_" + uuid4().hex if correlation_id is None else _text(correlation_id, "correlation_id")
        cause = None if causation_id is None else _text(causation_id, "causation_id")
        old = db.execute(
            "SELECT effect_id,payload_json,aggregate_id FROM effects WHERE kind=? AND idempotency_key=?",
            (kind, idempotency_key),
        ).fetchone()
        if old is not None:
            if old["payload_json"] != encoded or old["aggregate_id"] != aggregate_id:
                raise HarnessError(tr("storage.schema.invalid"), code="superagent/effect-conflict")
            return old["effect_id"]
        effect_id = "effect_" + uuid4().hex
        db.execute(
            "INSERT INTO effects(effect_id,kind,aggregate_id,idempotency_key,payload_json,state,due_at,created_at,correlation_id) "
            "VALUES(?,?,?,?,?,'pending',?,?,?)",
            (effect_id, kind, aggregate_id, idempotency_key, encoded, due, _now(), correlation),
        )
        effect = db.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        CoordinatorStore._append_effect_event(
            db, effect, event_type="effect.enqueued",
            payload={"kind": kind, "aggregate_id": aggregate_id, "idempotency_key": idempotency_key},
            actor=actor if actor is not None else {"kind": "system", "component": "durable-effect-queue"},
            causation_id=cause,
        )
        return effect_id

    @staticmethod
    def enqueue_outbox_effect(db: sqlite3.Connection, message_id: str, *,
                              correlation_id: str | None = None,
                              causation_id: str | None = None,
                              actor: object | None = None) -> str:
        CoordinatorStore._require_transaction(db)
        message_id = _text(message_id, "message_id")
        message = db.execute(
            "SELECT * FROM outbox_messages WHERE message_id=?", (message_id,)
        ).fetchone()
        if message is None or message["state"] != "pending":
            raise ValueError("outbox effect requires a pending message")
        payload = {
            "schema_version": 1,
            "message_id": message_id,
            "ticket_id": message["ticket_id"],
            "channel_id": message["channel_id"],
            "message_kind": message["kind"],
            "body_ref": message["body_ref"],
            "body_hash": message["body_hash"],
            "idempotency_key": message["idempotency_key"],
        }
        effect_id = CoordinatorStore.enqueue_effect(
            db, kind="outbox.send", aggregate_id=f"outbox:{message_id}",
            idempotency_key=message["idempotency_key"], payload=payload,
            correlation_id=correlation_id, causation_id=causation_id, actor=actor,
        )
        old = db.execute(
            "SELECT effect_id FROM outbox_effect_links WHERE message_id=?", (message_id,)
        ).fetchone()
        if old is not None and old["effect_id"] != effect_id:
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/outbox-effect-conflict")
        db.execute(
            "INSERT OR IGNORE INTO outbox_effect_links(message_id,effect_id,created_at) VALUES(?,?,?)",
            (message_id, effect_id, _now()),
        )
        return effect_id

    @staticmethod
    def claim_effects(db: sqlite3.Connection, *, owner: str, limit: int, lease_until: str) -> list[dict]:
        """只领取到期意图；过期租约必须先回查，不能自动重放。"""
        CoordinatorStore._require_transaction(db)
        _text(owner, "owner")
        if type(limit) is not int or not 1 <= limit <= _MAX_EFFECT_BATCH:
            raise ValueError(f"limit must be between 1 and {_MAX_EFFECT_BATCH}")
        lease = _time(lease_until, "lease_until")
        if lease <= _now():
            raise ValueError("lease_until must be in the future")
        rows = db.execute(
            "SELECT effect_id FROM effects WHERE kind<>'outbox.send' AND state IN ('pending','retry_wait') AND due_at<=? "
            "ORDER BY due_at,created_at LIMIT ?", (_now(), limit),
        ).fetchall()
        result = []
        for row in rows:
            before = db.execute(
                "SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)
            ).fetchone()
            changed = db.execute(
                "UPDATE effects SET state='leased',lease_owner=?,lease_until=?,generation=generation+1,"
                "attempts=attempts+1 WHERE effect_id=? AND state IN ('pending','retry_wait')",
                (owner, lease, row["effect_id"]),
            ).rowcount
            if changed:
                effect = db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)
                ).fetchone()
                CoordinatorStore._append_effect_event(
                    db, effect, event_type="effect.leased",
                    payload={"generation": effect["generation"], "attempts": effect["attempts"],
                             "lease_until": lease},
                    actor={"kind": "system", "component": "durable-effect-queue", "owner": owner},
                    causation_id=before["last_event_id"],
                )
                result.append(dict(db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)
                ).fetchone()))
        return result

    @staticmethod
    def mark_expired_effects_unknown(db: sqlite3.Connection, *, limit: int = _MAX_EFFECT_BATCH) -> list[dict]:
        """将过期租约转为待回查状态；绝不把过期租约直接放回可执行队列。"""
        CoordinatorStore._require_transaction(db)
        if type(limit) is not int or not 1 <= limit <= _MAX_EFFECT_BATCH:
            raise ValueError(f"limit must be between 1 and {_MAX_EFFECT_BATCH}")
        now = _now()
        rows = db.execute(
            "SELECT effect_id FROM effects WHERE state='leased' AND lease_until<=? "
            "ORDER BY lease_until,created_at LIMIT ?",
            (now, limit),
        ).fetchall()
        result = []
        for row in rows:
            before = db.execute(
                "SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)
            ).fetchone()
            changed = db.execute(
                "UPDATE effects SET state='unknown',last_error=COALESCE(last_error,?),"
                "lease_owner=NULL,lease_until=NULL "
                "WHERE effect_id=? AND state='leased' AND lease_until<=?",
                ("lease expired; external outcome requires reconciliation", row["effect_id"], now),
            ).rowcount
            if changed:
                effect = db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)
                ).fetchone()
                event_id = CoordinatorStore._append_effect_event(
                    db, effect, event_type="effect.unknown",
                    payload={"generation": effect["generation"], "reason": "lease_expired"},
                    actor={"kind": "system", "component": "durable-effect-recovery"},
                    causation_id=before["last_event_id"],
                )
                if effect["kind"] == "outbox.send":
                    from .superagent_outbox import OutboxEffectService
                    OutboxEffectService.mark_expired_unknown(
                        db, effect_id=effect["effect_id"], generation=effect["generation"],
                        causation_id=event_id,
                    )
                result.append(dict(db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)
                ).fetchone()))
        return result

    @staticmethod
    def settle_effect(db: sqlite3.Connection, *, effect_id: str, owner: str, generation: int,
                      state: str, receipt: object | None = None, error: str | None = None,
                      due_at: str | None = None) -> bool:
        """结算当前租约；unknown 只能通过回查接口核实。"""
        CoordinatorStore._require_transaction(db)
        _text(effect_id, "effect_id")
        if db.execute("SELECT 1 FROM outbox_effect_links WHERE effect_id=?", (effect_id,)).fetchone():
            raise ValueError("linked outbox effects must be settled through OutboxEffectService")
        _text(owner, "owner")
        if type(generation) is not int or generation < 1:
            raise ValueError("generation must be a positive integer")
        if state not in {"completed", "retry_wait", "failed", "unknown"}:
            raise ValueError("invalid effect settlement state")
        if state == "completed":
            if receipt is None or error is not None or due_at is not None:
                raise ValueError("completed requires a receipt and no error or due_at")

        elif state in {"retry_wait", "failed", "unknown"}:
            _text(error, "error")
            if (state == "retry_wait") != (due_at is not None):
                raise ValueError("due_at is required only for retry_wait")
        due = None if due_at is None else _time(due_at, "due_at")
        receipt_json = None if receipt is None else _json(receipt)
        before = db.execute(
            "SELECT * FROM effects WHERE effect_id=? AND state='leased' AND lease_owner=? AND generation=?",
            (effect_id, owner, generation),
        ).fetchone()
        if before is None:
            return False
        changed = db.execute(
            "UPDATE effects SET state=?,receipt_json=?,last_error=?,due_at=COALESCE(?,due_at),"
            "lease_owner=NULL,lease_until=NULL WHERE effect_id=? AND state='leased' AND lease_owner=? AND generation=?",
            (state, receipt_json, error, due, effect_id, owner, generation),
        ).rowcount
        if changed != 1:
            return False
        effect = db.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        CoordinatorStore._append_effect_event(
            db, effect, event_type=f"effect.{state}",
            payload={"generation": generation, "receipt_present": receipt is not None},
            actor={"kind": "system", "component": "durable-effect-worker", "owner": owner},
            causation_id=before["last_event_id"],
        )
        return True

    @staticmethod
    def reconcile_effect(db: sqlite3.Connection, *, effect_id: str, generation: int, state: str,
                         receipt: object, error: str | None = None, due_at: str | None = None) -> bool:
        """只凭原操作回执结算 unknown，不执行新的外部操作。"""

        CoordinatorStore._require_transaction(db)
        _text(effect_id, "effect_id")
        if db.execute("SELECT 1 FROM outbox_effect_links WHERE effect_id=?", (effect_id,)).fetchone():
            raise ValueError("linked outbox effects must be reconciled through OutboxEffectService")
        if type(generation) is not int or generation < 1:
            raise ValueError("generation must be a positive integer")
        if state not in {"completed", "retry_wait", "failed"} or receipt is None:
            raise ValueError("invalid reconciliation result")
        if state in {"retry_wait", "failed"}:

            _text(error, "error")
            if (state == "retry_wait") != (due_at is not None):
                raise ValueError("due_at is required only for retry_wait")

        elif error is not None or due_at is not None:
            raise ValueError("completed reconciliation cannot carry error or due_at")
        due = None if due_at is None else _time(due_at, "due_at")
        before = db.execute(
            "SELECT * FROM effects WHERE effect_id=? AND state='unknown' AND generation=?",
            (effect_id, generation),
        ).fetchone()
        if before is None:
            return False
        changed = db.execute(
            "UPDATE effects SET state=?,receipt_json=?,last_error=?,due_at=COALESCE(?,due_at) "
            "WHERE effect_id=? AND state='unknown' AND generation=?",
            (state, _json(receipt), error, due, effect_id, generation),
        ).rowcount
        if changed != 1:
            return False
        effect = db.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        CoordinatorStore._append_effect_event(
            db, effect, event_type=f"effect.{state}",
            payload={"generation": generation, "receipt_present": True, "reconciled": True},
            actor={"kind": "system", "component": "durable-effect-recovery"},
            causation_id=before["last_event_id"],
        )
        return True

    @staticmethod
    def cancel_effect(db: sqlite3.Connection, *, effect_id: str) -> bool:
        """只撤销尚未领取的意图。"""
        CoordinatorStore._require_transaction(db)
        _text(effect_id, "effect_id")
        if db.execute("SELECT 1 FROM outbox_effect_links WHERE effect_id=?", (effect_id,)).fetchone():
            raise ValueError("linked outbox effects must be withdrawn through OutboxEffectService")
        before = db.execute(
            "SELECT * FROM effects WHERE effect_id=? AND state IN ('pending','retry_wait')",
            (effect_id,),
        ).fetchone()
        if before is None:
            return False
        changed = db.execute(
            "UPDATE effects SET state='cancelled' WHERE effect_id=? AND state IN ('pending','retry_wait')",
            (effect_id,),
        ).rowcount
        if changed != 1:
            return False
        effect = db.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        CoordinatorStore._append_effect_event(
            db, effect, event_type="effect.cancelled",
            payload={"generation": effect["generation"]},
            actor={"kind": "system", "component": "durable-effect-queue"},
            causation_id=before["last_event_id"],
        )
        return True

_REPOSITORY_SPECS = {
    "tickets": {
        "table": "tickets", "identity": "ticket_id", "aggregate_type": "ticket",
        "required": ("ticket_id", "mandate_id", "origin_trigger_id", "title"),
        "create": ("ticket_id", "mandate_id", "origin_trigger_id", "title", "state", "outcome", "ended_by", "ended_at"),
        "update": ("title", "state", "outcome", "ended_by", "ended_at"),
        "defaults": {"state": "open", "outcome": None, "ended_by": None, "ended_at": None},
        "json": (), "times": ("ended_at",),
    },
    "missions": {
        "table": "missions", "identity": "mission_id", "aggregate_type": "mission",
        "required": ("mission_id", "ticket_id", "sequence", "repo", "base_commit"),
        "create": ("mission_id", "ticket_id", "sequence", "state", "outcome", "goal_ref",
                   "policy_version_at_start", "source_revision_at_start", "plan_revision",
                   "repo", "base_commit", "round_no", "budgets_json"),
        "update": ("state", "outcome", "goal_ref", "round_no", "budgets_json"),
        "defaults": {"state": "planning", "outcome": None, "goal_ref": None,
                     "policy_version_at_start": None, "source_revision_at_start": None,
                     "plan_revision": 0, "round_no": 0, "budgets_json": {}},
        "json": ("budgets_json",), "times": (),
    },
    "jobs": {
        "table": "jobs", "identity": "job_id", "aggregate_type": "job",
        "required": ("job_id", "mission_id", "plan_revision", "node_id", "role", "spec_hash"),
        "create": ("job_id", "mission_id", "plan_revision", "node_id", "role", "profile_id",
                   "instruction_ref", "instruction_hash", "state", "spec_hash", "result_hash"),
        "update": ("state", "result_hash"),
        "defaults": {"profile_id": None, "instruction_ref": None, "instruction_hash": None,
                     "state": "pending", "result_hash": None},
        "json": (), "times": (),
    },
    "workers": {
        "table": "workers", "identity": "worker_id", "aggregate_type": "worker",
        "required": ("worker_id", "ticket_id", "profile_id", "repo", "base_commit", "runtime_instance_id", "identity_hash"),
        "create": ("worker_id", "ticket_id", "mission_id", "job_id", "stage", "profile_id", "repo", "base_commit", "runtime_instance_id", "workspace_id", "session_id", "state", "identity_hash", "created_at", "last_seen_at"),
        "update": ("state", "workspace_id", "session_id", "last_seen_at"),
        "defaults": {"mission_id": None, "job_id": None, "stage": 0, "workspace_id": None, "session_id": None, "state": "planned", "last_seen_at": None},
        "json": (), "times": ("created_at", "last_seen_at"),
    },
    "attentions": {
        "table": "attentions", "identity": "attention_id", "aggregate_type": "attention",
        "required": ("attention_id", "ticket_id", "mission_id", "trigger_id", "assignment_id", "question_ref", "question_text", "options_json"),
        "create": ("attention_id", "ticket_id", "mission_id", "trigger_id", "assignment_id", "question_ref", "question_text", "options_json", "state", "answer_trigger_id", "created_at", "resolved_at"),
        "update": ("state", "answer_trigger_id", "resolved_at"),
        "defaults": {"state": "open", "answer_trigger_id": None, "resolved_at": None},
        "json": ("options_json",), "times": ("created_at", "resolved_at"),
    },
    "outbox": {
        "table": "outbox_messages", "identity": "message_id", "aggregate_type": "outbox_message",
        "required": ("message_id", "ticket_id", "channel_id", "kind", "body_ref", "body_hash", "idempotency_key"),
        "create": ("message_id", "ticket_id", "channel_id", "kind", "body_ref", "body_hash",
                   "idempotency_key", "state", "provider_id", "provider_key", "attempts", "sent_at"),
        "update": ("state", "provider_id", "provider_key", "attempts", "sent_at"),
        "defaults": {"state": "pending", "provider_id": None, "provider_key": None,
                     "attempts": 0, "sent_at": None},

        "json": (), "times": ("sent_at",),
        "transitions": {
            "pending": {"sending", "withdrawn"},
            "sending": {"sent", "retry_wait", "dropped", "unknown"},
            "retry_wait": {"sending", "withdrawn"},
            "unknown": {"sent", "retry_wait", "dropped"},
            "sent": set(), "dropped": set(), "withdrawn": set(),
        },

    },
    "assignments": {
        "table": "assignments", "identity": "assignment_id", "aggregate_type": "assignment",

        "required": ("assignment_id", "job_id", "attempt_no", "submission_key"),
        "create": ("assignment_id", "job_id", "attempt_no", "worker_id", "session_id",
                   "workspace_id", "run_id", "submission_key", "state", "report_json",
                   "started_at", "ended_at", "error_ref"),
        "update": ("worker_id", "session_id", "workspace_id", "run_id", "state",
                   "report_json", "started_at", "ended_at", "error_ref"),
        "defaults": {"worker_id": None, "session_id": None, "workspace_id": None,
                     "run_id": None, "state": "pending", "report_json": {},
                     "started_at": None, "ended_at": None, "error_ref": None},
        "json": ("report_json",), "times": ("started_at", "ended_at"),
    },
}

class JobDependencyRepository:
    """读取不可变计划版本中的 DAG 依赖。"""

    def __init__(self, uow: "CoordinatorUnitOfWork"):
        self._uow = uow

    def for_job(self, job_id: str) -> list[dict]:
        key = _text(job_id, "job_id")
        rows = self._uow.db.execute(
            "SELECT dependency.*, source.node_id AS node_id, target.node_id AS depends_on_node_id "

            "FROM job_dependencies AS dependency "
            "JOIN jobs AS source ON source.job_id=dependency.job_id "
            "JOIN jobs AS target ON target.job_id=dependency.depends_on_job_id "
            "WHERE dependency.job_id=? ORDER BY dependency.ordinal",
            (key,),
        ).fetchall()
        return [dict(row) for row in rows]

    def for_plan(self, mission_id: str, plan_revision: int) -> list[dict]:
        return self.read_plan(self._uow.db, mission_id, plan_revision)

    @staticmethod
    def read_plan(db, mission_id: str, plan_revision: int) -> list[dict]:
        """在调用方持有的读事务中读取依赖，不创建写入工作单元。"""
        mission = _text(mission_id, "mission_id")
        if type(plan_revision) is not int or plan_revision < 1:
            raise ValueError("plan_revision must be a positive integer")
        rows = db.execute(
            "SELECT dependency.*, source.node_id AS node_id, target.node_id AS depends_on_node_id "
            "FROM job_dependencies AS dependency "
            "JOIN jobs AS source ON source.job_id=dependency.job_id "
            "JOIN jobs AS target ON target.job_id=dependency.depends_on_job_id "
            "WHERE dependency.mission_id=? AND dependency.plan_revision=? "
            "ORDER BY source.node_id,dependency.ordinal",
            (mission, plan_revision),
        ).fetchall()
        return [dict(row) for row in rows]

class PlanRevisionRepository:
    """创建和读取不可变 PlanRevision，并在同一事务内物化 DAG。"""

    def __init__(self, uow: "CoordinatorUnitOfWork"):
        self._uow = uow

    def get(self, mission_id: str, plan_revision: int) -> dict | None:
        return self.read(self._uow.db, mission_id, plan_revision)

    @staticmethod
    def read(db, mission_id: str, plan_revision: int) -> dict | None:
        """复用完整冻结校验，允许只读消费者在自己的事务中读取。"""
        mission = _text(mission_id, "mission_id")
        if type(plan_revision) is not int or plan_revision < 1:
            raise ValueError("plan_revision must be a positive integer")
        row = db.execute(
            "SELECT * FROM mission_plans WHERE mission_id=? AND plan_revision=?",
            (mission, plan_revision),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            plan_json = json.loads(result["plan_json"])
            acceptance_json = json.loads(result["acceptance_json"])
        except (TypeError, ValueError) as exc:
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt") from exc
        if (
            result["sealed"] != 1
            or _hash(_json(plan_json)) != result["plan_hash"]
            or _hash(_json(acceptance_json)) != result["acceptance_hash"]
            or not isinstance(plan_json, dict)
            or not isinstance(plan_json.get("nodes"), list)
        ):
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt")
        mission_row = db.execute('SELECT plan_revision FROM missions WHERE mission_id=?', (mission,)).fetchone()
        if mission_row is None or mission_row["plan_revision"] < plan_revision:
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt")
        result["plan_json"] = plan_json
        result["acceptance_json"] = acceptance_json
        result["jobs"] = [dict(job) for job in db.execute(
            "SELECT * FROM jobs WHERE mission_id=? AND plan_revision=? ORDER BY node_id",
            (mission, plan_revision),
        ).fetchall()]
        result["dependencies"] = JobDependencyRepository.read_plan(db, mission, plan_revision)
        expected_nodes = {}
        for node in plan_json["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
                raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt")
            node_id = node["node_id"]
            if node_id in expected_nodes:
                raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt")
            expected_nodes[node_id] = node
        actual_nodes = {job["node_id"]: job for job in result["jobs"]}
        if set(actual_nodes) != set(expected_nodes):
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt")
        for node_id, job in actual_nodes.items():
            expected_job_id = "job_" + _hash(f"{mission}\0{plan_revision}\0{node_id}")[:32]
            node = expected_nodes[node_id]
            expected_spec = {key: value for key, value in node.items() if key != "dependencies"}
            if (
                job["job_id"] != expected_job_id
                or job["role"] != node.get("role")
                or job["profile_id"] != node.get("profile_id")
                or job["instruction_ref"] != node.get("instruction_ref")
                or job["instruction_hash"] != node.get("instruction_hash")
                or job["spec_hash"] != _hash(_json(expected_spec))
            ):
                raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt")
        expected_edges = sorted(
            (node["node_id"], edge["node_id"], ordinal, int(edge.get("requires_delivery", False)))
            for node in plan_json["nodes"]
            for ordinal, edge in enumerate(node.get("dependencies", []))
        )
        actual_edges = sorted(
            (edge["node_id"], edge["depends_on_node_id"], edge["ordinal"], edge["requires_delivery"])
            for edge in result["dependencies"]
        )
        if actual_edges != expected_edges:
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/plan-corrupt")
        return result

    def list_for_mission(self, mission_id: str) -> list[dict]:
        mission = _text(mission_id, "mission_id")
        revisions = self._uow.db.execute(

            "SELECT plan_revision FROM mission_plans WHERE mission_id=? ORDER BY plan_revision",
            (mission,),
        ).fetchall()
        return [self.get(mission, row["plan_revision"]) for row in revisions]

    def create(self, mission_id: str, *, expected_mission_revision: int, plan: dict,
               acceptance: object, created_by_kind: str, created_by_ref: str,
               system_profiles: dict[str, str] | None = None) -> dict | None:
        mission = _text(mission_id, "mission_id")
        if type(expected_mission_revision) is not int or expected_mission_revision < 1:
            raise ValueError("expected_mission_revision must be a positive integer")
        if not isinstance(plan, dict) or not isinstance(plan.get("nodes"), list) or not plan["nodes"]:
            raise ValueError("plan must contain a non-empty nodes array")
        if not isinstance(acceptance, (dict, list)):
            raise ValueError("acceptance must be an object or array")
        if created_by_kind not in {"host", "human", "assignment"}:
            raise ValueError("invalid plan creator kind")
        creator = _text(created_by_ref, "created_by_ref")
        mission_row = self._uow.missions.get(mission)
        if mission_row is None or mission_row["aggregate_revision"] != expected_mission_revision:
            return None
        if created_by_kind == "assignment":
            planner = self._uow.db.execute(
                "SELECT assignment.assignment_id FROM assignments AS assignment "
                "JOIN jobs ON jobs.job_id=assignment.job_id "
                "WHERE assignment.assignment_id=? AND jobs.mission_id=? "
                "AND jobs.plan_revision=0 AND jobs.role='plan'",
                (creator, mission),

            ).fetchone()
            if planner is None:
                raise ValueError("assignment creator must belong to this mission's planner job")

        revision = mission_row["plan_revision"] + 1
        node_ids: set[str] = set()
        normalized_nodes: list[dict] = []
        for node in plan["nodes"]:
            if not isinstance(node, dict):
                raise ValueError("each plan node must be an object")
            node_id = _text(node.get("node_id"), "node_id")
            if node_id.startswith("__system__") or node_id in node_ids:
                raise ValueError("plan node identity is reserved or duplicated")
            if node.get("role") != "work":
                raise ValueError("planner revisions may contain work nodes only")
            profile_id = _text(node.get("profile_id"), "profile_id")
            instruction_ref = node.get("instruction_ref")
            if instruction_ref is not None:
                _text(instruction_ref, "instruction_ref")
            instruction_hash = node.get("instruction_hash")
            if instruction_hash is not None and (
                not isinstance(instruction_hash, str) or len(instruction_hash) != 64
                or any(char not in "0123456789abcdef" for char in instruction_hash)
            ):
                raise ValueError("instruction_hash must be a lowercase SHA-256 digest")
            dependencies = node.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise ValueError("node dependencies must be an array")
            seen_dependencies: set[str] = set()
            normalized_dependencies = []
            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    raise ValueError("each dependency must be an object")
                target = _text(dependency.get("node_id"), "dependency node_id")
                delivery = dependency.get("requires_delivery", False)
                if type(delivery) is not bool or target in seen_dependencies or target == node_id:
                    raise ValueError("dependency identity or requires_delivery is invalid")
                seen_dependencies.add(target)
                normalized_dependencies.append({"node_id": target, "requires_delivery": delivery})
            node_ids.add(node_id)
            normalized_nodes.append({
                **node, "node_id": node_id, "role": "work", "profile_id": profile_id,
                "dependencies": normalized_dependencies,
            })
        if system_profiles is not None:
            if (not isinstance(system_profiles, dict)
                    or set(system_profiles) != {"integrate", "review"}):
                raise ValueError("system profiles require integrate and review roles")
            work_ids = [node["node_id"] for node in normalized_nodes]
            for role, dependencies in (
                ("integrate", work_ids), ("review", ["__system__integrate"]),
            ):
                normalized_nodes.append({
                    "node_id": "__system__" + role, "role": role,
                    "profile_id": _text(system_profiles[role], role + " profile"),
                    "instruction_ref": None, "instruction_hash": None,
                    "dependencies": [{"node_id": node_id, "requires_delivery": True}
                                     for node_id in dependencies],
                })
        by_id = {node["node_id"]: node for node in normalized_nodes}
        graph = {}

        for node in normalized_nodes:
            targets = [edge["node_id"] for edge in node["dependencies"]]
            if any(target not in by_id for target in targets):
                raise ValueError("dependency target must belong to the same plan revision")
            graph[node["node_id"]] = targets
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("plan dependencies contain a cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target in graph[node_id]:
                visit(target)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in graph:
            visit(node_id)

        canonical_plan = {**plan, "nodes": normalized_nodes}
        plan_json = _json(canonical_plan)

        acceptance_json = _json(acceptance)
        plan_hash = _hash(plan_json)
        acceptance_hash = _hash(acceptance_json)
        if not CoordinatorStore.compare_and_swap(
            self._uow.db, "mission", mission, expected_mission_revision
        ):
            return None
        if not CoordinatorStore.compare_and_swap(
            self._uow.db, "plan_revision", f"{mission}:{revision}", 0
        ):
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/revision-conflict")
        now = _now()
        self._uow.db.execute(
            "INSERT INTO mission_plans(mission_id,plan_revision,plan_json,plan_hash,acceptance_json,"
            "acceptance_hash,created_at,created_by_kind,created_by_ref,sealed) VALUES(?,?,?,?,?,?,?,?,?,0)",
            (mission, revision, plan_json, plan_hash, acceptance_json, acceptance_hash,
             now, created_by_kind, creator),
        )
        changed = self._uow.db.execute(
            "UPDATE missions SET plan_revision=?,updated_at=?,aggregate_revision=aggregate_revision+1 "
            "WHERE mission_id=? AND aggregate_revision=?",
            (revision, now, mission, expected_mission_revision),
        ).rowcount
        if changed != 1:
            raise RuntimeError("mission revision ledger diverged from record revision")
        self._uow.store.append_audit(
            self._uow.db, aggregate_type="mission", aggregate_id=mission,
            event_type="mission.plan_revision_created",

            payload={"plan_revision": revision, "plan_hash": plan_hash,
                     "acceptance_hash": acceptance_hash},
            actor=self._uow.actor, correlation_id=self._uow.correlation_id,
        )
        job_ids = {}
        for node in normalized_nodes:
            job_id = "job_" + _hash(f"{mission}\0{revision}\0{node['node_id']}")[:32]
            job_ids[node["node_id"]] = job_id
            spec = {key: value for key, value in node.items() if key != "dependencies"}
            self._uow.jobs.create({
                "job_id": job_id, "mission_id": mission, "plan_revision": revision,
                "node_id": node["node_id"], "role": node["role"], "profile_id": node["profile_id"],
                "instruction_ref": node.get("instruction_ref"),
                "instruction_hash": node.get("instruction_hash"),
                "spec_hash": _hash(_json(spec)),
            })
        edge_count = 0
        for node in normalized_nodes:
            for ordinal, edge in enumerate(node["dependencies"]):
                self._uow.db.execute(
                    "INSERT INTO job_dependencies(job_id,depends_on_job_id,mission_id,plan_revision,"
                    "ordinal,requires_delivery) VALUES(?,?,?,?,?,?)",
                    (job_ids[node["node_id"]], job_ids[edge["node_id"]], mission, revision,
                     ordinal, int(edge["requires_delivery"])),
                )

                edge_count += 1
        self._uow.db.execute(
            "UPDATE mission_plans SET sealed=1 WHERE mission_id=? AND plan_revision=? AND sealed=0",
            (mission, revision),
        )
        self._uow.store.append_audit(
            self._uow.db, aggregate_type="plan_revision",
            aggregate_id=f"{mission}:{revision}", event_type="plan_revision.created",
            payload={"plan_hash": plan_hash, "acceptance_hash": acceptance_hash,
                     "node_count": len(normalized_nodes), "edge_count": edge_count,
                     "created_by_kind": created_by_kind, "created_by_ref": creator},
            actor=self._uow.actor, correlation_id=self._uow.correlation_id,
        )
        return self.get(mission, revision)

class AggregateRepository:
    """单一聚合类型的版本化持久化入口。"""

    def __init__(self, uow: "CoordinatorUnitOfWork", spec: dict):
        self._uow = uow
        self._spec = spec

    def get(self, identity: str) -> dict | None:

        key = _text(identity, self._spec["identity"])
        row = self._uow.db.execute(
            f"SELECT * FROM {self._spec['table']} WHERE {self._spec['identity']}=?", (key,)
        ).fetchone()
        return None if row is None else dict(row)

    def create(self, values: dict, *, event_type: str | None = None,
               audit_payload: object | None = None, effect: dict | None = None) -> dict:
        if not isinstance(values, dict):
            raise TypeError("values must be a dictionary")
        unknown = set(values) - set(self._spec["create"])
        if unknown:
            raise ValueError(f"unsupported create fields: {sorted(unknown)}")
        record = dict(self._spec["defaults"])
        record.update(values)
        missing = [field for field in self._spec["required"] if field not in record or record[field] is None]
        if missing:
            raise ValueError(f"missing required fields: {missing}")
        identity = _text(record[self._spec["identity"]], self._spec["identity"])
        if not CoordinatorStore.compare_and_swap(self._uow.db, self._spec["aggregate_type"], identity, 0):
            raise HarnessError(tr("storage.schema.invalid"), code="superagent/revision-conflict")
        now = _now()
        if self._spec["table"] in {"tickets", "missions", "outbox_messages", "workers", "attentions"}:
            record["created_at"] = now
            record["updated_at"] = now
        for field in self._spec["json"]:
            record[field] = _json(record[field])
        for field in self._spec["times"]:
            if record.get(field) is not None:
                record[field] = _time(record[field], field)
        record["aggregate_revision"] = 1
        columns = tuple(record)
        self._uow.db.execute(
            f"INSERT INTO {self._spec['table']}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(record[column] for column in columns),
        )
        event_id = self._record_event(
            identity, event_type or f"{self._spec['aggregate_type']}.created",
            audit_payload if audit_payload is not None else record,
        )
        if self._spec["table"] == "outbox_messages":
            if effect is not None:
                raise ValueError("outbox messages create their durable send effect automatically")
            self._uow.store.enqueue_outbox_effect(
                self._uow.db, identity, correlation_id=self._uow.correlation_id,
                causation_id=event_id, actor=self._uow.actor,
            )
        else:
            self._enqueue(effect, causation_id=event_id)
        return self.get(identity)

    def update(self, identity: str, *, expected_revision: int, changes: dict,
               event_type: str, audit_payload: object | None = None,
               effect: dict | None = None) -> dict | None:
        if isinstance(changes, dict) and "state" in changes and self._spec["table"] in {
            "tickets", "missions", "jobs", "assignments", "workers", "attentions",
        }:
            raise PermissionError("aggregate state changes must pass through CoordinatorApplicationService")
        return self._write(
            identity, expected_revision=expected_revision, changes=changes,
            event_type=event_type, audit_payload=audit_payload, effect=effect,
        )

    def _transition(self, identity: str, *, expected_revision: int, changes: dict,
                    event_type: str, audit_payload: object | None = None,
                    effect: dict | None = None) -> dict | None:
        if self._spec["table"] not in {"tickets", "missions", "jobs", "assignments", "workers", "attentions"}:
            raise ValueError("only coordinator aggregates have application state transitions")
        if not isinstance(changes, dict) or "state" not in changes:
            raise ValueError("application transition must include a target state")
        return self._write(
            identity, expected_revision=expected_revision, changes=changes,
            event_type=event_type, audit_payload=audit_payload, effect=effect,
        )

    def _write(self, identity: str, *, expected_revision: int, changes: dict,
               event_type: str, audit_payload: object | None = None,
               effect: dict | None = None) -> dict | None:
        key = _text(identity, self._spec["identity"])
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty dictionary")

        unknown = set(changes) - set(self._spec["update"])
        if unknown:
            raise ValueError(f"unsupported update fields: {sorted(unknown)}")
        _text(event_type, "event_type")
        if self._spec["table"] == "outbox_messages" and "state" in changes:
            raise ValueError("outbox state is owned by its linked durable Effect")
        if "state" in changes and "transitions" in self._spec:
            current = self.get(key)
            if current is None or changes["state"] not in self._spec["transitions"][current["state"]]:
                raise ValueError("invalid outbox state transition")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        encoded = dict(changes)
        for field in self._spec["json"]:
            if field in encoded:
                encoded[field] = _json(encoded[field])
        for field in self._spec["times"]:
            if field in encoded and encoded[field] is not None:
                encoded[field] = _time(encoded[field], field)
        if self._spec["table"] in {"tickets", "missions"}:
            encoded["updated_at"] = _now()
        if not CoordinatorStore.compare_and_swap(
            self._uow.db, self._spec["aggregate_type"], key, expected_revision
        ):
            return None
        assignments = [f"{field}=?" for field in encoded]
        assignments.append("aggregate_revision=aggregate_revision+1")

        cursor = self._uow.db.execute(
            f"UPDATE {self._spec['table']} SET {','.join(assignments)} "
            f"WHERE {self._spec['identity']}=? AND aggregate_revision=?",
            (*encoded.values(), key, expected_revision),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("aggregate revision ledger diverged from record revision")
        payload = audit_payload if audit_payload is not None else {
            "revision": expected_revision + 1, "changes": encoded,
        }
        event_id = self._record_event(key, event_type, payload)
        self._enqueue(effect, causation_id=event_id)
        return self.get(key)

    def _record_event(self, identity: str, event_type: str, payload: object) -> str:
        return self._uow.store.append_audit(
            self._uow.db, aggregate_type=self._spec["aggregate_type"], aggregate_id=identity,
            event_type=event_type, payload=payload, actor=self._uow.actor,
            correlation_id=self._uow.correlation_id,
        )

    def _enqueue(self, effect: dict | None, *, causation_id: str | None = None) -> None:
        if effect is None:
            return
        if not isinstance(effect, dict) or not {"kind", "aggregate_id", "idempotency_key", "payload"} <= set(effect):
            raise ValueError("effect requires kind, aggregate_id, idempotency_key and payload")
        self._uow.store.enqueue_effect(
            self._uow.db, kind=effect["kind"], aggregate_id=effect["aggregate_id"],
            idempotency_key=effect["idempotency_key"], payload=effect["payload"],
            due_at=effect.get("due_at"), correlation_id=self._uow.correlation_id,
            causation_id=causation_id, actor=self._uow.actor,
        )

class ReviewRepository:
    def __init__(self, uow: "CoordinatorUnitOfWork"):
        self._uow = uow

    def get_by_reviewer_assignment(self, assignment_id: str) -> dict | None:
        row = self._uow.db.execute(
            "SELECT * FROM reviews WHERE reviewer_assignment_id=?",
            (_text(assignment_id, "reviewer_assignment_id"),),
        ).fetchone()
        return dict(row) if row is not None else None

    def create(self, *, mission_id: str, reviewer_assignment_id: str,
               candidate_artifact_id: str, plan_revision: int, acceptance_hash: str,
               verdict: dict, evidence_set_hash: str, state: str) -> dict:
        mission = _text(mission_id, "mission_id")
        reviewer = _text(reviewer_assignment_id, "reviewer_assignment_id")
        candidate = _text(candidate_artifact_id, "candidate_artifact_id")
        if type(plan_revision) is not int or plan_revision < 1:
            raise ValueError("plan_revision must be positive")
        for value in (acceptance_hash, evidence_set_hash):
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Review hashes must be lowercase SHA-256 digests")
        if not isinstance(verdict, dict) or state not in {"passed", "failed", "invalid"}:
            raise ValueError("Review verdict or state is invalid")
        encoded = _json(verdict)
        old = self.get_by_reviewer_assignment(reviewer)
        fields = (("mission_id", mission), ("candidate_artifact_id", candidate),
                  ("plan_revision", plan_revision), ("acceptance_hash", acceptance_hash),
                  ("verdict_json", encoded), ("evidence_set_hash", evidence_set_hash), ("state", state))
        if old is not None:
            if any(old[key] != value for key, value in fields):
                raise HarnessError(tr("superagent.conflict"), code="superagent/review-conflict")
            return old
        identity = "review_" + _hash("\0".join(
            (mission, str(plan_revision), reviewer, candidate, acceptance_hash)
        ))[:32]
        self._uow.db.execute(
            "INSERT INTO reviews(review_id,mission_id,reviewer_assignment_id,candidate_artifact_id,"
            "plan_revision,acceptance_hash,verdict_json,evidence_set_hash,state,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (identity, mission, reviewer, candidate, plan_revision, acceptance_hash,
             encoded, evidence_set_hash, state, _now()),
        )
        self._uow.store.append_audit(
            self._uow.db, aggregate_type="review", aggregate_id=identity,
            event_type="review.frozen",
            payload={"mission_id": mission, "plan_revision": plan_revision,
                     "reviewer_assignment_id": reviewer, "candidate_artifact_id": candidate,
                     "acceptance_hash": acceptance_hash, "evidence_set_hash": evidence_set_hash,
                     "state": state},
            actor=self._uow.actor, correlation_id=self._uow.correlation_id,
        )
        return dict(self._uow.db.execute(
            "SELECT * FROM reviews WHERE review_id=?", (identity,),
        ).fetchone())


class CoordinatorUnitOfWork:
    """将聚合写入、CAS、审计和副作用意图合并为一个 SQLite 事务。"""

    def __init__(self, store: CoordinatorStore, *, actor: object,
                 correlation_id: str | None = None):
        self.store = store
        self.actor = actor
        self.correlation_id = (
            "corr_" + uuid4().hex if correlation_id is None
            else _text(correlation_id, "correlation_id")
        )
        self._transaction = None
        self.db: sqlite3.Connection | None = None
        self.tickets: AggregateRepository | None = None
        self.missions: AggregateRepository | None = None
        self.jobs: AggregateRepository | None = None
        self.assignments: AggregateRepository | None = None
        self.workers: AggregateRepository | None = None
        self.attentions: AggregateRepository | None = None
        self.outbox: AggregateRepository | None = None
        self.plans: PlanRevisionRepository | None = None
        self.dependencies: JobDependencyRepository | None = None
        self.reviews: ReviewRepository | None = None

    def __enter__(self) -> "CoordinatorUnitOfWork":
        if self.db is not None:
            raise RuntimeError("Unit of Work cannot be entered twice")
        self._transaction = self.store.transaction(write=True)
        self.db = self._transaction.__enter__()
        self.tickets = AggregateRepository(self, _REPOSITORY_SPECS["tickets"])
        self.missions = AggregateRepository(self, _REPOSITORY_SPECS["missions"])

        self.jobs = AggregateRepository(self, _REPOSITORY_SPECS["jobs"])
        self.assignments = AggregateRepository(self, _REPOSITORY_SPECS["assignments"])
        self.workers = AggregateRepository(self, _REPOSITORY_SPECS["workers"])
        self.attentions = AggregateRepository(self, _REPOSITORY_SPECS["attentions"])
        self.outbox = AggregateRepository(self, _REPOSITORY_SPECS["outbox"])
        self.plans = PlanRevisionRepository(self)
        self.dependencies = JobDependencyRepository(self)
        self.reviews = ReviewRepository(self)
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        transaction = self._transaction
        self._transaction = None
        self.db = None
        if transaction is None:
            return False
        return transaction.__exit__(exc_type, exc, traceback)
