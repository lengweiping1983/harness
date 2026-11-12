"""为 Watchdog 持久化原生活动基线的可信状态。"""

MIGRATION_17 = """
ALTER TABLE watchdog_snapshots
ADD COLUMN activity_baseline_trusted INTEGER NOT NULL DEFAULT 0
CHECK(activity_baseline_trusted IN (0,1));

DROP TRIGGER watchdog_snapshots_insert_guard;
CREATE TRIGGER watchdog_snapshots_insert_guard
BEFORE INSERT ON watchdog_snapshots
WHEN NEW.aggregate_revision <> 1
 OR NEW.state <> 'healthy'
 OR length(NEW.fingerprint) <> 64
 OR NEW.fingerprint GLOB '*[^0-9a-f]*'
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
 OR NEW.observed_at < OLD.observed_at
 OR NEW.last_progress_at < OLD.last_progress_at
 OR NEW.last_progress_at > NEW.observed_at
 OR NEW.activity_baseline_trusted < OLD.activity_baseline_trusted
 OR (
      OLD.activity_baseline_trusted=0
      AND NEW.activity_baseline_trusted=1
      AND (
           NEW.last_progress_at <> OLD.last_progress_at
        OR NOT (
             (OLD.state='healthy' AND NEW.state IN ('healthy','stalled','blocked'))
          OR (OLD.state='stalled' AND NEW.state IN ('stalled','blocked'))
          OR (OLD.state='blocked' AND NEW.state='blocked')
        )
      )
 )
 OR (
      OLD.activity_baseline_trusted=0
      AND NEW.activity_baseline_trusted=0
      AND (
           NEW.fingerprint <> OLD.fingerprint
        OR NEW.last_progress_at <> OLD.last_progress_at
        OR NOT (
             (OLD.state='healthy' AND NEW.state IN ('healthy','stalled','blocked'))
          OR (OLD.state='stalled' AND NEW.state IN ('stalled','blocked'))
          OR (OLD.state='blocked' AND NEW.state='blocked')
        )
      )
 )
 OR (
      OLD.activity_baseline_trusted=1
      AND NEW.activity_baseline_trusted=1
      AND (
           (NEW.fingerprint <> OLD.fingerprint
            AND (NEW.state <> 'healthy' OR NEW.last_progress_at <> NEW.observed_at))
        OR (NEW.fingerprint = OLD.fingerprint
            AND (
                 NEW.last_progress_at <> OLD.last_progress_at
              OR NOT (
                   (OLD.state='healthy' AND NEW.state IN ('healthy','stalled','blocked'))
                OR (OLD.state='stalled' AND NEW.state IN ('stalled','blocked'))
                OR (OLD.state='blocked' AND NEW.state='blocked')
              )
            ))
      )
 )
BEGIN SELECT RAISE(ABORT,'watchdog progress and trusted baseline must be monotonic'); END;
"""
