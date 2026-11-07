"""Bounded, fair DAG scheduling policy for the V2 coordinator."""

from __future__ import annotations


class CoordinatorScheduler:
    """Select stage-ready DAG jobs without crossing mission or runtime limits.

    Durable state, authorization, native Run submission, and artifact effects
    remain owned by the coordinator.
    """

    _SCHEDULABLE_MISSION_STATES = frozenset({"planning", "running", "integrating", "reviewing"})
    _SCHEDULABLE_JOB_STATES = frozenset({"pending", "ready", "checkpointed"})
    _ROLES = frozenset({"work", "integrate", "review"})

    def __init__(self, *, max_active_assignments: int = 4, max_active_per_mission: int = 2):
        if type(max_active_assignments) is not int or max_active_assignments < 1:
            raise ValueError("max_active_assignments must be a positive integer")
        if type(max_active_per_mission) is not int or max_active_per_mission < 1:
            raise ValueError("max_active_per_mission must be a positive integer")
        self.max_active_assignments = max_active_assignments
        self.max_active_per_mission = max_active_per_mission
        self._mission_cursor = 0

    def order_missions(self, missions: list[dict]) -> list[dict]:
        """Rotate scheduling opportunities across every active execution stage."""
        eligible = [
            mission for mission in missions
            if mission.get("state") in self._SCHEDULABLE_MISSION_STATES
        ]
        if not eligible:
            self._mission_cursor = 0
            return []
        start = self._mission_cursor % len(eligible)
        ordered = eligible[start:] + eligible[:start]
        self._mission_cursor = (start + 1) % len(eligible)
        return ordered

    def available_slots(
        self,
        *,
        active_assignments: int,
        mission_active_assignments: int,
        requested: int,
    ) -> int:
        for name, value in (
            ("active_assignments", active_assignments),
            ("mission_active_assignments", mission_active_assignments),
            ("requested", requested),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        return max(
            0,
            min(
                requested,
                self.max_active_assignments - active_assignments,
                self.max_active_per_mission - mission_active_assignments,
            ),
        )

    def select_ready_jobs(
        self,
        jobs: list[dict],
        dependencies: list[dict],
        *,
        dispatchable_node_ids: set[str] | None = None,
        allowed_roles: set[str] | None = None,
    ) -> list[dict]:
        """Return stage-allowed jobs whose complete dependency set is done.

        Delivery dependencies are not inferred from parent completion: the
        execution layer confirms the frozen Artifact and applied Delivery
        before a child Job becomes ready.
        """
        roles = {"work"} if allowed_roles is None else set(allowed_roles)
        if not roles or not roles <= self._ROLES:
            raise ValueError("allowed_roles must contain supported execution roles")
        by_node = {
            job.get("node_id"): job
            for job in jobs
            if isinstance(job.get("node_id"), str)
        }
        parents_by_node: dict[str, list[str]] = {}
        for edge in dependencies:
            child = edge.get("node_id")
            parent = edge.get("depends_on_node_id")
            if not isinstance(child, str) or not isinstance(parent, str):
                continue
            parents_by_node.setdefault(child, []).append(parent)

        ready = []
        for job in jobs:
            node_id = job.get("node_id")
            if job.get("role") not in roles or job.get("state") not in self._SCHEDULABLE_JOB_STATES:
                continue
            if not isinstance(node_id, str):
                continue
            if dispatchable_node_ids is not None and node_id not in dispatchable_node_ids:
                continue
            parents = parents_by_node.get(node_id, [])
            if any(by_node.get(parent, {}).get("state") != "done" for parent in parents):
                continue
            ready.append(job)
        return ready


__all__ = ["CoordinatorScheduler"]
