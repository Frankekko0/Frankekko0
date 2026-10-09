"""Autonomy under hard limits (v3 phase 8b).

What the system may do on its own is decided in code, not in a prompt: limits, a kill switch, a dry-run
period, automatic suspension on anomalies and an independent verifier all sit between a decision and an
action. The only execution channels are a *dry run* (decide and record) and an *assisted* one (prepare the
action for the user to confirm on the marketplace): FlipFinder does not click, message or buy on Vinted
(decision Q1; Vinted's terms forbid automated tools). Everything that happens is appended to the audit log.
"""
