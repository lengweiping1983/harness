"""节点指令的版本化回执与 Assignment 作用域约束。"""

MIGRATION_15 = """
ALTER TABLE mission_instructions
ADD COLUMN aggregate_revision INTEGER NOT NULL DEFAULT 1 CHECK(aggregate_revision>=1);
CREATE INDEX mission_instructions_mission_created
ON mission_instructions(mission_id,created_at,request_id);
CREATE INDEX mission_instructions_assignment_state
ON mission_instructions(assignment_id,state,created_at);
CREATE TRIGGER mission_instructions_scope
BEFORE INSERT ON mission_instructions
WHEN NOT EXISTS (
 SELECT 1 FROM assignments AS assignment
 JOIN jobs AS job ON job.job_id=assignment.job_id
 WHERE assignment.assignment_id=NEW.assignment_id
 AND assignment.job_id=NEW.job_id
 AND assignment.run_id=NEW.run_id
 AND job.mission_id=NEW.mission_id
)
BEGIN SELECT RAISE(ABORT,'mission instruction does not match its original assignment'); END;
"""
