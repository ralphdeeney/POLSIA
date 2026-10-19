# Go-To-Market Strategy (v1)

ROLE: produce a go-to-market approach and a customer acquisition approach.

## Inputs

- PRODUCT/SERVICE: {{product_service}}
- TARGET CUSTOMER: {{target_customer}}
- BUDGET CONSTRAINTS: {{budget_constraints}}
- GEOGRAPHIC PREFERENCES: {{geo_preferences}}
- INITIAL GOALS: {{initial_goals}}

## Rules

1. Explicit budget constraints override plausible actions: no paid acquisition
   line when constrained.
2. Channel assumptions are hypotheses; do not cite a market statistic as fact.
3. Acquisition approach must be observable without external tools (e.g.,
   count of outreach messages sent, not "reach the target").
4. Tie the first acquisition action to at least one declared initial goal.
5. EVIDENCE: channel performance, conversion rates and market sizes are
   unverified; label them inference/hypothesis/unknown, never verified fact.

## Output contract

Return JSON only:

{
  "type": "final",
  "output": {
    "go_to_market": "<string>",
    "customer_acquisition": "<string>",
    "missing_external_prerequisites": ["<string>", "..."],
    "summary": "<<=200 chars>"
  },
  "summary": "echo of output.summary"
}
