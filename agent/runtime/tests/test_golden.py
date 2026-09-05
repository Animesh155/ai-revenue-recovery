"""L0 golden tests: soundness (no blocked action ever chosen), permissiveness (legal actions not removed),
and regressions for F1-F4. Run: python -m agent.runtime test"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agent.runtime import audit
from agent.runtime.config import ACTIONS, IST, MONEY_MOVING, Specs
from agent.runtime.loop import Runtime, process_event
from agent.runtime.shield import evaluate
from agent.runtime.simulate import run_batch

SPECS = Specs.load()
NOW = datetime(2026, 9, 4, 13, 0, tzinfo=IST)


def base_state(**over) -> dict:
    s = {
        "event_type": "subscription.pending", "amount_paise": 99900, "rail": "card", "mandate_status": "active",
        "decline_class": "insufficient_funds", "decline_family": "soft", "mandate_category": "general", "domain": "recurring",
        "retry_owner": "merchant", "fraud_flag": False, "customer_opted_out": False, "customer_dnd": False,
        "pre_debit_notice_sent": True, "hours_since_pre_debit_notice": 24, "hours_since_last_failed_debit": 24,
        "intended_send_ist_hour": 13, "contacts_last_7d": 0, "hours_since_last_contact": None,
        "card_network": "visa", "network_retries_30d": 1, "attempts_used": 1, "payment_id_in_processed": False,
        "network_cap": 15, "max_attempts_for_class": 3,
    }
    s.update(over)
    return s


def failure(sub="sub_t", pid="pay_1", amount=99900, method="card", desc="insufficient balance", status="pending",
            at="2026-09-04T13:00:00+05:30", event="subscription.pending", context=None):
    ev = {"event": event, "occurred_at": at,
          "payload": {"subscription": {"entity": {"id": sub, "status": status}},
                      "payment": {"entity": {"id": pid, "amount": amount, "currency": "INR", "status": "failed",
                                             "method": method, "error_description": desc}}}}
    if context:
        ev["context"] = context
    return ev


def make_rt(profile_over=None) -> Runtime:
    specs = copy.deepcopy(SPECS)
    if profile_over:
        specs.profile.update(profile_over)
    tmp = Path(tempfile.mkdtemp())
    return Runtime(specs=specs, cases_dir=tmp / "cases", runs_dir=tmp / "runs", write_runs=True)


class ShieldGolden(unittest.TestCase):
    def test_permissive_baseline(self):
        r = evaluate(base_state(), SPECS.constraints)
        self.assertEqual(set(r.feasible), set(ACTIONS), "clean state must leave every action feasible")

    def test_F1_defer_to_gateway_exempt_from_pre_debit(self):
        r = evaluate(base_state(retry_owner="gateway", pre_debit_notice_sent="unknown", hours_since_pre_debit_notice=None), SPECS.constraints)
        self.assertNotIn("schedule_retry", r.feasible)
        self.assertIn("defer_to_gateway", r.feasible, "F1: gateway owns its own pre-debit compliance")

    def test_F1_pre_debit_false_blocks_only_merchant_retry(self):
        r = evaluate(base_state(pre_debit_notice_sent=False), SPECS.constraints)
        self.assertNotIn("schedule_retry", r.feasible)
        self.assertIn("defer_to_gateway", r.feasible)

    def test_unknown_mandate_fails_closed(self):
        r = evaluate(base_state(mandate_status="unknown"), SPECS.constraints)
        self.assertTrue(r.require_action)
        self.assertFalse(MONEY_MOVING & set(r.feasible))

    def test_halted_and_cancelled_require_reregister(self):
        for ms in ("halted", "cancelled"):
            r = evaluate(base_state(mandate_status=ms), SPECS.constraints)
            self.assertTrue(r.require_action, ms)
            self.assertEqual(r.requires, "mandate_reregister", ms)
            self.assertFalse(MONEY_MOVING & set(r.feasible), ms)

    def test_F3_attempts_exhausted_is_block_not_require_action(self):
        r = evaluate(base_state(attempts_used=3), SPECS.constraints)
        self.assertFalse(MONEY_MOVING & set(r.feasible))
        self.assertFalse(r.require_action, "F3: exhausted attempts must not force re-register while mandate is live")
        self.assertIn("dunning_email", r.feasible)

    def test_F4_hard_decline_never_retried(self):
        r = evaluate(base_state(decline_class="card_expired", decline_family="hard"), SPECS.constraints)
        self.assertFalse(MONEY_MOVING & set(r.feasible), "F4: no retry/defer on hard declines")
        self.assertIn("mandate_reregister", r.feasible)

    def test_afa_thresholds(self):
        self.assertTrue(evaluate(base_state(amount_paise=2000000), SPECS.constraints).require_action)
        self.assertFalse(evaluate(base_state(amount_paise=2000000, mandate_category="insurance"), SPECS.constraints).require_action)
        self.assertTrue(evaluate(base_state(amount_paise=20000000, mandate_category="insurance"), SPECS.constraints).require_action)
        self.assertTrue(evaluate(base_state(rail="upi", amount_paise=1600000), SPECS.constraints).require_action)

    def test_fraud_and_opt_out(self):
        r = evaluate(base_state(fraud_flag=True), SPECS.constraints)
        self.assertEqual(set(r.feasible), {"escalate_human", "noop"})
        r = evaluate(base_state(customer_opted_out=True), SPECS.constraints)
        self.assertFalse({"dunning_email", "dunning_whatsapp", "mandate_reregister"} & set(r.feasible))
        self.assertIn("schedule_retry", r.feasible, "opt-out is about contact, not debit authority")

    def test_trai_channels(self):
        r = evaluate(base_state(intended_send_ist_hour=22), SPECS.constraints)
        self.assertNotIn("dunning_whatsapp", r.feasible); self.assertIn("dunning_email", r.feasible)
        r = evaluate(base_state(customer_dnd=True), SPECS.constraints)
        self.assertNotIn("dunning_whatsapp", r.feasible); self.assertIn("dunning_email", r.feasible)
        r = evaluate(base_state(customer_dnd="unknown"), SPECS.constraints)
        self.assertNotIn("dunning_whatsapp", r.feasible); self.assertIn("mandate_reregister", r.feasible, "transactional re-register survives DND-unknown")
        r = evaluate(base_state(contacts_last_7d=3), SPECS.constraints)
        self.assertFalse({"dunning_email", "dunning_whatsapp", "mandate_reregister"} & set(r.feasible))
        r = evaluate(base_state(hours_since_last_contact=5), SPECS.constraints)
        self.assertIn("dunning_email", r.deferred_only)

    def test_scheme_cap_and_24h(self):
        r = evaluate(base_state(network_retries_30d=15), SPECS.constraints)
        self.assertFalse(MONEY_MOVING & set(r.feasible))
        r = evaluate(base_state(hours_since_last_failed_debit=6), SPECS.constraints)
        self.assertIn("schedule_retry", r.deferred_only)
        self.assertIn("defer_to_gateway", r.feasible)

    def test_duplicate_is_noop_only(self):
        r = evaluate(base_state(payment_id_in_processed=True), SPECS.constraints)
        self.assertEqual(r.feasible, ["noop"])


class LoopGolden(unittest.TestCase):
    def test_F2_pending_infers_active_mandate(self):
        rt = make_rt({"retry_owner": "gateway"})
        r = process_event(failure(), rt)
        self.assertEqual(r.cls["mandate_status"], "active")
        self.assertFalse(r.shield.require_action)
        self.assertEqual(r.action, "defer_to_gateway")

    def test_escalation_across_ticks_and_attribution(self):
        rt = make_rt({"retry_owner": "gateway"})
        process_event(failure(pid="p1", at="2026-09-04T13:00:00+05:30"), rt)
        r2 = process_event(failure(pid="p2", at="2026-09-05T13:00:00+05:30"), rt)
        self.assertEqual(r2.action, "defer_to_gateway")
        r3 = process_event(failure(pid="p3", at="2026-09-06T13:00:00+05:30"), rt)
        self.assertEqual(r3.action, "dunning_email", "F3: after exhausted debits the cheapest contact wins")
        rec = {"event": "subscription.charged", "occurred_at": "2026-09-07T10:00:00+05:30", "via": "customer_action",
               "payload": {"subscription": {"entity": {"id": "sub_t", "status": "active"}},
                           "payment": {"entity": {"id": "p_ok", "amount": 99900, "method": "card"}}}}
        r4 = process_event(rec, rt)
        self.assertEqual(r4.case["status"], "recovered")
        self.assertEqual(r4.case["attribution"], "agent_attributed")

    def test_gateway_recovery_is_not_our_win(self):
        rt = make_rt({"retry_owner": "gateway"})
        process_event(failure(pid="p1"), rt)
        rec = {"event": "subscription.charged", "occurred_at": "2026-09-05T13:00:00+05:30",
               "payload": {"subscription": {"entity": {"id": "sub_t", "status": "active"}},
                           "payment": {"entity": {"id": "p_ok", "amount": 99900, "method": "card"}}}}
        r = process_event(rec, rt)
        self.assertEqual(r.case["attribution"], "gateway_attributed")

    def test_ev_floor_small_amount_never_escalates_to_human(self):
        rt = make_rt({"retry_owner": "gateway"})
        r = process_event(failure(amount=4900, desc="Payment processing failed"), rt)  # unknown class -> fallback human
        # unknown decline goes to policy default regardless of EV (explicit fallback), so test the floor on a known class:
        rt2 = make_rt({"retry_owner": "gateway"})
        r = process_event(failure(amount=4900, desc="The card has expired", context={"customer_opted_out": True}), rt2)
        self.assertNotEqual(r.action, "escalate_human")
        self.assertIn(r.case["status"], ("stopped_ev", "noop_closed", "stopped_tiers"))

    def test_horizon_stop(self):
        rt = make_rt({"retry_owner": "gateway", "recovery_horizon_days": 21})
        process_event(failure(pid="p1", at="2026-09-04T13:00:00+05:30"), rt)
        r = process_event({"event": "tick", "subscription_id": "sub_t", "occurred_at": "2026-09-26T13:00:00+05:30"}, rt)
        self.assertEqual(r.case["status"], "stopped_horizon")

    def test_duplicate_webhook_does_not_mutate_case(self):
        rt = make_rt({"retry_owner": "gateway"})
        r1 = process_event(failure(pid="p1"), rt)
        before = json.dumps(r1.case, sort_keys=True)
        r2 = process_event(failure(pid="p1", at="2026-09-04T13:05:00+05:30"), rt)
        self.assertEqual(r2.action, "noop")
        after = json.dumps({**r2.case, "last_event_type": r1.case["last_event_type"]}, sort_keys=True)
        self.assertEqual(before, after)

    def test_latency_guard_trips_shield(self):
        """If policy ever schedules a merchant debit <24h out, the RBI window constraint must veto it."""
        rt = make_rt({"retry_owner": "merchant"})
        rt.specs.tier("schedule_retry")["latency_hours"] = 12
        r = process_event(failure(), rt)
        self.assertNotIn("schedule_retry", r.shield.feasible)
        self.assertIn("rbi_pre_debit_window_not_elapsed", r.shield.blocked.get("schedule_retry", []))


class DetectorGolden(unittest.TestCase):
    SNAP = Path(__file__).resolve().parents[2] / "samples" / "snapshot.mixed.json"

    def test_snapshot_detection(self):
        from agent.runtime.detect import detect
        risks = detect(json.loads(self.SNAP.read_text()))
        ids = {r["subscription_id"] for r in risks}
        self.assertEqual(len(risks), 8)
        self.assertNotIn("sub_healthy_05", ids, "healthy subscription must not be flagged")
        self.assertNotIn("order_fresh_10", ids, "10-minute-old order is not abandoned")
        self.assertEqual(sum(r["amount_paise"] for r in risks), 9499500)
        by_rule = {r["rule_id"]: r for r in risks}
        self.assertEqual(by_rule["next_charge_exceeds_mandate_max"]["subscription_id"], "sub_max_breach_03")
        self.assertEqual(by_rule["invoice_overdue"]["domain"], "receivables")

    def test_pre_failure_and_non_recurring_scope(self):
        r = evaluate(base_state(decline_family="pre_failure", decline_class="risk_card_expiring"), SPECS.constraints)
        self.assertFalse(MONEY_MOVING & set(r.feasible)); self.assertIn("mandate_reregister", r.feasible)
        r = evaluate(base_state(domain="receivables", mandate_status="n/a", decline_family="non_recurring", decline_class="invoice_overdue", amount_paise=4800000), SPECS.constraints)
        self.assertFalse(MONEY_MOVING & set(r.feasible))
        self.assertNotIn("mandate_reregister", r.feasible, "no mandate concept outside recurring")
        self.assertFalse(r.require_action, "RBI AFA threshold must not fire on an invoice")
        self.assertIn("dunning_email", r.feasible)
        r = evaluate(base_state(domain="checkout", mandate_status="n/a", decline_family="non_recurring", decline_class="checkout_abandoned"), SPECS.constraints)
        self.assertFalse(r.require_action, "mandate fail-closed must not apply to checkout")

    def test_detector_events_through_loop(self):
        from agent.runtime.detect import detect
        rt = make_rt({"retry_owner": "gateway"})
        risks = {r["subscription_id"]: r for r in detect(json.loads(self.SNAP.read_text()))}
        res = process_event(risks["inv_overdue_08"], rt)
        self.assertEqual(res.case["domain"], "receivables"); self.assertEqual(res.case["origin"], "detector")
        self.assertEqual(res.action, "dunning_email")
        res = process_event(risks["sub_stale_pending_06"], rt)
        self.assertEqual(res.action, "defer_to_gateway", "stale pending reuses the reactive soft-decline path")
        res = process_event(risks["sub_card_exp_01"], rt)
        self.assertNotIn(res.action, MONEY_MOVING)


class BatchInvariants(unittest.TestCase):
    def test_soundness_over_batch_and_audit_chain(self):
        tmp = Path(tempfile.mkdtemp())
        rep = run_batch(40, 7, "t", out_root=tmp)
        self.assertEqual(rep["constraint_violations"], 0)
        ok, bad, n = audit.verify(tmp / "t" / "audit.log")
        self.assertTrue(ok and n > 40)
        # every acted entry's action was in its feasible set
        for line in (tmp / "t" / "audit.log").read_text().splitlines():
            e = json.loads(line)
            if e.get("decision") == "act":
                self.assertIn(e["action"], e["feasible"], e)
        # tamper -> chain breaks
        p = tmp / "t" / "audit.log"
        lines = p.read_text().splitlines()
        e = json.loads(lines[3]); e["action"] = "schedule_retry"; lines[3] = json.dumps(e, separators=(",", ":"))
        p.write_text("\n".join(lines) + "\n")
        ok, bad, _ = audit.verify(p)
        self.assertFalse(ok); self.assertEqual(bad, 3)


if __name__ == "__main__":
    unittest.main()
