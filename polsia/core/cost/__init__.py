"""Cost/usage domain (Phase 2): company-scoped UsageRecord and a
provider-neutral cost-calculation abstraction.

Usage is recorded per execution (model tokens, runtime duration, tool calls);
pricing is never hard-coded into the domain — the CostCalculator port is
implemented by adapters (null adapter in Phase 2).
"""
