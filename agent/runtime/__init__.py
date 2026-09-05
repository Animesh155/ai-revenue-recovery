"""Deterministic runtime for the revenue-recovery agent.

Executes the loop INGEST -> CLASSIFY -> SHIELD -> STOP? -> PLAN -> EXECUTE -> AUDIT
by reading the declarative specs in agent/ (constraints.json, policy.escalation-path.json).
No ML. Stdlib only.
"""
