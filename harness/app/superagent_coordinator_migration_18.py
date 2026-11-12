"""为 Watchdog 保存每个原生 Run 的单调进度游标。"""

MIGRATION_18 = """
ALTER TABLE watchdog_snapshots
ADD COLUMN ledger_fingerprint TEXT
CHECK(
    ledger_fingerprint IS NULL
    OR (
        length(ledger_fingerprint)=64
        AND ledger_fingerprint NOT GLOB '*[^0-9a-f]*'
    )
);

CREATE TABLE watchdog_assignment_cursors (
    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
    assignment_id TEXT NOT NULL UNIQUE REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL CHECK(length(trim(run_id))>0),
    session_id TEXT NOT NULL CHECK(length(trim(session_id))>0),
    last_event_seq INTEGER NOT NULL CHECK(last_event_seq>=-1),
    last_event_time TEXT,
    last_event_type TEXT,
    aggregate_revision INTEGER NOT NULL DEFAULT 1 CHECK(aggregate_revision>=1),
    last_progress_revision INTEGER NOT NULL DEFAULT 0 CHECK(last_progress_revision>=0),
    PRIMARY KEY(mission_id,assignment_id),
    CHECK(
        (last_event_seq=-1 AND last_event_time IS NULL AND last_event_type IS NULL)
        OR (
            last_event_seq>=0
            AND length(trim(last_event_time))>0
            AND length(trim(last_event_type))>0
        )
    )
);

CREATE TRIGGER watchdog_assignment_cursors_insert_guard
BEFORE INSERT ON watchdog_assignment_cursors
WHEN NEW.aggregate_revision <> 1
 OR NEW.last_progress_revision <> 0
 OR NOT EXISTS (
    SELECT 1
    FROM assignments AS assignment
    JOIN jobs AS job ON job.job_id=assignment.job_id
    WHERE assignment.assignment_id=NEW.assignment_id
      AND job.mission_id=NEW.mission_id
      AND assignment.run_id=NEW.run_id
      AND assignment.session_id=NEW.session_id
      AND assignment.state IN ('submitting','accepted','running','capturing','stopping','unknown')
 )
BEGIN SELECT RAISE(ABORT,'watchdog cursor must bind an active original assignment and run'); END;

CREATE TRIGGER watchdog_assignment_cursors_transition_guard
BEFORE UPDATE ON watchdog_assignment_cursors
WHEN NEW.aggregate_revision <> OLD.aggregate_revision + 1
 OR NEW.mission_id <> OLD.mission_id
 OR NEW.assignment_id <> OLD.assignment_id
 OR NEW.run_id <> OLD.run_id
 OR NEW.session_id <> OLD.session_id
 OR NEW.last_event_seq <= OLD.last_event_seq
 OR NEW.last_progress_revision <> (
    SELECT aggregate_revision + 1
    FROM watchdog_snapshots
    WHERE mission_id=OLD.mission_id
 )
 OR NOT EXISTS (
    SELECT 1
    FROM assignments AS assignment
    JOIN jobs AS job ON job.job_id=assignment.job_id
    WHERE assignment.assignment_id=NEW.assignment_id
      AND job.mission_id=NEW.mission_id
      AND assignment.run_id=NEW.run_id
      AND assignment.session_id=NEW.session_id
      AND assignment.state IN ('submitting','accepted','running','capturing','stopping','unknown')
 )
 OR (
    NEW.last_event_seq>=0
    AND (
        length(trim(NEW.last_event_time))=0
        OR length(trim(NEW.last_event_type))=0
    )
 )
BEGIN SELECT RAISE(ABORT,'watchdog run cursor must advance monotonically for its original active run'); END;

CREATE TRIGGER watchdog_assignment_cursors_immutable_delete
BEFORE DELETE ON watchdog_assignment_cursors
BEGIN SELECT RAISE(ABORT,'watchdog run cursor history is immutable'); END;

DROP TRIGGER watchdog_snapshots_insert_guard;
CREATE TRIGGER watchdog_snapshots_insert_guard
BEFORE INSERT ON watchdog_snapshots
WHEN NEW.aggregate_revision <> 1
 OR NEW.state <> 'healthy'
 OR length(NEW.fingerprint) <> 64
 OR NEW.fingerprint GLOB '*[^0-9a-f]*'
 OR length(NEW.ledger_fingerprint) <> 64
 OR NEW.ledger_fingerprint GLOB '*[^0-9a-f]*'
 OR length(trim(NEW.last_progress_at)) = 0
 OR length(trim(NEW.observed_at)) = 0
 OR NEW.last_progress_at > NEW.observed_at
 OR NEW.activity_baseline_trusted NOT IN (0,1)
BEGIN SELECT RAISE(ABORT,'watchdog snapshot must begin with a healthy monotonic observation'); END;

DROP TRIGGER watchdog_snapshots_transition_guard;
CREATE TRIGGER watchdog_snapshots_transition_guard
BEFORE UPDATE ON watchdog_snapshots
WHEN NEW.aggregate_revision <> OLD.aggregate_revision + 1
 OR length(NEW.fingerprint) <> 64
 OR NEW.fingerprint GLOB '*[^0-9a-f]*'
 OR (
      NEW.ledger_fingerprint IS NOT NULL
      AND (
           length(NEW.ledger_fingerprint) <> 64
        OR NEW.ledger_fingerprint GLOB '*[^0-9a-f]*'
      )
 )
 OR (OLD.ledger_fingerprint IS NOT NULL AND NEW.ledger_fingerprint IS NULL)
 OR NEW.observed_at < OLD.observed_at
 OR NEW.last_progress_at < OLD.last_progress_at
 OR NEW.last_progress_at > NEW.observed_at
 OR NEW.activity_baseline_trusted < OLD.activity_baseline_trusted
 OR (
      OLD.ledger_fingerprint IS NULL
      AND NEW.ledger_fingerprint IS NOT NULL
      AND (
           NEW.fingerprint <> OLD.fingerprint
        OR NEW.last_progress_at <> OLD.last_progress_at
      )
 )
 OR (
      OLD.ledger_fingerprint IS NOT NULL
      AND NEW.ledger_fingerprint <> OLD.ledger_fingerprint
      AND NEW.fingerprint = OLD.fingerprint
 )
 OR (
      NEW.fingerprint <> OLD.fingerprint
      AND (
           NEW.state <> 'healthy'
        OR NEW.last_progress_at <> NEW.observed_at
        OR NOT (
             (
                 OLD.ledger_fingerprint IS NOT NULL
                 AND NEW.ledger_fingerprint IS NOT NULL
                 AND NEW.ledger_fingerprint <> OLD.ledger_fingerprint
             )
          OR (
                 OLD.ledger_fingerprint IS NOT NULL
                 AND NEW.ledger_fingerprint = OLD.ledger_fingerprint
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
BEGIN SELECT RAISE(ABORT,'watchdog progress, ledger baseline, and run cursors must be monotonic'); END;
"""
