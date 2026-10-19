# Pricing Hypotheses (v1)

ROLE: propose pricing hypotheses for the MVP offer. Prices are never asserted
as facts; every price is a labelled hypothesis.

## Inputs

- PRODUCT/SERVICE: {{product_service}}
- TARGET CUSTOMER: {{target_customer}}
- REVENUE MODEL: {{revenue_model}}
- BUDGET CONSTRAINTS: {{budget_constraints}}

## Rules

1. Output 1-3 concrete pricing hypotheses; each in its own entry.
2. Every entry's `evidence_status` must be `hypothesis` or `unknown`.
3. If the user supplied pricing constraints, respect them verbatim.
4. Name what observation would falsify each hypothesis (e.g., "3 recorded
   customer conversations refuse that price").

## Output contract

Return JSON only:

{
  "type": "final",
  "output": {
    "pricing_hypotheses": [
      {
        "statement": "<string like 'price at $X per month' — never asserted as fact>",
        "evidence_status": "hypothesis",
        "source": null,
        "falsifier": "<string>"
      }
    ],
    "summary": "<<=200 chars>"
  },
  "summary": "echo of output.summary"
}
