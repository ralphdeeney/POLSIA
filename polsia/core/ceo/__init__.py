"""CEO domain (Phase 3): observation, prioritization, decisions, plans.

The CEO is a control-plane component on top of the Phase 2 kernel. Everything
here is deterministic and inspectable: observations are bounded typed views,
priorities are policy-scored with rationale, decisions are validated typed
objects, and no model output can override OS security.
"""
