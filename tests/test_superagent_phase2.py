from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from threading import Barrier, Thread

from harness.core.errors import HarnessError
from harness.app.superagent_coordinator_host import CoordinatorHost
from harness.app.superagent_coordinator_store import _hash, _json
from harness.app.superagent_source_registry import (
    LocalSourceKind, NormalizedThread, NormalizedTrigger, SourceRegistry, ThreadLifecycleUpdate,
)


class CustomLocalSourceKind(LocalSourceKind):
    kind = "local-custom"


class CustomLocalSourceKindV2(CustomLocalSourceKind):
    api_version = 2


class SuperagentPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.secrets = {
            "secret://github/repo": b"github-webhook-secret",
            "secret://feishu/token": b"feishu-verification-token",
            "secret://feishu/encrypt": b"feishu-encrypt-key-32-bytes-long",
        }
        self.host = CoordinatorHost.open(
            self.root / "v2", instance_id="phase2-tests",
            identity={"project": "harness"},
            secret_resolver=lambda reference: self.secrets[reference],
            local_identity_verifier=self._local_identity,
        )
        self.host.sources.create_instance(
            "local-source", kind="local", config={"namespace": "repo"},
        )

    def tearDown(self):
        self.host.close()
        self.temp.cleanup()

    @staticmethod
    def _local_identity(event):
        metadata = event.get("metadata", {})
        return {
            "actor_id": metadata.get("actor_id", "human:1"),
            "actor_kind": metadata.get("actor_kind", "human"),
            "addressed": metadata.get("addressed", "direct"),
        }

    def policy(self, *, mode="auto", allowed=("human:1",), scope=None):
        source = {"allowed": [{"id": actor} for actor in allowed]}
        if scope is not None:
            source["scope"] = scope
        return {
            "mode": mode,
            "timeout_seconds": 120,
            "rules": [{
                "id": "implementation", "name": "Implementation",
                "enabled": True, "instruction": "Only accept a settled implementation request.",
            }],
            "sources": {"local-source": source},
        }

    def publish(self, **kwargs):
        return self.host.authorization.publish_policy(
            self.policy(**kwargs), actor={"kind": "human", "id": "policy-admin"},
        )

    def receive(self, event_id="event-1", thread_id="thread-1", text="Fix the existing typo.", **metadata):
        return self.host.sources.receive_local(
            "local-source",
            {
                "event_id": event_id, "thread_id": thread_id, "text": text,
                "occurred_at": "2026-11-04T12:00:00Z",
                "metadata": {"actor_id": "human:1", "actor_kind": "human", "addressed": "direct", **metadata},
            },
        )

    @staticmethod
    def accept_proposal(evidence="Fix the existing typo."):
        return {
            "kind": "accept",
            "reason": "An explicit implementation request with a settled target.",
            "rule_assessments": [{
                "rule_id": "implementation", "matched": True,
                "reason": "The request asks for a concrete existing behavior fix.",
            }],
            "direction": {
                "implementation_requested": True, "target_basis": "specified",
                "requires_design_decision": False, "evidence": evidence,
            },
            "title": "Fix the existing typo",
            "intent_evidence": evidence,
        }

    def accept_local_event(self, *, event_id="origin", thread_id="work-thread", scope=None):
        self.publish(scope=scope)
        received = self.receive(event_id=event_id, thread_id=thread_id)
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        return received, candidate, accepted

    def end_ticket_by_system(self, ticket_id):
        with self.host._store.transaction(write=True) as db:
            ticket = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            self.host._authorization_service._ticket_update(
                db, ticket,
                {
                    "state": "ended", "outcome": "completed", "ended_by": "system",
                    "ended_at": "2026-11-04T12:01:00Z",
                },
                event_type="ticket.completed",
                payload={"outcome": "completed"},
                actor={"kind": "system"},
            )

    def accept_custom_source(self, plugin):
        unregister = self.host.sources.register(plugin)
        self.host.sources.create_instance(
            "custom-source", kind="local-custom", config={"namespace": "custom"},
        )
        policy = self.policy()
        policy["sources"]["custom-source"] = policy["sources"].pop("local-source")
        self.host.authorization.publish_policy(
            policy, actor={"kind": "human", "id": "policy-admin"},
        )
        text = "Fix the custom source issue."
        received = self.host.sources.receive_local(
            "custom-source",
            {
                "event_id": "custom-origin", "thread_id": "custom-thread", "text": text,
                "occurred_at": "2026-11-04T12:00:00Z",
                "metadata": {"actor_id": "human:1", "actor_kind": "human", "addressed": "direct"},
            },
        )
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(evidence=text),
            actor={"kind": "assessor"},
        )
        self.assertEqual(accepted["kind"], "accept")
        return unregister, accepted

    def test_accept_requires_both_evidence_fields_to_quote_admitted_source_text(self):
        self.publish()
        mutations = (
            lambda proposal: proposal["direction"].update({"evidence": "Fabricated implementation request"}),
            lambda proposal: proposal.update({"intent_evidence": "Fabricated implementation request"}),
        )
        for index, mutate in enumerate(mutations):
            received = self.receive(
                event_id=f"un grounded-evidence-{index}",
                thread_id=f"un grounded-evidence-{index}",
            )
            candidate = self.host.authorization.begin_evaluation(
                received["thread_ids"][0], actor={"kind": "host"},
            )
            proposal = self.accept_proposal()
            mutate(proposal)
            result = self.host.authorization.complete_evaluation(
                candidate["evaluation_id"], proposal, actor={"kind": "assessor"},
            )
            self.assertEqual(result["kind"], "ignore")
            self.assertIsNone(result["ticket_id"])
            decision = self.host.authorization.decision(result["decision_id"])
            self.assertEqual(decision["audit"]["rejected_accept"], "evidence_not_grounded")

    def test_grounded_source_quote_allows_ticket_mandate_creation(self):
        self.publish()
        received = self.receive(event_id="grounded-acceptance")
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        result = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(),
            actor={"kind": "assessor"},
        )
        self.assertEqual(result["kind"], "accept")
        self.assertIsNotNone(self.host.authorization.mandate(result["mandate_id"]))
        self.assertEqual(self.host.authorization.ticket(result["ticket_id"])["state"], "open")

    def test_local_source_accept_creates_immutable_mandate_and_ticket(self):
        received, candidate, result = self.accept_local_event()
        self.assertEqual(received["state"], "stored")
        self.assertEqual(candidate["state"], "running")
        self.assertEqual(candidate["strength"], "explicit")
        self.assertEqual(result["kind"], "accept")
        ticket = self.host.authorization.ticket(result["ticket_id"])
        self.assertEqual(ticket["state"], "open")
        self.assertEqual(ticket["mandate"]["strength"], "explicit")
        self.assertEqual(ticket["mandate"]["policy_version"], candidate["policy_version"])
        self.assertEqual(len(ticket["subscriptions"]), 1)
        self.assertEqual(ticket["subscriptions"][0]["mode"], "work")
        self.assertEqual(ticket["inputs"][0]["context_only"], 0)
        self.assertEqual(
            self.host.authorization.revalidate_mandate(result["mandate_id"], actor={"kind": "host"})["status"],
            "valid",
        )
        with self.host._store.transaction(write=True) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE mandates SET scope_json='{}' WHERE mandate_id=?", (result["mandate_id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE decisions SET kind='ignore' WHERE decision_id=?", (result["decision_id"],))

    def test_bot_origin_ticket_is_context_only_not_work_fanout(self):
        policy = self.policy()
        policy["sources"]["local-source"]["bots"] = [{"id": "bot:1"}]
        self.host.authorization.publish_policy(
            policy, actor={"kind": "human", "id": "policy-admin"},
        )
        received = self.receive(
            event_id="bot-origin", thread_id="bot-thread",
            actor_id="bot:1", actor_kind="bot", addressed="none",
        )
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual(candidate["strength"], "inferred")
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        self.assertEqual(accepted["kind"], "accept")
        trigger = self.host.sources.get_trigger(received["trigger_id"])
        self.assertEqual(trigger["disposition"]["state"], "ticket.context_only")
        self.assertEqual(trigger["disposition"]["fanout_ticket_ids"], [])
        self.assertEqual(trigger["disposition"]["context_ticket_ids"], [accepted["ticket_id"]])
        ticket = self.host.authorization.ticket(accepted["ticket_id"])
        self.assertEqual(ticket["inputs"][0]["context_only"], 1)

    def test_context_only_bot_followup_cannot_reopen_ticket(self):
        policy = self.policy()
        policy["sources"]["local-source"]["bots"] = [{"id": "bot:1"}]
        self.host.authorization.publish_policy(
            policy, actor={"kind": "human", "id": "policy-admin"},
        )
        origin = self.receive(event_id="reopen-context-origin", thread_id="reopen-context-thread")
        candidate = self.host.authorization.begin_evaluation(
            origin["thread_ids"][0], actor={"kind": "host"},
        )
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        followup = self.receive(
            event_id="reopen-context-bot", thread_id="reopen-context-thread",
            actor_id="bot:1", actor_kind="bot", addressed="none",
        )
        self.assertEqual(followup["disposition"], "evaluation.queued")
        self.assertEqual(followup["context_ticket_ids"], [accepted["ticket_id"]])
        self.assertEqual(
            self.host.authorization.ticket(accepted["ticket_id"])["inputs"][-1]["context_only"], 1,
        )
        self.end_ticket_by_system(accepted["ticket_id"])

        reopen_candidate = self.host.authorization.begin_evaluation(
            origin["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual(reopen_candidate["strength"], "inferred")
        proposal = self.accept_proposal()
        proposal["kind"] = "reopen"
        proposal["ticket_id"] = accepted["ticket_id"]
        with self.assertRaises(PermissionError):
            self.host.authorization.complete_evaluation(
                reopen_candidate["evaluation_id"], proposal, actor={"kind": "assessor"},
            )
        self.assertEqual(self.host.authorization.ticket(accepted["ticket_id"])["state"], "ended")

    def test_human_work_followup_resumes_nonhuman_ended_ticket(self):
        _, _, accepted = self.accept_local_event(
            event_id="reopen-human-origin", thread_id="reopen-human-thread",
        )
        self.end_ticket_by_system(accepted["ticket_id"])

        followup = self.receive(
            event_id="reopen-human-followup", thread_id="reopen-human-thread",
        )
        self.assertEqual(followup["disposition"], "ticket.fanout")
        ticket = self.host.authorization.ticket(accepted["ticket_id"])
        self.assertEqual(ticket["state"], "open")
        self.assertEqual(ticket["inputs"][-1]["context_only"], 0)

    def test_source_trigger_dedup_conflict_and_thread_namespaces(self):
        self.publish()
        first = self.receive(event_id="same-event", thread_id="thread-1", text="Same body")
        duplicate = self.receive(event_id="same-event", thread_id="thread-1", text="Same body")
        self.assertEqual(duplicate["state"], "duplicate")
        self.assertEqual(first["trigger_id"], duplicate["trigger_id"])
        with self.assertRaises(ValueError):
            self.receive(event_id="same-event", thread_id="thread-1", text="Different body")

        trigger = self.host.sources.get_trigger(first["trigger_id"])
        self.assertEqual(trigger["external_event_id"], "same-event")
        self.assertEqual(trigger["body"]["text"], "Same body")
        self.assertEqual(trigger["threads"], [{"thread_id": first["thread_ids"][0], "relation": "primary"}])
        self.assertEqual(trigger["disposition"]["state"], "evaluation.queued")
        self.assertEqual(trigger["disposition"]["events"][0]["event_type"], "evaluation.queued")
        with self.assertRaises(ValueError):
            self.host.sources.get_trigger(first["trigger_id"], event_limit=0)
        self.assertEqual(
            self.host.sources.list_triggers(source_instance_id="local-source")[0]["trigger_id"],
            first["trigger_id"],
        )
        self.assertEqual(
            self.host.sources.list_triggers(thread_id=first["thread_ids"][0])[0]["trigger_id"],
            first["trigger_id"],
        )
        self.assertIsNone(self.host.sources.get_trigger("missing-trigger"))
        with self.assertRaises(ValueError):
            self.host.sources.list_triggers(limit=0)
        self.host.sources.create_instance(
            "local-source-two", kind="local", config={"namespace": "repo-two"},
        )
        second = self.host.sources.receive_local(
            "local-source-two",
            {"event_id": "same-event", "thread_id": "thread-1", "text": "Same body",
             "occurred_at": "2026-11-04T12:00:00Z"},
        )
        self.assertNotEqual(first["trigger_id"], second["trigger_id"])
        self.assertNotEqual(first["thread_ids"][0], second["thread_ids"][0])

    def test_accept_fanout_excludes_triggers_rejected_by_gate(self):
        self.publish()
        allowed = self.receive(
            event_id="mixed-batch-allowed", thread_id="mixed-batch",
            text="Allowed explicit request",
        )
        denied = self.receive(
            event_id="mixed-batch-denied", thread_id="mixed-batch",
            text="Unlisted unaddressed input", actor_id="human:2", addressed="none",
        )
        candidate = self.host.authorization.begin_evaluation(
            allowed["thread_ids"][0], actor={"kind": "host"},
        )
        gate_by_trigger = {item["trigger_id"]: item["gate"] for item in candidate["gate_results"]}
        self.assertTrue(gate_by_trigger[allowed["trigger_id"]]["admissible"])
        self.assertFalse(gate_by_trigger[denied["trigger_id"]]["admissible"])
        self.assertEqual(
            [item["trigger_id"] for item in candidate["triggers"]],
            [allowed["trigger_id"]],
        )
        self.assertNotIn(
            "Unlisted unaddressed input",
            [item["body"].get("text") for item in candidate["triggers"]],
        )

        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(evidence="Allowed explicit request"),
            actor={"kind": "assessor"},
        )
        ticket = self.host.authorization.ticket(accepted["ticket_id"])
        self.assertEqual([item["trigger_id"] for item in ticket["inputs"]], [allowed["trigger_id"]])
        denied_view = self.host.sources.get_trigger(denied["trigger_id"])
        self.assertEqual(denied_view["disposition"]["state"], "filtered")
        self.assertEqual(denied_view["disposition"]["events"][-1]["event_type"], "filtered")
        self.assertEqual(
            self.host.sources.get_trigger(allowed["trigger_id"])["disposition"]["state"],
            "ticket.fanout",
        )

    def test_gate_denied_triggers_stay_filtered_across_unavailable_requeue(self):
        self.publish()
        allowed = self.receive(
            event_id="unavailable-allowed", thread_id="unavailable-mixed",
            text="Allowed explicit request",
        )
        denied = self.receive(
            event_id="unavailable-denied", thread_id="unavailable-mixed",
            text="Denied unaddressed request", actor_id="human:2", addressed="none",
        )
        candidate = self.host.authorization.begin_evaluation(
            allowed["thread_ids"][0], actor={"kind": "host"},
        )
        unavailable = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"],
            {"kind": "unavailable", "reason": "assessor_failed"},
            actor={"kind": "assessor"},
        )
        self.assertEqual(unavailable["state"], "unavailable")
        self.assertEqual(self.host.sources.get_trigger(denied["trigger_id"])["disposition"]["state"], "filtered")
        self.assertEqual(
            self.host.sources.get_trigger(allowed["trigger_id"])["disposition"]["state"],
            "evaluation.queued",
        )
        self.assertTrue(self.host.authorization.requeue_unavailable(
            candidate["evaluation_id"], actor={"kind": "host"},
        ))
        self.assertEqual(self.host.sources.get_trigger(denied["trigger_id"])["disposition"]["state"], "filtered")
        self.assertEqual(
            self.host.sources.get_trigger(allowed["trigger_id"])["disposition"]["state"],
            "evaluation.queued",
        )

        denied_only = self.receive(
            event_id="gate-denied-only", thread_id="gate-denied-only",
            text="No admissible actor", actor_id="human:2", addressed="none",
        )
        immediate = self.host.authorization.begin_evaluation(
            denied_only["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual((immediate["state"], immediate["kind"]), ("decided", "ignore"))
        self.assertEqual(
            self.host.sources.get_trigger(denied_only["trigger_id"])["disposition"]["state"],
            "filtered",
        )

    def test_inferred_candidate_in_observe_mode_records_would_accept_only(self):
        self.publish(mode="observe")
        received = self.receive(addressed="none")
        candidate = self.host.authorization.begin_evaluation(received["thread_ids"][0], actor={"kind": "host"})
        self.assertEqual(candidate["strength"], "inferred")
        result = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        self.assertEqual(result["kind"], "ignore")
        self.assertIsNone(result["ticket_id"])
        with self.host._store.transaction() as db:
            decision = db.execute("SELECT audit_json FROM decisions WHERE decision_id=?", (result["decision_id"],)).fetchone()
        self.assertTrue(json.loads(decision["audit_json"])["would_accept"])

    def test_inferred_mandate_is_revoked_when_policy_changes_to_observe(self):
        self.publish(mode="auto")
        received = self.receive(
            event_id="inferred-mandate", thread_id="inferred-thread", addressed="none",
        )
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual(candidate["strength"], "inferred")
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        self.assertEqual(accepted["kind"], "accept")

        self.publish(mode="observe")
        status = self.host.authorization.revalidate_mandate(
            accepted["mandate_id"], actor={"kind": "policy"},
        )
        self.assertEqual(status["status"], "revoked")
        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(mandate["events"][-1]["event_type"], "revoked")
        self.assertEqual(mandate["events"][-1]["payload"]["reason"], "inferred_admission_disabled")

        followup = self.receive(
            event_id="inferred-after-observe", thread_id="inferred-thread",
            text="Try to continue after observe mode",
        )
        self.assertEqual(followup["fanout_ticket_ids"], [])
        with self.assertRaises(PermissionError):
            self.host.authorization.subscribe(
                accepted["ticket_id"], received["thread_ids"][0],
                mode="work", actor={"kind": "host"},
            )

    def test_source_config_revision_change_revokes_existing_mandate(self):
        _, _, accepted = self.accept_local_event()
        with self.host._store.transaction(write=True) as db:
            db.execute(
                "UPDATE source_instances SET config_revision=config_revision+1 WHERE source_instance_id=?",
                ("local-source",),
            )
        status = self.host.authorization.revalidate_mandate(
            accepted["mandate_id"], actor={"kind": "policy"},
        )
        self.assertEqual(status["status"], "revoked")
        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(mandate["events"][-1]["event_type"], "revoked")
        self.assertEqual(mandate["events"][-1]["payload"]["reason"], "source_config_changed")

    def test_source_config_change_atomically_revokes_existing_mandate(self):
        self.host.sources.create_instance(
            "github-source", kind="github",
            config={
                "repository": "openai/harness", "repository_id": 42,
                "bot_login": "agent", "bot_user_id": 99,
                "webhook_secret_ref": "secret://github/repo",
            },
        )
        self.host.authorization.publish_policy(
            {
                "mode": "auto", "timeout_seconds": 120,
                "rules": [{
                    "id": "implementation", "name": "Implementation", "enabled": True,
                    "instruction": "Only accept a settled implementation request.",
                }],
                "sources": {"github-source": {"allowed": [{"id": "github:user:2"}]}},
            },
            actor={"kind": "human", "id": "policy-admin"},
        )
        received = self.host._authorization_service.receive_normalized(
            source_instance_id="github-source", expected_source_revision=1,
            kind="github", adapter_version="github/1",
            trigger=NormalizedTrigger(
                external_event_id="github-config-revision-origin",
                event_kind="github.issue_comment.created",
                actor_id="github:user:2", actor_kind="human",
                occurred_at="2026-11-04T12:00:00Z", addressed="mention",
                threads=(NormalizedThread("issue/42"),),
                primary_thread_id="issue/42",
                body={"text": "@agent please implement this"},
                metadata={"scope_row": "default"},
            ),
        )
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(evidence="@agent please implement this"),
            actor={"kind": "assessor"},
        )

        updated = self.host.sources.update_instance(
            "github-source", expected_revision=1,
            config={
                "repository": "openai/harness", "repository_id": 42,
                "bot_login": "renamed-agent", "bot_user_id": 99,
                "webhook_secret_ref": "secret://github/repo",
            },
            actor={"kind": "human", "id": "source-admin"},
        )
        self.assertEqual(updated.config_revision, 2)
        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(mandate["events"][-1]["event_type"], "revoked")
        self.assertEqual(mandate["events"][-1]["payload"]["reason"], "source_config_changed")

    def test_source_disable_atomically_revokes_existing_mandate(self):
        _, _, accepted = self.accept_local_event(event_id="source-disable-revokes")
        disabled = self.host.sources.update_instance(
            "local-source", expected_revision=1, enabled=False,
            actor={"kind": "human", "id": "source-admin"},
        )
        self.assertFalse(disabled.enabled)
        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(mandate["events"][-1]["event_type"], "revoked")
        self.assertEqual(mandate["events"][-1]["payload"]["reason"], "source_disabled")

        enabled = self.host.sources.update_instance(
            "local-source", expected_revision=2, enabled=True,
            actor={"kind": "human", "id": "source-admin"},
        )
        self.assertTrue(enabled.enabled)
        mandate_after_reenable = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(len(mandate_after_reenable["events"]), 1)
        self.assertEqual(mandate_after_reenable["events"][-1]["event_type"], "revoked")

        self.publish(mode="observe")
        mandate_after_policy_change = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(len(mandate_after_policy_change["events"]), 1)

    def test_source_config_revision_change_stales_active_evaluation_before_assessment(self):
        self.publish()
        received = self.receive(event_id="source-revision-active", thread_id="source-revision-thread")
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual(candidate["state"], "running")

        self.host.sources.update_instance(
            "local-source", expected_revision=1, enabled=False,
        )
        self.host.sources.update_instance(
            "local-source", expected_revision=2, enabled=True,
        )
        assessed = []
        result = self.host.authorization.assess_next(
            received["thread_ids"][0],
            lambda value: assessed.append(value) or self.accept_proposal(),
            actor={"kind": "host"},
        )
        self.assertEqual((result["state"], result["kind"]), ("decided", "ignore"))
        self.assertEqual(assessed, [])
        with self.host._store.transaction() as db:
            old = db.execute(
                "SELECT state,outcome_json FROM evaluations WHERE evaluation_id=?",
                (candidate["evaluation_id"],),
            ).fetchone()
        self.assertEqual(old["state"], "cancelled")
        self.assertEqual(json.loads(old["outcome_json"])["reason"], "source_config_changed")
        self.assertEqual(
            self.host.sources.get_trigger(received["trigger_id"])["disposition"]["state"],
            "filtered",
        )

    def test_policy_change_cancels_running_evaluation_and_requeues_trigger(self):
        self.publish()
        received = self.receive()
        candidate = self.host.authorization.begin_evaluation(received["thread_ids"][0], actor={"kind": "host"})
        self.publish(mode="observe")
        result = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["reason"], "policy_changed")
        with self.host._store.transaction() as db:
            disposition = db.execute(
                "SELECT state FROM trigger_dispositions WHERE trigger_id=?", (received["trigger_id"],)
            ).fetchone()
        self.assertEqual(disposition["state"], "evaluation.queued")
        self.assertIsNone(self.host.authorization.ticket("ticket-does-not-exist"))

    def test_handback_requeues_work_input_but_context_input_does_not_own(self):
        work_thread = "thr_" + _hash(_json(["local-source", "local/repo/work-thread"]))
        context_thread = "thr_" + _hash(_json(["local-source", "local/repo/context-thread"]))
        scope = {
            "source_instance_ids": ["local-source"],
            "thread_ids": [work_thread, context_thread],
            "actions": ["read"], "tools": [],
            "budget": {"max_turns": 1, "max_seconds": 600},
        }
        _, _, accepted = self.accept_local_event(thread_id="work-thread", scope=scope)
        ticket_id = accepted["ticket_id"]

        self.receive(event_id="context-create", thread_id="context-thread", text="Create context thread")
        self.host.authorization.subscribe(
            ticket_id, context_thread, mode="context", actor={"kind": "host"},
        )
        with self.assertRaises(PermissionError):
            self.host.authorization.subscribe(
                ticket_id, context_thread, mode="work", actor={"kind": "host"},
            )
        context_event = self.receive(event_id="context-followup", thread_id="context-thread", text="Context only")
        self.assertEqual(context_event["disposition"], "evaluation.queued")
        self.assertEqual(context_event["context_ticket_ids"], [ticket_id])
        with self.assertRaises(PermissionError):
            self.host.authorization.handback(
                ticket_id, context_event["trigger_id"], "Context is not owned",
                actor={"kind": "assignment", "id": "assignment-test"},
            )
        with self.host._store.transaction() as db:
            context_input = db.execute(
                "SELECT context_only,state FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
                (ticket_id, context_event["trigger_id"]),
            ).fetchone()
        self.assertEqual(context_input["context_only"], 1)

        related_event = self.host._authorization_service.receive_normalized(
            source_instance_id="local-source",
            expected_source_revision=1,
            kind="local",
            adapter_version="local/1",
            trigger=NormalizedTrigger(
                external_event_id="related-context-only",
                event_kind="local.message",
                actor_id="human:1",
                actor_kind="human",
                occurred_at="2026-11-04T12:00:00Z",
                addressed="direct",
                threads=(
                    NormalizedThread("local/repo/context-thread", "primary"),
                    NormalizedThread("local/repo/work-thread", "related"),
                ),
                primary_thread_id="local/repo/context-thread",
                body={"text": "A related thread is not a work follow-up"},
                metadata={"namespace": "repo", "scope_row": "default"},
                verification_state="local_attested",
            ),
        )
        self.assertEqual(related_event["disposition"], "evaluation.queued")
        self.assertEqual(related_event["fanout_ticket_ids"], [])
        self.assertEqual(related_event["context_ticket_ids"], [ticket_id])
        with self.assertRaises(PermissionError):
            self.host.authorization.handback(
                ticket_id, related_event["trigger_id"], "Related thread is context only",
                actor={"kind": "assignment", "id": "assignment-test"},
            )
        with self.host._store.transaction() as db:
            related_input = db.execute(
                "SELECT context_only,state FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
                (ticket_id, related_event["trigger_id"]),
            ).fetchone()
        self.assertEqual(related_input["context_only"], 1)

        followup = self.receive(event_id="work-followup", thread_id="work-thread", text="Another work detail")
        self.assertEqual(followup["disposition"], "ticket.fanout")
        handed = self.host.authorization.handback(
            ticket_id, followup["trigger_id"], "Outside the accepted ticket scope",
            actor={"kind": "assignment", "id": "assignment-test"},
        )
        self.assertTrue(handed["requeued"])
        with self.host._store.transaction() as db:
            input_row = db.execute(
                "SELECT state,context_only FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
                (ticket_id, followup["trigger_id"]),
            ).fetchone()
        self.assertEqual(input_row["state"], "handed_back")
        self.assertEqual(input_row["context_only"], 0)

    def test_followup_fanout_rechecks_current_gate_for_each_trigger(self):
        origin, _, accepted = self.accept_local_event()
        policy = self.policy()
        policy["sources"]["local-source"]["admission"] = {
            "default": {"unlisted_addressed": "ignore"},
        }
        self.host.authorization.publish_policy(
            policy, actor={"kind": "human", "id": "policy-admin"},
        )

        followup = self.receive(
            event_id="unlisted-followup", thread_id="work-thread",
            actor_id="human:unlisted",
        )
        self.assertEqual(followup["disposition"], "evaluation.queued")
        self.assertEqual(followup["fanout_ticket_ids"], [])
        self.assertEqual(followup["gate"]["reason"], "admission_unlisted_addressed_ignored")
        immediate = self.host.authorization.begin_evaluation(
            followup["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual((immediate["state"], immediate["kind"]), ("decided", "ignore"))
        ticket = self.host.authorization.ticket(accepted["ticket_id"])
        self.assertEqual([item["trigger_id"] for item in ticket["inputs"]], [origin["trigger_id"]])
        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertFalse(any(event["event_type"] == "revoked" for event in mandate["events"]))

    def test_policy_removal_revokes_mandate_and_prevents_followup_fanout(self):
        _, _, accepted = self.accept_local_event()
        mandate_id = accepted["mandate_id"]
        ticket_id = accepted["ticket_id"]
        self.publish(allowed=())
        status = self.host.authorization.revalidate_mandate(mandate_id, actor={"kind": "policy"})
        self.assertEqual(status["status"], "revoked")
        followup = self.receive(event_id="after-revocation", thread_id="work-thread", text="Try to continue")
        self.assertEqual(followup["disposition"], "evaluation.queued")
        self.assertEqual(followup["fanout_ticket_ids"], [])

    def test_policy_scope_narrowing_revokes_old_mandate_scope_and_stops_fanout(self):
        origin_thread = "thr_" + _hash(_json(["local-source", "local/repo/scope-origin"]))
        extra_thread = "thr_" + _hash(_json(["local-source", "local/repo/scope-extra"]))
        scope = {
            "source_instance_ids": ["local-source"],
            "thread_ids": [origin_thread, extra_thread],
            "actions": ["read"], "tools": [],
            "budget": {"max_turns": 1, "max_seconds": 600},
        }
        _, _, accepted = self.accept_local_event(thread_id="scope-origin", scope=scope)
        ticket_id = accepted["ticket_id"]
        extra = self.receive(event_id="scope-extra-create", thread_id="scope-extra")
        self.host.authorization.subscribe(
            ticket_id, extra["thread_ids"][0], mode="work", actor={"kind": "host"},
        )

        narrowed = self.policy(scope={
            "source_instance_ids": ["local-source"],
            "thread_ids": [origin_thread],
            "actions": ["read"], "tools": [],
            "budget": {"max_turns": 1, "max_seconds": 600},
        })
        self.host.authorization.publish_policy(narrowed, actor={"kind": "human", "id": "policy-admin"})
        status = self.host.authorization.revalidate_mandate(
            accepted["mandate_id"], actor={"kind": "policy"},
        )
        self.assertEqual(status["status"], "revoked")

        followup = self.receive(event_id="scope-extra-after-narrow", thread_id="scope-extra")
        self.assertEqual(followup["disposition"], "evaluation.queued")
        self.assertEqual(followup["fanout_ticket_ids"], [])

        self.host.authorization.unsubscribe(
            ticket_id, extra["thread_ids"][0], actor={"kind": "host"},
        )
        with self.assertRaises(PermissionError):
            self.host.authorization.subscribe(
                ticket_id, extra["thread_ids"][0], mode="work", actor={"kind": "host"},
            )

    def test_policy_scope_cannot_expand_beyond_originating_thread(self):
        self.publish()
        received = self.receive()
        candidate = self.host.authorization.begin_evaluation(received["thread_ids"][0], actor={"kind": "host"})
        proposal = self.accept_proposal()
        proposal["scope"] = {
            "source_instance_ids": ["local-source"],
            "thread_ids": [received["thread_ids"][0], "other-thread"],
            "actions": ["read"], "tools": [],
            "budget": {"max_turns": 1, "max_seconds": 600},
        }
        with self.assertRaises(PermissionError):
            self.host.authorization.complete_evaluation(
                candidate["evaluation_id"], proposal, actor={"kind": "assessor"},
            )
        self.assertIsNone(self.host.authorization.ticket("ticket-does-not-exist"))

    def test_equal_timestamp_thread_lifecycle_conflicts_fail_closed(self):
        observed_at = "2026-11-04T12:00:00Z"

        def receive(event_id, thread_key, state, outcome=None):
            return self.host._authorization_service.receive_normalized(
                source_instance_id="local-source",
                expected_source_revision=1,
                kind="local",
                adapter_version="local/1",
                trigger=NormalizedTrigger(
                    external_event_id=event_id,
                    event_kind="local.lifecycle",
                    actor_id="human:1",
                    actor_kind="human",
                    occurred_at=observed_at,
                    addressed="none",
                    threads=(NormalizedThread(f"local/repo/{thread_key}"),),
                    primary_thread_id=f"local/repo/{thread_key}",
                    body={"text": "lifecycle observation"},
                    metadata={"scope_row": "default"},
                    lifecycle=ThreadLifecycleUpdate(
                        state, outcome, provider_revision="same-revision", observed_at=observed_at,
                    ),
                    verification_state="local_attested",
                ),
            )

        opened = receive("tie-open-first", "tie-open-first", "open")
        closed = receive("tie-close-after-open", "tie-open-first", "closed", "closed")
        self.assertEqual(
            self.host.sources.get_thread(opened["thread_ids"][0])["source_state"],
            "closed",
        )
        reopened = receive("tie-reopen-after-close", "tie-open-first", "open")
        self.assertEqual(
            self.host.sources.get_thread(reopened["thread_ids"][0])["source_state"],
            "closed",
        )

        closed_first = receive("tie-close-first", "tie-close-first", "closed", "merged")
        opened_after = receive("tie-open-after-close", "tie-close-first", "open")
        closed_thread = self.host.sources.get_thread(closed_first["thread_ids"][0])
        self.assertEqual(closed_thread["source_state"], "closed")
        self.assertEqual(closed_thread["source_outcome"], "merged")
        self.assertEqual(
            self.host.sources.get_thread(opened_after["thread_ids"][0])["source_state"],
            "closed",
        )

    def test_github_source_verifies_webhook_signature_and_normalizes_issue_comment(self):
        self.host.sources.create_instance(
            "github-source", kind="github",
            config={
                "repository": "openai/harness", "repository_id": 42,
                "bot_login": "harness-agent", "bot_user_id": 99,
                "webhook_secret_ref": "secret://github/repo",
            },
        )
        raw = json.dumps({
            "action": "created",
            "repository": {"id": 42, "full_name": "openai/harness"},
            "issue": {"id": 700, "number": 7, "title": "Issue"},
            "comment": {
                "id": 900, "body": "@harness-agent please fix this",
                "html_url": "https://github.com/openai/harness/issues/7#issuecomment-900",
                "created_at": "2026-11-04T10:00:00Z",
                "user": {"id": 1, "login": "alice", "type": "User"},
            },
            "sender": {"id": 1, "login": "alice", "type": "User"},
        }, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(self.secrets["secret://github/repo"], raw, hashlib.sha256).hexdigest()
        headers = {
            "X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "delivery-900",
            "X-Hub-Signature-256": signature,
        }
        received = self.host.sources.receive("github-source", headers=headers, raw=raw)
        self.assertEqual(received["state"], "stored")
        with self.host._store.transaction() as db:
            trigger = db.execute("SELECT * FROM triggers WHERE trigger_id=?", (received["trigger_id"],)).fetchone()
        self.assertEqual(trigger["addressed"], "mention")
        self.assertEqual(json.loads(trigger["actor_json"])["actor_kind"], "human")
        self.assertEqual(trigger["external_event_id"], "github:comment:900")
        with self.assertRaises(ValueError):
            self.host.sources.receive(
                "github-source",
                headers={**headers, "x-hub-signature-256": signature},
                raw=raw,
            )
        self.host.authorization.publish_policy(
            {
                "mode": "auto", "timeout_seconds": 120,
                "rules": [{
                    "id": "implementation", "name": "Implementation", "enabled": True,
                    "instruction": "Only accept settled implementation requests.",
                }],
                "sources": {"github-source": {"allowed": [
                    {"id": "github:user:2"}, {"id": "github:user:3"},
                ]}},
            },
            actor={"kind": "human", "id": "policy-admin"},
        )
        closed_raw = json.dumps({
            "action": "closed",
            "repository": {"id": 42, "full_name": "openai/harness"},
            "pull_request": {
                "id": 701, "number": 8, "title": "Merged change",
                "body": "@harness-agent please implement this",
                "html_url": "https://github.com/openai/harness/pull/8",
                "updated_at": "2026-11-04T11:00:00Z", "merged": True,
                "user": {"id": 1, "login": "author", "type": "User"},
            },
            "sender": {"id": 2, "login": "closer", "type": "User"},
        }, separators=(",", ":")).encode()
        closed_signature = "sha256=" + hmac.new(
            self.secrets["secret://github/repo"], closed_raw, hashlib.sha256,
        ).hexdigest()
        closed = self.host.sources.receive(
            "github-source",
            headers={
                "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-701",
                "X-Hub-Signature-256": closed_signature,
            },
            raw=closed_raw,
        )
        self.assertEqual(closed["state"], "stored")
        thread = self.host.sources.get_thread(closed["thread_ids"][0])
        self.assertEqual(thread["source_state"], "closed")
        self.assertEqual(thread["source_outcome"], "merged")
        closed_trigger = self.host.sources.get_trigger(closed["trigger_id"])
        self.assertEqual(closed_trigger["actor"]["actor_id"], "github:user:2")
        self.assertEqual(closed_trigger["addressed"], "none")
        self.assertTrue(closed_trigger["metadata"]["lifecycle_only"])
        closed_evaluation = self.host.authorization.begin_evaluation(
            closed["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual((closed_evaluation["state"], closed_evaluation["kind"]), ("decided", "ignore"))

        comment_after_close_raw = json.dumps({
            "action": "created",
            "repository": {"id": 42, "full_name": "openai/harness"},
            "issue": {"id": 701, "number": 8, "title": "Merged change"},
            "comment": {
                "id": 902, "body": "@harness-agent please continue",
                "html_url": "https://github.com/openai/harness/pull/8#issuecomment-902",
                "created_at": "2026-11-04T11:30:00Z",
                "user": {"id": 2, "login": "closer", "type": "User"},
            },
            "sender": {"id": 2, "login": "closer", "type": "User"},
        }, separators=(",", ":")).encode()
        comment_after_close_signature = "sha256=" + hmac.new(
            self.secrets["secret://github/repo"], comment_after_close_raw, hashlib.sha256,
        ).hexdigest()
        comment_after_close = self.host.sources.receive(
            "github-source",
            headers={
                "X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "delivery-902",
                "X-Hub-Signature-256": comment_after_close_signature,
            },
            raw=comment_after_close_raw,
        )
        self.assertEqual(comment_after_close["gate"]["reason"], "thread_closed")
        self.assertEqual(comment_after_close["fanout_ticket_ids"], [])
        closed_comment_evaluation = self.host.authorization.begin_evaluation(
            closed["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual((closed_comment_evaluation["state"], closed_comment_evaluation["kind"]), ("decided", "ignore"))

        reopened_raw = json.dumps({
            "action": "reopened",
            "repository": {"id": 42, "full_name": "openai/harness"},
            "pull_request": {
                "id": 701, "number": 8, "title": "Merged change",
                "html_url": "https://github.com/openai/harness/pull/8",
                "created_at": "2026-11-04T09:00:00Z",
                "updated_at": "2026-11-04T12:00:00Z", "merged": False,
                "body": "@harness-agent please resume this request",
                "user": {"id": 1, "login": "author", "type": "User"},
            },
            "sender": {"id": 3, "login": "reopener", "type": "User"},
        }, separators=(",", ":")).encode()
        reopened_signature = "sha256=" + hmac.new(
            self.secrets["secret://github/repo"], reopened_raw, hashlib.sha256,
        ).hexdigest()
        reopened = self.host.sources.receive(
            "github-source",
            headers={
                "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-701-reopen",
                "X-Hub-Signature-256": reopened_signature,
            },
            raw=reopened_raw,
        )
        self.assertEqual(self.host.sources.get_thread(reopened["thread_ids"][0])["source_state"], "open")
        self.assertTrue(reopened["gate"]["admissible"])
        reopened_candidate = self.host.authorization.begin_evaluation(
            reopened["thread_ids"][0], actor={"kind": "host"},
        )
        accepted = self.host.authorization.complete_evaluation(
            reopened_candidate["evaluation_id"], self.accept_proposal(
                evidence="@harness-agent please resume this request",
            ),
            actor={"kind": "assessor"},
        )
        self.assertEqual(accepted["kind"], "accept")

        stale_closed_raw = json.dumps({
            "action": "closed",
            "repository": {"id": 42, "full_name": "openai/harness"},
            "pull_request": {
                "id": 701, "number": 8, "title": "Merged change", "body": "",
                "html_url": "https://github.com/openai/harness/pull/8",
                "created_at": "2026-11-04T09:00:00Z",
                "updated_at": "2026-11-04T10:00:00Z", "merged": True,
                "user": {"id": 1, "login": "author", "type": "User"},
            },
            "sender": {"id": 2, "login": "closer", "type": "User"},
        }, separators=(",", ":")).encode()
        stale_signature = "sha256=" + hmac.new(
            self.secrets["secret://github/repo"], stale_closed_raw, hashlib.sha256,
        ).hexdigest()
        stale = self.host.sources.receive(
            "github-source",
            headers={
                "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-701-stale-close",
                "X-Hub-Signature-256": stale_signature,
            },
            raw=stale_closed_raw,
        )
        self.assertEqual(stale["state"], "stored")
        current_thread = self.host.sources.get_thread(reopened["thread_ids"][0])
        self.assertEqual(current_thread["source_state"], "open")
        self.assertEqual(current_thread["source_observed_at"], "2026-11-04T12:00:00.000Z")
        self.assertEqual(
            self.host.authorization.revalidate_mandate(
                accepted["mandate_id"], actor={"kind": "host"},
            )["status"],
            "valid",
        )

        final_closed_payload = json.loads(closed_raw)
        final_closed_payload["pull_request"]["updated_at"] = "2026-11-04T13:00:00Z"
        final_closed_raw = json.dumps(final_closed_payload, separators=(",", ":")).encode()
        final_closed_signature = "sha256=" + hmac.new(
            self.secrets["secret://github/repo"], final_closed_raw, hashlib.sha256,
        ).hexdigest()
        final_closed = self.host.sources.receive(
            "github-source",
            headers={
                "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-701-final-close",
                "X-Hub-Signature-256": final_closed_signature,
            },
            raw=final_closed_raw,
        )
        self.assertEqual(self.host.sources.get_thread(final_closed["thread_ids"][0])["source_state"], "closed")
        closed_mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(closed_mandate["events"][-1]["event_type"], "revoked")
        self.assertEqual(closed_mandate["events"][-1]["payload"]["reason"], "origin_thread_closed")
        self.assertEqual(
            self.host.authorization.revalidate_mandate(
                accepted["mandate_id"], actor={"kind": "host"},
            )["status"],
            "revoked",
        )
        final_thread = self.host.sources.get_thread(final_closed["thread_ids"][0])

        same_time_reopen_payload = json.loads(reopened_raw)
        same_time_reopen_payload["pull_request"]["updated_at"] = "2026-11-04T13:00:00Z"
        same_time_reopen_raw = json.dumps(same_time_reopen_payload, separators=(",", ":")).encode()
        same_time_reopen_signature = "sha256=" + hmac.new(
            self.secrets["secret://github/repo"], same_time_reopen_raw, hashlib.sha256,
        ).hexdigest()
        same_time_reopen = self.host.sources.receive(
            "github-source",
            headers={
                "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-701-same-time-reopen",
                "X-Hub-Signature-256": same_time_reopen_signature,
            },
            raw=same_time_reopen_raw,
        )
        tied_thread = self.host.sources.get_thread(same_time_reopen["thread_ids"][0])
        self.assertEqual(same_time_reopen["gate"]["reason"], "thread_closed")
        self.assertEqual(tied_thread["source_state"], "closed")
        self.assertEqual(tied_thread["source_outcome"], "merged")
        self.assertEqual(tied_thread["lifecycle_revision"], final_thread["lifecycle_revision"])

        same_time_closed_payload = json.loads(final_closed_raw)
        same_time_closed_payload["pull_request"]["merged"] = False
        same_time_closed_raw = json.dumps(same_time_closed_payload, separators=(",", ":")).encode()
        same_time_closed_signature = "sha256=" + hmac.new(
            self.secrets["secret://github/repo"], same_time_closed_raw, hashlib.sha256,
        ).hexdigest()
        same_time_closed = self.host.sources.receive(
            "github-source",
            headers={
                "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-701-same-time-closed",
                "X-Hub-Signature-256": same_time_closed_signature,
            },
            raw=same_time_closed_raw,
        )
        tied_closed_thread = self.host.sources.get_thread(same_time_closed["thread_ids"][0])
        self.assertEqual(tied_closed_thread["source_state"], "closed")
        self.assertEqual(tied_closed_thread["source_outcome"], "merged")
        self.assertEqual(tied_closed_thread["lifecycle_revision"], final_thread["lifecycle_revision"])

        with self.assertRaises(PermissionError):
            self.host.sources.receive("github-source", headers={**headers, "X-Hub-Signature-256": "sha256=" + "0" * 64}, raw=raw)

    def test_feishu_source_verifies_encrypted_event_and_tenant_scope(self):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            self.skipTest("cryptography extra is not installed")
        self.host.sources.create_instance(
            "feishu-source", kind="feishu",
            config={
                "app_id": "cli_test", "tenant_key": "tenant_test", "bot_open_id": "ou_bot",
                "chats": ["oc_chat"], "verification_token_ref": "secret://feishu/token",
                "encrypt_key_ref": "secret://feishu/encrypt",
            },
        )
        event = {
            "schema": "2.0",
            "header": {
                "token": "feishu-verification-token", "app_id": "cli_test",
                "tenant_key": "tenant_test", "event_type": "im.message.receive_v1",
                "event_id": "delivery-1", "event_time": str(int(time.time())),
            },
            "event": {
                "message": {
                    "chat_id": "oc_chat", "message_id": "om_message", "root_id": "om_root",
                    "message_type": "text", "chat_type": "group",
                    "content": json.dumps({"text": "@_user_1 please fix this"}),
                    "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot"}}],
                },
                "sender": {
                    "tenant_key": "tenant_test", "sender_type": "user",
                    "sender_id": {"open_id": "ou_user"},
                },
            },
        }
        plain = json.dumps(event, separators=(",", ":")).encode()
        padding = 16 - (len(plain) % 16)
        padded = plain + bytes([padding]) * padding
        iv = b"0123456789abcdef"
        encrypt_key = self.secrets["secret://feishu/encrypt"]
        encryptor = Cipher(
            algorithms.AES(hashlib.sha256(encrypt_key).digest()), modes.CBC(iv),
        ).encryptor()
        encrypted = iv + encryptor.update(padded) + encryptor.finalize()
        raw = json.dumps({"encrypt": base64.b64encode(encrypted).decode()}, separators=(",", ":")).encode()
        timestamp, nonce = str(int(time.time())), "nonce-phase2"
        signature = hashlib.sha256(timestamp.encode() + nonce.encode() + encrypt_key + raw).hexdigest()
        received = self.host.sources.receive(
            "feishu-source",
            headers={
                "x-lark-request-timestamp": timestamp, "x-lark-request-nonce": nonce,
                "x-lark-signature": signature,
            },
            raw=raw,
        )
        self.assertEqual(received["state"], "stored")
        with self.host._store.transaction() as db:
            trigger = db.execute("SELECT * FROM triggers WHERE trigger_id=?", (received["trigger_id"],)).fetchone()
        self.assertEqual(trigger["addressed"], "mention")
        self.assertEqual(trigger["external_event_id"], "feishu:message:om_message")

    def test_source_kind_api_version_mismatch_fails_closed(self):
        registry = SourceRegistry(self.host._store, register_builtins=False)
        first_kind = LocalSourceKind()
        unregister = registry.register(first_kind)
        registry.create_instance(
            "versioned-local", kind="local", config={"namespace": "versioned"},
        )
        unregister()
        with self.assertRaises(LookupError):
            registry.advance_cursor(
                "versioned-local", "events", {"offset": 1}, expected_revision=0,
                actor={"kind": "host"},
            )

        upgraded_kind = LocalSourceKind()
        upgraded_kind.api_version = 2
        registry.register(upgraded_kind)
        instance = registry.get_instance("versioned-local")
        self.assertFalse(instance.installed)
        with self.assertRaises(RuntimeError):
            registry.receive_local(
                "versioned-local",
                {
                    "event_id": "version-mismatch", "thread_id": "thread",
                    "text": "must not be normalized by an incompatible plugin",
                    "occurred_at": "2026-11-04T12:00:00Z",
                },
            )
        with self.assertRaises(RuntimeError):
            registry.update_instance(
                "versioned-local", expected_revision=1, enabled=False,
            )
        with self.assertRaises(RuntimeError):
            registry.advance_cursor(
                "versioned-local", "events", {"offset": 1}, expected_revision=0,
                actor={"kind": "host"},
            )

    def test_source_kind_unregistration_revokes_mandates_and_unregister_is_host_scoped(self):
        unregister, accepted = self.accept_custom_source(CustomLocalSourceKind())
        unregister()
        self.assertFalse(self.host.sources.get_instance("custom-source").installed)
        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(mandate["events"][-1]["event_type"], "revoked")
        self.assertEqual(mandate["events"][-1]["payload"]["reason"], "source_kind_unavailable")
        self.assertEqual(
            self.host.authorization.revalidate_mandate(
                accepted["mandate_id"], actor={"kind": "host"},
            )["status"],
            "revoked",
        )
        self.host.close()
        with self.assertRaises(HarnessError):
            unregister()

    def test_source_kind_unregistration_cancels_running_evaluation_and_filters_trigger(self):
        unregister = self.host.sources.register(CustomLocalSourceKind())
        self.host.sources.create_instance(
            "custom-source", kind="local-custom", config={"namespace": "custom"},
        )
        policy = self.policy()
        policy["sources"]["custom-source"] = policy["sources"].pop("local-source")
        self.host.authorization.publish_policy(
            policy, actor={"kind": "human", "id": "policy-admin"},
        )
        received = self.host.sources.receive_local(
            "custom-source",
            {
                "event_id": "pending-custom-origin", "thread_id": "pending-custom-thread",
                "text": "Fix the custom source issue.",
                "occurred_at": "2026-11-04T12:00:00Z",
                "metadata": {"actor_id": "human:1", "actor_kind": "human", "addressed": "direct"},
            },
        )
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual(candidate["state"], "running")

        unregister()
        evaluation = self.host.authorization.evaluation(candidate["evaluation_id"])
        self.assertEqual(evaluation["state"], "cancelled")
        trigger = self.host.sources.get_trigger(received["trigger_id"])
        self.assertEqual(trigger["disposition"]["state"], "filtered")
        self.assertEqual(trigger["disposition"]["events"][-1]["payload"]["reason"], "source_kind_unavailable")
        with self.assertRaises(RuntimeError):
            self.host.authorization.complete_evaluation(
                candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
            )
        self.assertEqual(
            self.host.authorization.begin_evaluation(
                received["thread_ids"][0], actor={"kind": "host"},
            )["state"],
            "empty",
        )

    def test_source_kind_api_version_drift_revokes_mandates_during_host_open(self):
        _, accepted = self.accept_custom_source(CustomLocalSourceKind())
        self.host.close()
        self.host = CoordinatorHost.open(
            self.root / "v2", instance_id="phase2-tests",
            identity={"project": "harness"},
            secret_resolver=lambda reference: self.secrets[reference],
            local_identity_verifier=self._local_identity,
            source_kinds=(CustomLocalSourceKindV2(),),
        )
        instance = self.host.sources.get_instance("custom-source")
        self.assertFalse(instance.installed)
        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(mandate["events"][-1]["event_type"], "revoked")
        self.assertEqual(
            mandate["events"][-1]["payload"]["reason"],
            "source_kind_api_version_mismatch",
        )
        self.assertEqual(
            self.host.authorization.revalidate_mandate(
                accepted["mandate_id"], actor={"kind": "host"},
            )["status"],
            "revoked",
        )
        with self.assertRaises(RuntimeError):
            self.host.sources.receive_local(
                "custom-source",
                {
                    "event_id": "after-upgrade", "thread_id": "custom-thread",
                    "text": "Must not use a mismatched SourceKind.",
                    "occurred_at": "2026-11-04T13:00:00Z",
                },
            )

    def test_source_instance_identity_is_immutable(self):
        self.host.sources.create_instance(
            "github-source", kind="github",
            config={
                "repository": "openai/harness", "repository_id": 42,
                "bot_login": "agent", "bot_user_id": 99,
                "webhook_secret_ref": "secret://github/repo",
            },
        )
        with self.assertRaises(PermissionError):
            self.host.sources.update_instance(
                "github-source", expected_revision=1,
                config={
                    "repository": "openai/other", "repository_id": 43,
                    "bot_login": "agent", "bot_user_id": 99,
                    "webhook_secret_ref": "secret://github/repo",
                },
            )

    def test_shadow_api_is_removed_without_opening_or_rewriting_old_ledgers(self):
        legacy = self.root / 'old-ledger.sqlite3'
        legacy.write_bytes(b'preserve original')
        self.assertFalse(hasattr(self.host, 'shadow'))
        with self.assertRaises(TypeError):
            CoordinatorHost.open(self.root / 'unopened', instance_id='retired', identity={}, legacy_state_dir=legacy)
        self.assertEqual(legacy.read_bytes(), b'preserve original')
        self.assertFalse((self.root / 'unopened').exists())


    def test_explicit_human_request_can_be_accepted_in_observe_mode(self):
        self.publish(mode="observe")
        received = self.receive(event_id="explicit-observe")
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        self.assertEqual(candidate["strength"], "explicit")
        result = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        self.assertEqual(result["kind"], "accept")
        self.assertIsNotNone(self.host.authorization.ticket(result["ticket_id"]))

    def test_next_attempt_expires_an_abandoned_running_evaluation(self):
        self.publish()
        received = self.receive(event_id="abandoned-evaluation")
        first = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"}, now="2026-11-04T12:00:00Z",
        )
        active = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"}, now="2026-11-04T12:01:00Z",
        )
        self.assertEqual(active["evaluation_id"], first["evaluation_id"])
        retried = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"}, now="2026-11-04T12:02:01Z",
        )
        self.assertEqual(retried["state"], "running")
        self.assertNotEqual(retried["evaluation_id"], first["evaluation_id"])
        with self.host._store.transaction() as db:
            old = db.execute(
                "SELECT state,outcome_json FROM evaluations WHERE evaluation_id=?",
                (first["evaluation_id"],),
            ).fetchone()
        self.assertEqual(old["state"], "cancelled")
        self.assertEqual(json.loads(old["outcome_json"])["reason"], "evaluation_deadline_exceeded")

    def test_expired_evaluation_is_requeued_without_creating_ticket(self):
        self.publish()
        received = self.receive(event_id="expired-evaluation")
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"}, now="2026-11-04T12:00:00Z",
        )
        result = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
            now="2026-11-04T12:02:01Z",
        )
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["reason"], "evaluation_deadline_exceeded")
        with self.host._store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tickets").fetchone()[0], 0)
            disposition = db.execute(
                "SELECT state FROM trigger_dispositions WHERE trigger_id=?", (received["trigger_id"],)
            ).fetchone()
        self.assertEqual(disposition["state"], "evaluation.queued")

    def test_policy_content_can_be_reactivated_as_a_new_immutable_revision(self):
        first = self.publish(mode="auto")
        second = self.publish(mode="observe")
        third = self.publish(mode="auto")
        duplicate = self.publish(mode="auto")
        self.assertEqual((first["revision"], second["revision"], third["revision"]), (1, 2, 3))
        self.assertEqual(first["policy_hash"], third["policy_hash"])
        self.assertNotEqual(first["policy_version"], third["policy_version"])
        self.assertEqual(duplicate["policy_version"], third["policy_version"])
        self.assertEqual(duplicate["revision"], 3)

    def test_policy_rule_and_source_projections_are_immutable(self):
        policy = self.publish()
        with self.host._store.transaction(write=True) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM policy_rules WHERE policy_version=?", (policy["policy_version"],)).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM source_policies WHERE policy_version=?", (policy["policy_version"],)).fetchone()[0],
                1,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE policy_rules SET rule_json='{}' WHERE policy_version=?",
                    (policy["policy_version"],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "DELETE FROM source_policies WHERE policy_version=?",
                    (policy["policy_version"],),
                )

    def test_thread_and_channel_registry_reads_return_immutable_source_facts(self):
        received = self.host.sources.receive_local(
            "local-source",
            {
                "event_id": "channel-address", "thread_id": "channel-thread",
                "text": "A message with a channel address",
                "occurred_at": "2026-11-04T12:00:00Z",
                "channel_address": {"chat_id": "oc_local", "message_id": "om_local"},
            },
        )
        thread_id = received["thread_ids"][0]
        thread = self.host.sources.get_thread(thread_id)
        self.assertEqual(thread["thread_id"], thread_id)
        self.assertEqual(thread["source_state"], "unsupported")
        self.assertEqual(self.host.sources.list_threads(source_instance_id="local-source")[0]["thread_id"], thread_id)
        channels = self.host.sources.list_channels(thread_id=thread_id)
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["address"], {"chat_id": "oc_local", "message_id": "om_local"})
        self.assertEqual(channels[0]["capabilities"], {"reply": False, "read_context": False})
        self.assertEqual(self.host.sources.get_channel(channels[0]["channel_id"]), channels[0])
        with self.host._store.transaction(write=True) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE channels SET address_json='{}' WHERE channel_id=?",
                    (channels[0]["channel_id"],),
                )
        with self.assertRaises(ValueError):
            self.host.sources.list_channels(limit=0)

    def test_normalized_trigger_bounds_thread_metadata_and_channel_address(self):
        def receive(trigger):
            return self.host._authorization_service.receive_normalized(
                source_instance_id="local-source", expected_source_revision=1,
                kind="local", adapter_version="local/1", trigger=trigger,
            )

        with self.assertRaises(ValueError):
            receive(NormalizedTrigger(
                external_event_id="oversized-thread-metadata",
                event_kind="local.message", actor_id="human:1", actor_kind="human",
                occurred_at="2026-11-04T12:00:00Z", addressed="direct",
                threads=(NormalizedThread(
                    "local/repo/oversized-metadata",
                    metadata={"detail": "x" * 65_537},
                ),),
                primary_thread_id="local/repo/oversized-metadata",
                body={"text": "bounded"}, metadata={"scope_row": "default"},
                verification_state="local_attested",
            ))
        with self.assertRaises(ValueError):
            receive(NormalizedTrigger(
                external_event_id="invalid-empty-thread-metadata",
                event_kind="local.message", actor_id="human:1", actor_kind="human",
                occurred_at="2026-11-04T12:00:00Z", addressed="direct",
                threads=(NormalizedThread("local/repo/invalid-metadata", metadata=[]),),
                primary_thread_id="local/repo/invalid-metadata",
                body={"text": "bounded"}, metadata={"scope_row": "default"},
                verification_state="local_attested",
            ))
        with self.assertRaises(ValueError):
            receive(NormalizedTrigger(
                external_event_id="empty-channel-address",
                event_kind="local.message", actor_id="human:1", actor_kind="human",
                occurred_at="2026-11-04T12:00:00Z", addressed="direct",
                threads=(NormalizedThread("local/repo/empty-channel"),),
                primary_thread_id="local/repo/empty-channel",
                body={"text": "bounded"}, metadata={"scope_row": "default"},
                channel_address={},
                verification_state="local_attested",
            ))
        with self.assertRaises(ValueError):
            receive(NormalizedTrigger(
                external_event_id="oversized-channel-address",
                event_kind="local.message", actor_id="human:1", actor_kind="human",
                occurred_at="2026-11-04T12:00:00Z", addressed="direct",
                threads=(NormalizedThread("local/repo/oversized-channel"),),
                primary_thread_id="local/repo/oversized-channel",
                body={"text": "bounded"}, metadata={"scope_row": "default"},
                channel_address={"address": "x" * 16_385},
                verification_state="local_attested",
            ))

    def test_normalized_trigger_bounds_all_identity_fields_before_persistence(self):
        def make_trigger(**overrides):
            values = {
                "external_event_id": "bounded-event",
                "event_kind": "local.message",
                "actor_id": "human:1",
                "actor_kind": "human",
                "occurred_at": "2026-11-04T12:00:00Z",
                "addressed": "direct",
                "threads": (NormalizedThread("local/repo/bounded-thread"),),
                "primary_thread_id": "local/repo/bounded-thread",
                "body": {"text": "bounded"},
                "metadata": {"scope_row": "default"},
                "verification_state": "local_attested",
            }
            values.update(overrides)
            return NormalizedTrigger(**values)

        def receive(trigger, *, source_instance_id="local-source", adapter_version="local/1"):
            return self.host._authorization_service.receive_normalized(
                source_instance_id=source_instance_id,
                expected_source_revision=1,
                kind="local",
                adapter_version=adapter_version,
                trigger=trigger,
            )

        with self.host._store.transaction() as db:
            before = db.execute("SELECT count(*) FROM triggers").fetchone()[0]
        invalid = (
            (make_trigger(external_event_id="e" * 513), "local-source", "local/1"),
            (make_trigger(event_kind="k" * 129), "local-source", "local/1"),
            (make_trigger(actor_id="a" * 513), "local-source", "local/1"),
            (
                make_trigger(
                    threads=(NormalizedThread("t" * 1025),),
                    primary_thread_id="t" * 1025,
                ),
                "local-source", "local/1",
            ),
            (make_trigger(), "s" * 129, "local/1"),
            (make_trigger(), "local-source", "v" * 129),
            (
                make_trigger(lifecycle=ThreadLifecycleUpdate(
                    "open", provider_revision="p" * 257,
                    observed_at="2026-11-04T12:00:00Z",
                )),
                "local-source", "local/1",
            ),
        )
        for trigger, source_instance_id, adapter_version in invalid:
            with self.subTest(source_instance_id=source_instance_id, adapter_version=adapter_version):
                with self.assertRaises(ValueError):
                    receive(
                        trigger, source_instance_id=source_instance_id,
                        adapter_version=adapter_version,
                    )
        with self.host._store.transaction() as db:
            after = db.execute("SELECT count(*) FROM triggers").fetchone()[0]
        self.assertEqual(after, before)

    def test_source_registry_enforces_global_webhook_size_before_plugin_code(self):
        registry = SourceRegistry(self.host._store, register_builtins=False)

        class PermissiveSourceKind:
            kind = "permissive"
            api_version = 1

            def validate_config(self, config):
                return {}

            def identity(self, config):
                return {}

            def normalize(self, **kwargs):
                raise AssertionError("oversized input must be rejected before plugin code")

        registry.register(PermissiveSourceKind())
        registry.create_instance("permissive-source", kind="permissive", config={})
        with self.assertRaises(ValueError):
            registry.receive(
                "permissive-source", headers={}, raw=b"x" * 1_048_577,
            )
        with self.assertRaises(ValueError):
            registry.receive(
                "permissive-source", headers={"X-Large": "h" * 8193}, raw=b"x",
            )
        with self.assertRaises(ValueError):
            registry.receive(
                "permissive-source", headers={"X-Invalid": "\x00"}, raw=b"x",
            )

    def test_source_registry_bounds_instance_mapping_and_cursor_keys(self):
        with self.assertRaises(ValueError):
            self.host.sources.create_instance(
                "s" * 129, kind="local", config={"namespace": "long-id"},
            )
        with self.assertRaises(TypeError):
            self.host.sources.create_instance(
                "long-legacy", kind="local", config={"namespace": "long-legacy"},
                legacy_source_id="l" * 257,
            )
        with self.assertRaises(ValueError):
            self.host.sources.get_cursor("local-source", "k" * 257)
        with self.assertRaises(ValueError):
            self.host.sources.advance_cursor(
                "local-source", "k" * 257, {"offset": 1}, expected_revision=0,
                actor={"kind": "source", "id": "local-source"},
            )

    def test_source_cursor_revision_cas_and_idempotent_replay(self):
        self.assertIsNone(self.host.sources.get_cursor("local-source", "events"))
        first = self.host.sources.advance_cursor(
            "local-source", "events", {"offset": 1}, expected_revision=0,
            actor={"kind": "source", "id": "local-source"},
        )
        duplicate = self.host.sources.advance_cursor(
            "local-source", "events", {"offset": 1}, expected_revision=0,
            actor={"kind": "source", "id": "local-source"},
        )
        second = self.host.sources.advance_cursor(
            "local-source", "events", {"offset": 2}, expected_revision=1,
            actor={"kind": "source", "id": "local-source"},
        )
        self.assertEqual((first["revision"], first["state"]), (1, "advanced"))
        self.assertEqual((duplicate["revision"], duplicate["state"]), (1, "duplicate"))
        self.assertEqual(duplicate["updated_at"], first["updated_at"])
        self.assertEqual((second["revision"], second["state"]), (2, "advanced"))
        with self.assertRaises(RuntimeError):
            self.host.sources.advance_cursor(
                "local-source", "events", {"offset": 3}, expected_revision=1,
                actor={"kind": "source", "id": "local-source"},
            )
        current = self.host.sources.get_cursor("local-source", "events")
        self.assertEqual(current["cursor"], {"offset": 2})
        self.assertEqual(current["revision"], 2)

    def test_concurrent_source_cursor_advances_have_one_compare_and_swap_winner(self):
        self.host.sources.advance_cursor(
            "local-source", "events", {"offset": 1}, expected_revision=0,
            actor={"kind": "source", "id": "local-source"},
        )
        barrier = Barrier(2)
        outcomes = []

        def advance(offset):
            barrier.wait(timeout=5)
            try:
                self.host.sources.advance_cursor(
                    "local-source", "events", {"offset": offset}, expected_revision=1,
                    actor={"kind": "source", "id": "local-source"},
                )
            except RuntimeError:
                outcomes.append("conflict")
            else:
                outcomes.append("advanced")

        workers = [Thread(target=advance, args=(offset,)) for offset in (2, 3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertCountEqual(outcomes, ["advanced", "conflict"])
        self.assertEqual(self.host.sources.get_cursor("local-source", "events")["revision"], 2)

    def test_related_thread_subscription_cannot_escape_mandate_scope(self):
        self.publish()
        received = self.host._authorization_service.receive_normalized(
            source_instance_id="local-source", expected_source_revision=1,
            kind="local", adapter_version="test/1",
            trigger=NormalizedTrigger(
                external_event_id="multi-thread-event", event_kind="local.message",
                actor_id="human:1", actor_kind="human", occurred_at="2026-11-04T12:00:00Z",
                addressed="direct",
                threads=(
                    NormalizedThread("local/repo/work-thread", "primary"),
                    NormalizedThread("local/repo/related-thread", "related"),
                ),
                primary_thread_id="local/repo/work-thread",
                body={"text": "Fix the existing typo."}, metadata={"scope_row": "default"},
                verification_state="local_attested",
            ),
        )
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self.accept_proposal(), actor={"kind": "assessor"},
        )
        related_thread_id = "thr_" + _hash(_json(["local-source", "local/repo/related-thread"]))
        ticket = self.host.authorization.ticket(accepted["ticket_id"])
        self.assertEqual(
            [item["thread_id"] for item in ticket["subscriptions"]],
            [received["thread_ids"][0]],
        )
        self.assertNotIn(related_thread_id, [item["thread_id"] for item in ticket["subscriptions"]])

        closed_related = self.host._authorization_service.receive_normalized(
            source_instance_id="local-source", expected_source_revision=1,
            kind="local", adapter_version="test/1",
            trigger=NormalizedTrigger(
                external_event_id="close-related-thread", event_kind="local.thread.closed",
                actor_id="human:1", actor_kind="human", occurred_at="2026-11-04T13:00:00Z",
                addressed="direct",
                threads=(NormalizedThread("local/repo/related-thread", "primary"),),
                primary_thread_id="local/repo/related-thread",
                body={"text": "Close the related context thread."},
                metadata={"scope_row": "default"},
                lifecycle=ThreadLifecycleUpdate(
                    "closed", "closed", provider_revision="local/2",
                    observed_at="2026-11-04T13:00:00Z",
                ),
                verification_state="local_attested",
            ),
        )
        self.assertEqual(closed_related["gate"]["reason"], "thread_closed")
        self.assertEqual(
            self.host.sources.get_thread(related_thread_id)["source_state"], "closed",
        )
        self.assertEqual(self.host.authorization.mandate(accepted["mandate_id"])["events"], [])
        self.assertEqual(
            self.host.authorization.revalidate_mandate(
                accepted["mandate_id"], actor={"kind": "host"},
            )["status"],
            "valid",
        )

    def test_assessor_failure_records_unavailable_without_creating_ticket(self):
        self.publish()
        received = self.receive(event_id="assessor-failure")
        result = self.host.authorization.assess_next(
            received["thread_ids"][0], lambda _candidate: None, actor={"kind": "host"},
        )
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["kind"], "unavailable")
        self.assertIsNone(result["ticket_id"])
        with self.host._store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tickets").fetchone()[0], 0)

    def test_authorization_audit_queries_expose_immutable_history(self):
        old_policy = self.publish()
        _, candidate, accepted = self.accept_local_event(
            event_id="audit-history", thread_id="audit-thread",
        )
        policy = self.host.authorization.policy_revision(old_policy["policy_version"])
        self.assertEqual(policy["policy_hash"], old_policy["policy_hash"])
        self.assertEqual(policy["created_by"], {"kind": "human", "id": "policy-admin"})
        self.assertEqual(
            [item["policy_version"] for item in self.host.authorization.list_policy_revisions()],
            [old_policy["policy_version"]],
        )

        evaluation = self.host.authorization.evaluation(candidate["evaluation_id"])
        self.assertEqual(evaluation["state"], "decided")
        self.assertEqual(evaluation["outcome"]["kind"], "accept")
        self.assertEqual(evaluation["policy"]["policy_version"], old_policy["policy_version"])
        self.assertEqual(evaluation["triggers"][0]["gate"]["strength"], "explicit")
        self.assertEqual(
            self.host.authorization.list_evaluations(thread_id=candidate["thread_id"])[0]["evaluation_id"],
            candidate["evaluation_id"],
        )

        decision = self.host.authorization.decision(accepted["decision_id"])
        self.assertEqual(decision["kind"], "accept")
        self.assertEqual(decision["audit"]["strength"], "explicit")
        self.assertEqual(
            self.host.authorization.list_decisions(kind="accept")[0]["decision_id"],
            accepted["decision_id"],
        )

        mandate = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(mandate["decision_id"], accepted["decision_id"])
        with self.assertRaises(ValueError):
            self.host.authorization.mandate(accepted["mandate_id"], event_limit=0)
        self.assertEqual(mandate["scope"]["thread_ids"], [candidate["thread_id"]])
        self.assertEqual(mandate["events"], [])
        self.assertEqual(
            self.host.authorization.list_mandates(ticket_id=accepted["ticket_id"])[0]["mandate_id"],
            accepted["mandate_id"],
        )

        self.publish(mode="observe")
        revalidation = self.host.authorization.revalidate_mandate(
            accepted["mandate_id"], actor={"kind": "policy"},
        )
        self.assertEqual(revalidation["status"], "valid")
        self.assertTrue(revalidation["revalidated"])
        mandate_after = self.host.authorization.mandate(accepted["mandate_id"])
        event_types = [event["event_type"] for event in mandate_after["events"]]
        self.assertIn("affected", event_types)
        revalidated_events = [
            event for event in mandate_after["events"] if event["event_type"] == "revalidated"
        ]
        self.assertEqual(len(revalidated_events), 1)
        self.assertEqual(
            revalidated_events[0]["payload"]["policy_version"],
            self.host.authorization.current_policy()["policy_version"],
        )
        self.host.authorization.revalidate_mandate(
            accepted["mandate_id"], actor={"kind": "policy"},
        )
        mandate_after_repeat = self.host.authorization.mandate(accepted["mandate_id"])
        self.assertEqual(
            sum(event["event_type"] == "revalidated" for event in mandate_after_repeat["events"]),
            1,
        )
        with self.assertRaises(ValueError):
            self.host.authorization.list_evaluations(limit=0)
        with self.assertRaises(ValueError):
            self.host.authorization.list_decisions(kind="unknown")
        self.assertIsNone(self.host.authorization.evaluation("missing-evaluation"))
        self.assertIsNone(self.host.authorization.decision("missing-decision"))
        self.assertIsNone(self.host.authorization.mandate("missing-mandate"))

    def test_legacy_source_mapping_api_is_removed_and_current_source_is_preserved(self):
        before = self.host.sources.get_instance('local-source')
        with self.assertRaises(TypeError):
            self.host.sources.update_instance('local-source', expected_revision=1, legacy_source_id='retired')
        with self.assertRaises(TypeError):
            self.host.sources.create_instance('retired', kind='local', config={'namespace':'retired'}, legacy_source_id='retired')
        self.assertEqual(self.host.sources.get_instance('local-source'), before)
        self.assertNotIn('retired', [s.source_instance_id for s in self.host.sources.list_instances()])



if __name__ == "__main__":
    unittest.main()
