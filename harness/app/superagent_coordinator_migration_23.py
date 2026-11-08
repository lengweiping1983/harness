"""远程设备与 Mission 租约同库登记，失联不能换设备重放未知任务。"""
MIGRATION_23 = """
CREATE TABLE remote_devices (
 device_id TEXT PRIMARY KEY, identity_hash TEXT NOT NULL, endpoint TEXT NOT NULL,
 capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 256), created_at TEXT NOT NULL
);
CREATE TRIGGER remote_device_immutable BEFORE UPDATE ON remote_devices
BEGIN SELECT RAISE(ABORT,'remote device identity is immutable'); END;
CREATE TRIGGER remote_device_no_delete BEFORE DELETE ON remote_devices
BEGIN SELECT RAISE(ABORT,'remote device history cannot be deleted'); END;
CREATE TABLE remote_mission_leases (
 mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id),
 device_id TEXT NOT NULL REFERENCES remote_devices(device_id),
 generation INTEGER NOT NULL CHECK(generation>=1),
 state TEXT NOT NULL CHECK(state IN ('active','revoked')),
 expires_at INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TRIGGER remote_lease_initial BEFORE INSERT ON remote_mission_leases
WHEN NEW.generation<>1 OR NEW.state<>'active'
BEGIN SELECT RAISE(ABORT,'remote lease must start active'); END;
CREATE TRIGGER remote_lease_fenced BEFORE UPDATE ON remote_mission_leases
WHEN NEW.mission_id IS NOT OLD.mission_id OR NEW.device_id IS NOT OLD.device_id
 OR NEW.created_at IS NOT OLD.created_at OR NEW.generation<>OLD.generation
 OR OLD.state='revoked' OR NEW.expires_at<OLD.expires_at
BEGIN SELECT RAISE(ABORT,'remote lease identity or generation is fenced'); END;
CREATE TRIGGER remote_lease_no_delete BEFORE DELETE ON remote_mission_leases
BEGIN SELECT RAISE(ABORT,'remote lease history cannot be deleted'); END;
"""
