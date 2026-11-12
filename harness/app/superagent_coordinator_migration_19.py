"""将 Watchdog 的账本进度证明绑定到数据库中的实际 Mission / Job / Assignment 事实。"""

MIGRATION_19 = """
ALTER TABLE watchdog_snapshots
ADD COLUMN ledger_facts_json TEXT
CHECK(ledger_facts_json IS NULL OR json_valid(ledger_facts_json));

CREATE VIEW watchdog_ledger_facts AS
SELECT mission.mission_id,
       json_object(
           'assignments', json((
               SELECT json_group_array(json_object(
                   'assignment_id', assignment.assignment_id,
                   'ended_at', assignment.ended_at,
                   'run_id', assignment.run_id,
                   'started_at', assignment.started_at
               ) ORDER BY assignment.assignment_id)
               FROM assignments AS assignment
               JOIN jobs AS job ON job.job_id=assignment.job_id
               WHERE job.mission_id=mission.mission_id
                 AND assignment.started_at IS NOT NULL
           )),
           'jobs', json((
               SELECT json_group_array(json_object(
                   'job_id', job.job_id,
                   'result_hash', job.result_hash,
                   'state', job.state
               ) ORDER BY job.job_id)
               FROM jobs AS job
               WHERE job.mission_id=mission.mission_id
                 AND (job.state IN ('done','checkpointed') OR job.result_hash IS NOT NULL)
           )),
           'mission', json_object(
               'mission_id', mission.mission_id,
               'plan_revision', mission.plan_revision,
               'round_no', mission.round_no
           )
       ) AS facts_json
FROM missions AS mission;

DROP TRIGGER watchdog_snapshots_insert_guard;
DROP TRIGGER watchdog_snapshots_transition_guard;

UPDATE watchdog_snapshots
SET ledger_fingerprint=NULL,
    ledger_facts_json=(
        SELECT facts_json
        FROM watchdog_ledger_facts
        WHERE mission_id=watchdog_snapshots.mission_id
    );

CREATE TRIGGER watchdog_snapshots_insert_guard
BEFORE INSERT ON watchdog_snapshots
WHEN NEW.aggregate_revision <> 1
 OR NEW.state <> 'healthy'
 OR length(NEW.fingerprint) <> 64
 OR NEW.fingerprint GLOB '*[^0-9a-f]*'
 OR NEW.ledger_fingerprint IS NULL
 OR NEW.ledger_fingerprint IS NOT superagent_sha256(NEW.ledger_facts_json)
 OR NEW.ledger_facts_json IS NULL
 OR NOT json_valid(NEW.ledger_facts_json)
 OR NEW.ledger_facts_json IS NOT (
    SELECT facts_json
    FROM watchdog_ledger_facts
    WHERE mission_id=NEW.mission_id
 )
 OR length(trim(NEW.last_progress_at)) = 0
 OR length(trim(NEW.observed_at)) = 0
 OR NEW.last_progress_at > NEW.observed_at
 OR NEW.activity_baseline_trusted NOT IN (0,1)
BEGIN SELECT RAISE(ABORT,'watchdog snapshot must begin with a healthy verified ledger baseline'); END;

CREATE TRIGGER watchdog_snapshots_transition_guard
BEFORE UPDATE ON watchdog_snapshots
WHEN NEW.aggregate_revision <> OLD.aggregate_revision + 1
 OR length(NEW.fingerprint) <> 64
 OR NEW.fingerprint GLOB '*[^0-9a-f]*'
 OR NEW.ledger_facts_json IS NULL
 OR NOT json_valid(NEW.ledger_facts_json)
 OR (
      NEW.ledger_facts_json IS NOT OLD.ledger_facts_json
      AND NEW.ledger_facts_json IS NOT (
          SELECT facts_json
          FROM watchdog_ledger_facts
          WHERE mission_id=NEW.mission_id
      )
 )
 OR (
      NEW.ledger_fingerprint IS NOT NULL
      AND NEW.ledger_fingerprint IS NOT superagent_sha256(NEW.ledger_facts_json)
 )
 OR (OLD.ledger_fingerprint IS NOT NULL AND NEW.ledger_fingerprint IS NULL)
 OR (
      OLD.ledger_fingerprint IS NOT NULL
      AND NEW.ledger_facts_json IS NOT OLD.ledger_facts_json
      AND NEW.ledger_fingerprint IS OLD.ledger_fingerprint
 )
 OR (
      OLD.ledger_fingerprint IS NOT NULL
      AND NEW.ledger_facts_json IS OLD.ledger_facts_json
      AND NEW.ledger_fingerprint IS NOT OLD.ledger_fingerprint
 )
 OR NEW.observed_at < OLD.observed_at
 OR NEW.last_progress_at < OLD.last_progress_at
 OR NEW.last_progress_at > NEW.observed_at
 OR NEW.activity_baseline_trusted < OLD.activity_baseline_trusted

 OR (
      NEW.ledger_facts_json IS NOT OLD.ledger_facts_json
      AND NEW.fingerprint = OLD.fingerprint
 )
 OR (
      NEW.fingerprint <> OLD.fingerprint
      AND (
           NEW.state <> 'healthy'
        OR NEW.last_progress_at <> NEW.observed_at
        OR NOT (
             (
                 NEW.ledger_facts_json IS NOT OLD.ledger_facts_json
                 AND NEW.ledger_fingerprint IS NOT OLD.ledger_fingerprint
             )
          OR (
                 NEW.ledger_facts_json IS OLD.ledger_facts_json
                 AND NEW.ledger_fingerprint IS NOT NULL
                 AND (OLD.ledger_fingerprint IS NULL
                      OR NEW.ledger_fingerprint IS OLD.ledger_fingerprint)
                 AND OLD.activity_baseline_trusted=1
                 AND NEW.activity_baseline_trusted=1
                 AND EXISTS (
                     SELECT 1
                     FROM watchdog_assignment_cursors AS cursor
                     WHERE cursor.mission_id=NEW.mission_id
                       AND cursor.last_progress_revision=NEW.aggregate_revision
                 )
             )
        )
      )
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
BEGIN SELECT RAISE(ABORT,'watchdog progress must match the current ledger facts and verified run cursors'); END;
"""
