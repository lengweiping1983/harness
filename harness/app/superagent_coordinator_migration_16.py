"""统一协调账本的交付、复核、控制、指令与监督状态守卫。"""

MIGRATION_16 = """
CREATE TRIGGER input_deliveries_transition_guard
BEFORE UPDATE ON input_deliveries
WHEN NEW.aggregate_revision <> OLD.aggregate_revision + 1
 OR NOT (
      (OLD.state='pending' AND NEW.state IN ('submitting','rejected'))
   OR (OLD.state='submitting' AND NEW.state IN ('accepted','applied','unapplied','unknown','rejected'))
   OR (OLD.state='accepted' AND NEW.state IN ('applied','unapplied'))
   OR (OLD.state='unknown' AND NEW.state IN ('accepted','applied','unapplied','rejected'))
 )
 OR (NEW.state IN ('pending','submitting') AND NEW.receipt_json <> '{}')
 OR (NEW.state NOT IN ('pending','submitting') AND (
        json_extract(NEW.receipt_json,'$.status') IS NOT NEW.state
     OR json_extract(NEW.receipt_json,'$.delivery_id') IS NOT NEW.delivery_id
     OR json_extract(NEW.receipt_json,'$.input_id') IS NOT NEW.input_id
     OR json_extract(NEW.receipt_json,'$.assignment_id') IS NOT NEW.assignment_id
     OR json_extract(NEW.receipt_json,'$.delivery_key') IS NOT NEW.delivery_key
 ))
 OR (NEW.state='accepted' AND json_extract(NEW.receipt_json,'$.native_receipt.status') IS NOT 'pending')
 OR (NEW.state='applied'
     AND json_extract(NEW.receipt_json,'$.native_receipt.status') IS NOT 'applied'
     AND json_extract(NEW.receipt_json,'$.native_receipt.source') IS NOT 'original_run_input_receipt')
 OR (NEW.state='unapplied' AND NOT (
        json_extract(NEW.receipt_json,'$.native_receipt.status') IS 'unapplied'
        OR (
            json_type(NEW.receipt_json,'$.native_receipt') IS 'null'
            AND json_type(NEW.receipt_json,'$.reason_code') IS 'text'
            AND length(json_extract(NEW.receipt_json,'$.reason_code')) BETWEEN 1 AND 96
        )
 ))
 OR (NEW.state='rejected' AND (
        json_extract(NEW.receipt_json,'$.native_receipt') IS NOT NULL
        OR json_type(NEW.receipt_json,'$.reason_code') IS NOT 'text'
        OR length(json_extract(NEW.receipt_json,'$.reason_code')) NOT BETWEEN 1 AND 96
 ))
BEGIN SELECT RAISE(ABORT,'input delivery state or revision is not monotonic'); END;

CREATE TRIGGER input_deliveries_immutable_delete
BEFORE DELETE ON input_deliveries
BEGIN SELECT RAISE(ABORT,'input delivery history is immutable'); END;

CREATE TRIGGER deliveries_dependency_scope
BEFORE INSERT ON deliveries
WHEN NOT EXISTS (
 SELECT 1
 FROM artifacts AS artifact
 JOIN assignments AS source_assignment ON source_assignment.assignment_id=artifact.assignment_id
 JOIN jobs AS source_job ON source_job.job_id=source_assignment.job_id
 JOIN assignments AS target_assignment ON target_assignment.assignment_id=NEW.target_assignment_id
 JOIN jobs AS target_job ON target_job.job_id=target_assignment.job_id
 WHERE artifact.artifact_id=NEW.artifact_id
 AND source_job.mission_id=target_job.mission_id
 AND source_job.plan_revision=target_job.plan_revision
 AND source_job.role IN ('work','integrate')
 AND (
      (source_job.job_id=target_job.job_id
       AND source_assignment.attempt_no < target_assignment.attempt_no)
      OR EXISTS (
          SELECT 1 FROM job_dependencies AS dependency
          WHERE dependency.job_id=target_job.job_id
          AND dependency.depends_on_job_id=source_job.job_id
          AND dependency.mission_id=source_job.mission_id
          AND dependency.plan_revision=source_job.plan_revision
          AND dependency.requires_delivery=1
      )
 )
)
BEGIN SELECT RAISE(ABORT,'artifact delivery requires a frozen dependency edge or later checkpoint assignment'); END;

CREATE TRIGGER deliveries_transition_guard
BEFORE UPDATE ON deliveries
WHEN NEW.aggregate_revision <> OLD.aggregate_revision + 1
 OR NOT (
      (OLD.state='pending' AND NEW.state IN ('planning','applying','applied','conflict','cancelled'))
   OR (OLD.state='planning' AND NEW.state IN ('applying','conflict','unknown','cancelled'))
   OR (OLD.state='applying' AND NEW.state IN ('applied','conflict','unknown'))
   OR (OLD.state='unknown' AND NEW.state IN ('applied','conflict','cancelled'))
 )
 OR (NEW.state='applied' AND NEW.receipt_json IS NULL)
BEGIN SELECT RAISE(ABORT,'artifact delivery state or revision is not monotonic'); END;

CREATE TRIGGER deliveries_immutable_delete
BEFORE DELETE ON deliveries
BEGIN SELECT RAISE(ABORT,'artifact delivery history is immutable'); END;

CREATE TRIGGER reviews_scope
BEFORE INSERT ON reviews
WHEN NOT EXISTS (
 SELECT 1
 FROM missions AS mission
 JOIN mission_plans AS plan
   ON plan.mission_id=mission.mission_id AND plan.plan_revision=NEW.plan_revision
 JOIN assignments AS reviewer
   ON reviewer.assignment_id=NEW.reviewer_assignment_id
 JOIN jobs AS reviewer_job ON reviewer_job.job_id=reviewer.job_id
 JOIN artifacts AS artifact ON artifact.artifact_id=NEW.candidate_artifact_id
 JOIN assignments AS source ON source.assignment_id=artifact.assignment_id
 JOIN jobs AS source_job ON source_job.job_id=source.job_id
 WHERE mission.mission_id=NEW.mission_id
 AND mission.plan_revision=NEW.plan_revision
 AND plan.sealed=1
 AND plan.acceptance_hash=NEW.acceptance_hash
 AND reviewer_job.mission_id=NEW.mission_id
 AND reviewer_job.plan_revision=NEW.plan_revision
 AND reviewer_job.role='review'
 AND source_job.mission_id=NEW.mission_id
 AND source_job.plan_revision=NEW.plan_revision
 AND source_job.role='integrate'
 AND reviewer.workspace_id IS NOT NULL
 AND reviewer.session_id IS NOT NULL
 AND source.workspace_id IS NOT NULL
 AND source.session_id IS NOT NULL
 AND reviewer.workspace_id <> source.workspace_id
 AND reviewer.session_id <> source.session_id
)
BEGIN SELECT RAISE(ABORT,'review requires the frozen mission candidate and an independent reviewer assignment'); END;

CREATE TRIGGER mission_instructions_transition_guard
BEFORE UPDATE ON mission_instructions
WHEN NEW.aggregate_revision <> OLD.aggregate_revision + 1
 OR NOT (
      (OLD.state='pending' AND NEW.state='submitting')
   OR (OLD.state='submitting' AND NEW.state IN ('accepted','unknown','rejected'))
   OR (OLD.state='unknown' AND NEW.state IN ('accepted','rejected'))
   OR (OLD.state='accepted' AND NEW.state='accepted'
       AND json_extract(OLD.receipt_json,'$.native_receipt.status')='pending'
       AND json_extract(NEW.receipt_json,'$.native_receipt.status') IN ('applied','unapplied'))
 )
 OR (NEW.state='submitting' AND NEW.receipt_json <> '{}')
 OR (NEW.state='accepted' AND (
        json_type(NEW.receipt_json,'$.version') IS NOT 'integer'
     OR json_extract(NEW.receipt_json,'$.version') <> 1
     OR json_extract(NEW.receipt_json,'$.state') IS NOT 'accepted'
     OR json_type(NEW.receipt_json,'$.reason_code') IS NOT 'null'
     OR json_type(NEW.receipt_json,'$.native_receipt') IS NOT 'object'
     OR json_type(NEW.receipt_json,'$.native_receipt.request_id') IS NOT 'text'
     OR json_extract(NEW.receipt_json,'$.native_receipt.request_id') IS NOT
        ('superagent-v2:instruction:' || NEW.request_id || ':' || NEW.assignment_id)
     OR json_type(NEW.receipt_json,'$.native_receipt.turn') IS NOT 'integer'
     OR json_extract(NEW.receipt_json,'$.native_receipt.turn') IS NOT NEW.turn_id
     OR json_type(NEW.receipt_json,'$.native_receipt.status') IS NOT 'text'
     OR json_extract(NEW.receipt_json,'$.native_receipt.status') NOT IN ('pending','applied','unapplied')
     OR json_type(NEW.receipt_json,'$.native_receipt.accepted_seq') IS NOT 'integer'
     OR json_extract(NEW.receipt_json,'$.native_receipt.accepted_seq') < 1
     OR (json_extract(NEW.receipt_json,'$.native_receipt.status')='applied' AND (
            json_type(NEW.receipt_json,'$.native_receipt.applied_seq') IS NOT 'integer'
         OR json_extract(NEW.receipt_json,'$.native_receipt.applied_seq') <=
            json_extract(NEW.receipt_json,'$.native_receipt.accepted_seq')
     ))
     OR (json_extract(NEW.receipt_json,'$.native_receipt.status')<>'applied'
         AND json_type(NEW.receipt_json,'$.native_receipt.applied_seq') IS NOT 'null')
 ))
 OR (NEW.state IN ('unknown','rejected') AND (
        json_type(NEW.receipt_json,'$.version') IS NOT 'integer'
     OR json_extract(NEW.receipt_json,'$.version') <> 1
     OR json_extract(NEW.receipt_json,'$.state') IS NOT NEW.state
     OR json_type(NEW.receipt_json,'$.reason_code') IS NOT 'text'
     OR length(json_extract(NEW.receipt_json,'$.reason_code')) NOT BETWEEN 1 AND 96
     OR json_type(NEW.receipt_json,'$.native_receipt') IS NOT 'null'
 ))
BEGIN SELECT RAISE(ABORT,'mission instruction state, receipt or revision is not monotonic'); END;

CREATE TRIGGER mission_instructions_immutable_delete
BEFORE DELETE ON mission_instructions
BEGIN SELECT RAISE(ABORT,'mission instruction history is immutable'); END;

CREATE TRIGGER mission_controls_insert_guard
BEFORE INSERT ON mission_controls
WHEN NEW.mode <> 'running' OR NEW.generation <> 1
BEGIN SELECT RAISE(ABORT,'mission control must start in generation one running mode'); END;

CREATE TRIGGER mission_controls_transition_guard
BEFORE UPDATE ON mission_controls
WHEN NEW.generation <> OLD.generation + 1
 OR (OLD.mode='cancelled' AND NEW.mode <> 'cancelled')
BEGIN SELECT RAISE(ABORT,'mission control generation cannot regress or resume after cancellation'); END;

CREATE TRIGGER mission_controls_immutable_delete
BEFORE DELETE ON mission_controls
BEGIN SELECT RAISE(ABORT,'mission control history is immutable'); END;

CREATE TRIGGER control_commands_terminal_immutable
BEFORE UPDATE OF state,receipt_json ON control_commands
WHEN OLD.state IN ('applied','rejected')
BEGIN SELECT RAISE(ABORT,'terminal control command receipt is immutable'); END;

CREATE TRIGGER control_commands_immutable_delete
BEFORE DELETE ON control_commands
BEGIN SELECT RAISE(ABORT,'control command history is immutable'); END;

CREATE TRIGGER watchdog_snapshots_insert_guard
BEFORE INSERT ON watchdog_snapshots
WHEN NEW.aggregate_revision <> 1
 OR NEW.state <> 'healthy'
 OR length(NEW.fingerprint) <> 64
 OR NEW.fingerprint GLOB '*[^0-9a-f]*'
 OR length(trim(NEW.last_progress_at)) = 0
 OR length(trim(NEW.observed_at)) = 0
 OR NEW.last_progress_at > NEW.observed_at
BEGIN SELECT RAISE(ABORT,'watchdog snapshot must begin with a healthy monotonic observation'); END;

CREATE TRIGGER watchdog_snapshots_transition_guard
BEFORE UPDATE ON watchdog_snapshots
WHEN NEW.aggregate_revision <> OLD.aggregate_revision + 1
 OR length(NEW.fingerprint) <> 64
 OR NEW.fingerprint GLOB '*[^0-9a-f]*'
 OR NEW.observed_at < OLD.observed_at
 OR NEW.last_progress_at < OLD.last_progress_at
 OR NEW.last_progress_at > NEW.observed_at
 OR (
      NEW.fingerprint <> OLD.fingerprint
      AND (NEW.state <> 'healthy' OR NEW.last_progress_at <> NEW.observed_at)
 )
 OR (
      NEW.fingerprint = OLD.fingerprint
      AND (
           NEW.last_progress_at <> OLD.last_progress_at
        OR NOT (
             (OLD.state='healthy' AND NEW.state IN ('healthy','stalled','blocked'))
          OR (OLD.state='stalled' AND NEW.state IN ('stalled','blocked'))
          OR (OLD.state='blocked' AND NEW.state='blocked')
        )
      )
 )
BEGIN SELECT RAISE(ABORT,'watchdog progress and escalation must be monotonic'); END;

CREATE TRIGGER watchdog_snapshots_immutable_delete
BEFORE DELETE ON watchdog_snapshots
BEGIN SELECT RAISE(ABORT,'watchdog observation history is immutable'); END;
"""
