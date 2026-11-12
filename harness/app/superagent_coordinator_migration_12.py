"""Phase 3 execution, Worker, Evidence and Attention schema migration."""

MIGRATION_12 = """
ALTER TABLE ticket_inputs ADD COLUMN delivered_to_assignment_id TEXT
 REFERENCES assignments(assignment_id) ON DELETE RESTRICT;
ALTER TABLE ticket_inputs ADD COLUMN delivery_key TEXT;
ALTER TABLE ticket_inputs ADD COLUMN input_revision INTEGER NOT NULL DEFAULT 1
 CHECK(input_revision>=1);
CREATE UNIQUE INDEX ticket_inputs_delivery_key_unique
 ON ticket_inputs(delivery_key) WHERE delivery_key IS NOT NULL;
CREATE UNIQUE INDEX missions_ticket_goal_unique
 ON missions(ticket_id,goal_ref) WHERE goal_ref IS NOT NULL;

CREATE TABLE workers (
 worker_id TEXT PRIMARY KEY CHECK(length(trim(worker_id))>0),
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 profile_id TEXT NOT NULL CHECK(length(trim(profile_id))>0),
 repo TEXT NOT NULL CHECK(length(trim(repo))>0),
 base_commit TEXT NOT NULL CHECK(length(base_commit) IN (40,64)),
 runtime_instance_id TEXT NOT NULL REFERENCES instances(instance_id) ON DELETE RESTRICT,
 workspace_id TEXT,
 session_id TEXT UNIQUE,
 state TEXT NOT NULL CHECK(state IN
  ('planned','workspace_creating','workspace_ready','session_creating','ready','unknown','stopped')),
 identity_hash TEXT NOT NULL CHECK(length(identity_hash)=64),
 created_at TEXT NOT NULL,
 last_seen_at TEXT,
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 UNIQUE(ticket_id,profile_id)
);
CREATE INDEX workers_ticket_state ON workers(ticket_id,state,profile_id);
CREATE TRIGGER workers_identity_immutable
BEFORE UPDATE OF worker_id,ticket_id,profile_id,repo,base_commit,runtime_instance_id,identity_hash,created_at
ON workers BEGIN
 SELECT RAISE(ABORT,'worker identity is immutable');
END;
CREATE TRIGGER workers_session_requires_workspace
BEFORE UPDATE OF session_id ON workers
WHEN NEW.session_id IS NOT NULL AND NEW.workspace_id IS NULL BEGIN
 SELECT RAISE(ABORT,'worker session requires a workspace');
END;
CREATE TRIGGER assignments_worker_ready
BEFORE INSERT ON assignments
WHEN NEW.worker_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM workers AS worker
 JOIN jobs AS job ON job.job_id=NEW.job_id
 JOIN missions AS mission ON mission.mission_id=job.mission_id
 WHERE worker.worker_id=NEW.worker_id AND worker.state='ready'
   AND worker.ticket_id=mission.ticket_id
   AND worker.session_id=NEW.session_id
   AND worker.workspace_id=NEW.workspace_id
) BEGIN
 SELECT RAISE(ABORT,'assignment requires its ready ticket Worker');
END;
CREATE TRIGGER assignments_worker_identity_immutable
BEFORE UPDATE OF worker_id,session_id,workspace_id ON assignments
WHEN OLD.worker_id IS NOT NULL AND (
 NEW.worker_id IS NOT OLD.worker_id OR NEW.session_id IS NOT OLD.session_id
 OR NEW.workspace_id IS NOT OLD.workspace_id
) BEGIN
 SELECT RAISE(ABORT,'assignment Worker binding is immutable');
END;

CREATE TABLE evidence (
 evidence_id TEXT PRIMARY KEY CHECK(length(trim(evidence_id))>0),
 assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
 harness_instance_id TEXT NOT NULL REFERENCES instances(instance_id) ON DELETE RESTRICT,
 session_id TEXT NOT NULL CHECK(length(trim(session_id))>0),
 run_id TEXT NOT NULL CHECK(length(trim(run_id))>0),
 evidence_type TEXT NOT NULL CHECK(evidence_type IN ('run','tool','background')),
 turn_id TEXT,
 tool_call_id TEXT,
 result_seq INTEGER,
 reference_hash TEXT NOT NULL CHECK(length(reference_hash)=64),
 payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 reference_json TEXT NOT NULL CHECK(json_valid(reference_json)),
 created_at TEXT NOT NULL,
 UNIQUE(assignment_id,evidence_type,reference_hash)
);
CREATE INDEX evidence_assignment ON evidence(assignment_id,evidence_type,created_at,evidence_id);
CREATE TRIGGER evidence_immutable_update
BEFORE UPDATE ON evidence BEGIN
 SELECT RAISE(ABORT,'execution evidence is immutable');
END;
CREATE TRIGGER evidence_immutable_delete
BEFORE DELETE ON evidence BEGIN
 SELECT RAISE(ABORT,'execution evidence is immutable');
END;

CREATE TABLE attentions (
 attention_id TEXT PRIMARY KEY CHECK(length(trim(attention_id))>0),
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
 trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
 question_ref TEXT NOT NULL CHECK(length(trim(question_ref))>0),
 question_text TEXT NOT NULL CHECK(length(trim(question_text))>0 AND length(question_text)<=8192),
 options_json TEXT NOT NULL CHECK(json_valid(options_json)),
 state TEXT NOT NULL CHECK(state IN ('open','answered','dismissed')),
 answer_trigger_id TEXT REFERENCES triggers(trigger_id) ON DELETE RESTRICT,
 created_at TEXT NOT NULL,
 resolved_at TEXT,
 aggregate_revision INTEGER NOT NULL CHECK(aggregate_revision>=1),
 UNIQUE(ticket_id,trigger_id,assignment_id,question_ref),
 CHECK((state='open' AND answer_trigger_id IS NULL AND resolved_at IS NULL)
    OR (state='answered' AND answer_trigger_id IS NOT NULL AND resolved_at IS NOT NULL)
    OR (state='dismissed' AND answer_trigger_id IS NULL AND resolved_at IS NOT NULL))
);
CREATE INDEX attentions_ticket_state ON attentions(ticket_id,state,created_at);
CREATE TRIGGER attentions_identity_immutable
BEFORE UPDATE OF attention_id,ticket_id,mission_id,trigger_id,assignment_id,question_ref,question_text,options_json,created_at
ON attentions BEGIN
 SELECT RAISE(ABORT,'attention identity is immutable');
END;
"""
