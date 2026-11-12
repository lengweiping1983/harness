"""Pure policy tests for bounded, fair coordinator DAG scheduling."""

import unittest

from harness.app.superagent_scheduler import CoordinatorScheduler


class CoordinatorSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = CoordinatorScheduler(max_active_assignments=4, max_active_per_mission=2)

    def test_dependencies_unlock_only_after_every_parent_is_done(self):
        jobs = [
            {"node_id": "source-a", "role": "work", "state": "done"},
            {"node_id": "source-b", "role": "work", "state": "running"},
            {"node_id": "join", "role": "work", "state": "pending"},
            {"node_id": "other", "role": "work", "state": "pending"},
        ]
        dependencies = [
            {"node_id": "join", "depends_on_node_id": "source-a", "requires_delivery": True},
            {"node_id": "join", "depends_on_node_id": "source-b", "requires_delivery": False},
        ]
        self.assertEqual(
            [job["node_id"] for job in self.scheduler.select_ready_jobs(jobs, dependencies)],
            ["other"],
        )
        jobs[1]["state"] = "done"
        self.assertEqual(
            [job["node_id"] for job in self.scheduler.select_ready_jobs(jobs, dependencies)],
            ["join", "other"],
        )

    def test_stage_filter_selects_only_allowed_role_and_resumes_checkpointed_jobs(self):
        jobs = [
            {"node_id": "work", "role": "work", "state": "done"},
            {"node_id": "integrate", "role": "integrate", "state": "done"},
            {"node_id": "review", "role": "review", "state": "checkpointed"},
        ]
        dependencies = [
            {"node_id": "integrate", "depends_on_node_id": "work", "requires_delivery": True},
            {"node_id": "review", "depends_on_node_id": "integrate", "requires_delivery": True},
        ]
        self.assertEqual(
            [job["node_id"] for job in self.scheduler.select_ready_jobs(
                jobs, dependencies, allowed_roles={"review"},
            )],
            ["review"],
        )
        self.assertEqual(self.scheduler.select_ready_jobs(jobs, dependencies), [])

    def test_dispatchable_filter_excludes_jobs_with_unsettled_assignments(self):
        jobs = [
            {"node_id": "reserved", "role": "work", "state": "ready"},
            {"node_id": "free", "role": "work", "state": "pending"},
            {"node_id": "review", "role": "review", "state": "pending"},
        ]
        selected = self.scheduler.select_ready_jobs(
            jobs, [], dispatchable_node_ids={"free", "review"},
        )
        self.assertEqual([job["node_id"] for job in selected], ["free"])

    def test_global_and_per_mission_capacity_apply_backpressure(self):
        self.assertEqual(
            self.scheduler.available_slots(
                active_assignments=1, mission_active_assignments=1, requested=4,
            ),
            1,
        )
        self.assertEqual(
            self.scheduler.available_slots(
                active_assignments=4, mission_active_assignments=0, requested=4,
            ),
            0,
        )
        self.assertEqual(
            self.scheduler.available_slots(
                active_assignments=0, mission_active_assignments=2, requested=4,
            ),
            0,
        )

    def test_mission_order_rotates_between_work_integration_and_review(self):
        missions = [
            {"mission_id": "a", "state": "running"},
            {"mission_id": "b", "state": "reviewing"},
            {"mission_id": "c", "state": "integrating"},
        ]
        self.assertEqual(
            [item["mission_id"] for item in self.scheduler.order_missions(missions)],
            ["a", "b", "c"],
        )
        self.assertEqual(
            [item["mission_id"] for item in self.scheduler.order_missions(missions)],
            ["b", "c", "a"],
        )


if __name__ == "__main__":
    unittest.main()
