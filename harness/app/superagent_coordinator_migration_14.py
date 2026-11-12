"""统一协调模型的执行资源、文件交付、输入投递和控制账本。"""

MIGRATION_14 = """
DROP TRIGGER assignments_worker_ready;
DROP TRIGGER workers_identity_immutable;
DROP TRIGGER workers_session_requires_workspace;
DROP INDEX workers_ticket_state;
CREATE TABLE workers_scoped (
 worker_id TEXT PRIMARY KEY CHECK(length(trim(worker_id))>0),
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE RESTRICT,
 mission_id TEXT REFERENCES missions(mission_id) ON DELETE RESTRICT,
 job_id TEXT REFERENCES jobs(job_id) ON DELETE RESTRICT,
 stage INTEGER NOT NULL DEFAULT 0 CHECK(stage>=0),
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
 updated_at TEXT NOT NULL,
 CHECK((mission_id IS NULL AND job_id IS NULL AND stage=0)
    OR (mission_id IS NOT NULL AND job_id IS NOT NULL AND stage>=1)),
 UNIQUE(job_id,stage)
);
INSERT INTO workers_scoped(worker_id,ticket_id,profile_id,repo,base_commit,runtime_instance_id,
 workspace_id,session_id,state,identity_hash,created_at,last_seen_at,aggregate_revision,updated_at)
 SELECT worker_id,ticket_id,profile_id,repo,base_commit,runtime_instance_id,
 workspace_id,session_id,state,identity_hash,created_at,last_seen_at,aggregate_revision,updated_at
 FROM workers;
DROP TABLE workers;
ALTER TABLE workers_scoped RENAME TO workers;
CREATE INDEX workers_ticket_state ON workers(ticket_id,state,profile_id);
CREATE INDEX workers_mission_job ON workers(mission_id,job_id,stage);
CREATE TRIGGER workers_scope_valid BEFORE INSERT ON workers
WHEN NEW.job_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM jobs AS job JOIN missions AS mission ON mission.mission_id=job.mission_id
 WHERE job.job_id=NEW.job_id AND job.mission_id=NEW.mission_id
 AND mission.ticket_id=NEW.ticket_id AND mission.repo=NEW.repo AND mission.base_commit=NEW.base_commit
 AND job.profile_id=NEW.profile_id
) BEGIN SELECT RAISE(ABORT,'worker execution scope does not match its frozen mission'); END;
CREATE TRIGGER workers_identity_immutable
BEFORE UPDATE OF worker_id,ticket_id,mission_id,job_id,stage,profile_id,repo,base_commit,
 runtime_instance_id,identity_hash,created_at ON workers
BEGIN SELECT RAISE(ABORT,'worker identity is immutable'); END;
CREATE TRIGGER workers_session_requires_workspace BEFORE UPDATE OF session_id ON workers
WHEN NEW.session_id IS NOT NULL AND NEW.workspace_id IS NULL
BEGIN SELECT RAISE(ABORT,'worker session requires a workspace'); END;
CREATE TRIGGER assignments_worker_ready BEFORE INSERT ON assignments
WHEN NEW.worker_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM workers AS worker JOIN jobs AS job ON job.job_id=NEW.job_id
 JOIN missions AS mission ON mission.mission_id=job.mission_id
 WHERE worker.worker_id=NEW.worker_id AND worker.state='ready'
 AND worker.ticket_id=mission.ticket_id AND worker.session_id=NEW.session_id
 AND worker.workspace_id=NEW.workspace_id
 AND (worker.job_id IS NULL OR (worker.job_id=job.job_id AND worker.mission_id=mission.mission_id))
) BEGIN SELECT RAISE(ABORT,'assignment requires its ready scoped worker'); END;

CREATE TABLE input_deliveries (
 delivery_id TEXT PRIMARY KEY,
 input_id TEXT NOT NULL REFERENCES ticket_inputs(input_id) ON DELETE RESTRICT,
 assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
 delivery_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('pending','submitting','accepted','applied','unapplied','unknown','rejected')),
 receipt_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(receipt_json)),
 aggregate_revision INTEGER NOT NULL DEFAULT 1 CHECK(aggregate_revision>=1),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(input_id,assignment_id)
);
CREATE TRIGGER input_deliveries_scope BEFORE INSERT ON input_deliveries
WHEN NOT EXISTS (
 SELECT 1 FROM ticket_inputs AS input JOIN assignments AS assignment ON assignment.assignment_id=NEW.assignment_id
 JOIN jobs AS job ON job.job_id=assignment.job_id JOIN missions AS mission ON mission.mission_id=job.mission_id
 WHERE input.input_id=NEW.input_id AND input.ticket_id=mission.ticket_id
) BEGIN SELECT RAISE(ABORT,'input delivery crosses ticket scope'); END;
CREATE TRIGGER input_deliveries_identity_immutable
BEFORE UPDATE OF delivery_id,input_id,assignment_id,delivery_key,created_at ON input_deliveries
BEGIN SELECT RAISE(ABORT,'input delivery identity is immutable'); END;

CREATE TABLE artifacts (
 artifact_id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
 workspace_id TEXT NOT NULL,
 repo TEXT NOT NULL,
 base_commit TEXT NOT NULL,
 commit_id TEXT NOT NULL,
 tree_hash TEXT NOT NULL,
 manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
 manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),
 receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)),
 created_at TEXT NOT NULL
);
CREATE TRIGGER artifacts_immutable_update BEFORE UPDATE ON artifacts
BEGIN SELECT RAISE(ABORT,'artifacts are immutable'); END;
CREATE TRIGGER artifacts_immutable_delete BEFORE DELETE ON artifacts
BEGIN SELECT RAISE(ABORT,'artifacts are immutable'); END;
CREATE TRIGGER artifacts_scope BEFORE INSERT ON artifacts WHEN NOT EXISTS (
 SELECT 1 FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id
 JOIN missions AS mission ON mission.mission_id=job.mission_id
 WHERE assignment.assignment_id=NEW.assignment_id AND assignment.workspace_id=NEW.workspace_id
 AND mission.repo=NEW.repo AND mission.base_commit=NEW.base_commit
) BEGIN SELECT RAISE(ABORT,'artifact does not match its original assignment'); END;
CREATE TABLE deliveries (
 delivery_id TEXT PRIMARY KEY,
 artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
 target_assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
 target_workspace_id TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('pending','planning','applying','applied','conflict','unknown','cancelled')),
 import_plan_json TEXT CHECK(import_plan_json IS NULL OR json_valid(import_plan_json)),
 receipt_json TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
 aggregate_revision INTEGER NOT NULL DEFAULT 1 CHECK(aggregate_revision>=1),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(artifact_id,target_assignment_id)
);
CREATE TRIGGER deliveries_identity_immutable
BEFORE UPDATE OF delivery_id,artifact_id,target_assignment_id,target_workspace_id,idempotency_key,created_at ON deliveries
BEGIN SELECT RAISE(ABORT,'delivery identity is immutable'); END;
CREATE TRIGGER deliveries_scope BEFORE INSERT ON deliveries WHEN NOT EXISTS (
 SELECT 1 FROM artifacts AS artifact JOIN assignments AS source ON source.assignment_id=artifact.assignment_id
 JOIN jobs AS source_job ON source_job.job_id=source.job_id
 JOIN assignments AS target ON target.assignment_id=NEW.target_assignment_id
 JOIN jobs AS target_job ON target_job.job_id=target.job_id
 WHERE artifact.artifact_id=NEW.artifact_id AND source_job.mission_id=target_job.mission_id
 AND target.workspace_id=NEW.target_workspace_id
) BEGIN SELECT RAISE(ABORT,'artifact delivery crosses mission scope'); END;
CREATE TRIGGER deliveries_plan_immutable BEFORE UPDATE OF import_plan_json ON deliveries
WHEN NEW.import_plan_json IS NOT OLD.import_plan_json
BEGIN SELECT RAISE(ABORT,'delivery plan is immutable'); END;
CREATE TRIGGER deliveries_applied_immutable BEFORE UPDATE OF state,receipt_json ON deliveries
WHEN OLD.state='applied' AND (NEW.state IS NOT OLD.state OR NEW.receipt_json IS NOT OLD.receipt_json)
BEGIN SELECT RAISE(ABORT,'applied delivery receipt is immutable'); END;
CREATE TABLE reviews (
 review_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
 reviewer_assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
 candidate_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
 plan_revision INTEGER NOT NULL,
 acceptance_hash TEXT NOT NULL CHECK(length(acceptance_hash)=64),
 verdict_json TEXT NOT NULL CHECK(json_valid(verdict_json)),
 evidence_set_hash TEXT NOT NULL CHECK(length(evidence_set_hash)=64),
 state TEXT NOT NULL CHECK(state IN ('passed','failed','invalid')),
 created_at TEXT NOT NULL,
 FOREIGN KEY(mission_id,plan_revision) REFERENCES mission_plans(mission_id,plan_revision) ON DELETE RESTRICT
);
CREATE TRIGGER reviews_immutable_update BEFORE UPDATE ON reviews
BEGIN SELECT RAISE(ABORT,'reviews are immutable'); END;
CREATE TRIGGER reviews_immutable_delete BEFORE DELETE ON reviews
BEGIN SELECT RAISE(ABORT,'reviews are immutable'); END;

CREATE TABLE mission_controls (
 mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id) ON DELETE RESTRICT,
 mode TEXT NOT NULL CHECK(mode IN ('running','paused','cancelled')),
 generation INTEGER NOT NULL CHECK(generation>=1),
 updated_at TEXT NOT NULL
);
CREATE TABLE control_commands (
 sequence INTEGER PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE,
 mission_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('pause','resume','cancel')),
 payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 state TEXT NOT NULL CHECK(state IN ('pending','applied','rejected','unknown')),
 receipt_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(receipt_json)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TRIGGER control_command_identity_immutable
BEFORE UPDATE OF request_id,mission_id,action,payload_hash,created_at ON control_commands
BEGIN SELECT RAISE(ABORT,'control command identity is immutable'); END;
CREATE TABLE mission_instructions (
 request_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
 job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
 assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
 run_id TEXT NOT NULL,
 turn_id INTEGER NOT NULL CHECK(turn_id>=1),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 state TEXT NOT NULL CHECK(state IN ('pending','submitting','accepted','unknown','rejected')),
 receipt_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(receipt_json)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TRIGGER mission_instruction_identity_immutable
BEFORE UPDATE OF request_id,mission_id,job_id,assignment_id,run_id,turn_id,payload_json,payload_hash,created_at ON mission_instructions
BEGIN SELECT RAISE(ABORT,'mission instruction identity is immutable'); END;
CREATE TABLE watchdog_snapshots (
 mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id) ON DELETE RESTRICT,
 fingerprint TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('healthy','stalled','blocked')),
 last_progress_at TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 aggregate_revision INTEGER NOT NULL DEFAULT 1 CHECK(aggregate_revision>=1)
);
INSERT INTO mission_controls(mission_id,mode,generation,updated_at)
 SELECT mission_id,'running',1,updated_at FROM missions;
CREATE TRIGGER missions_initialize_control AFTER INSERT ON missions
BEGIN
 INSERT INTO mission_controls(mission_id,mode,generation,updated_at)
 VALUES(NEW.mission_id,'running',1,NEW.created_at);
END;
"""
