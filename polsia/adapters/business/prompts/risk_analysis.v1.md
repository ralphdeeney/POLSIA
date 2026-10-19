# Risk Analysis (v1)

ROLE: surface startup risks and required owner actions for the proposed
company.

## Inputs

- CANONICAL DESCRIPTION: {{canonical_description}}
- BUSINESS MODEL FAMILY: {{business_model}}
- PRODUCT/SERVICE: {{product_service}}
- PRICING HYPOTHESES: {{pricing_hypotheses}}
- GO-TO-MARKET: {{go_to_market}}
- BUDGET CONSTRAINTS: {{budget_constraints}}

## Rules

1. Output 3-6 risks, ordered by likely impact (highest first).
2. Each risk gets `evidence_status` of hypothesis (or unknown when you cannot
   justify it at all). Do not use `verified_fact`.
3. Each `required_owner_action` must be a concrete, verifiable act internal to
   the OS (e.g., confirm scope, provide credentials, approve a spend ceiling);
   never "launch the company", never "spend owner money" as a default step.
4. Name any legal/industry risk as unknown until external research is done.

## Output contract

Return JSON only:

{
  "type": "final",
  "output": {
    "startup_risks": [
      {"statement": "<string>", "evidence_status": "hypothesis", "source": null}
    ],
    "required_owner_actions": ["<string>", "..."],
    "summary": "<<=200 chars>"
  },
  "summary": "echo of output.summary"
}
