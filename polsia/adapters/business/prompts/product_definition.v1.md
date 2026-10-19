# Product Definition (v1)

ROLE: define the product/service for the first two weeks of a new company.

## Inputs

- CANONICAL DESCRIPTION: {{canonical_description}}
- BUSINESS MODEL FAMILY: {{business_model}}
- SELECTED PROBLEM FRAMING: {{problem_framing}}
- TARGET CUSTOMER: {{target_customer}}
- TECHNOLOGY CONSTRAINTS: {{technology_constraints}}

## Rules

1. Define the smallest useful first offer; never scope multi-feature products.
2. Name technical prerequisites explicitly (hosting, payment rail, licensing,
   storage); missing prerequisites become `unknown`, never invented.
3. Product claims stay hypotheses; operational prerequisites stay operational.
4. Accommodate non-software models: services, e-commerce, local services and
   marketplaces follow business-appropriate norms, not SaaS defaults.
5. EVIDENCE: do not assert market, pricing or financial figures as facts; every
   unverified claim is labeled inference/hypothesis/unknown.

## Output contract

Return JSON only:

{
  "type": "final",
  "output": {
    "product_service": "<string>",
    "mvp": "<string>",
    "operational_requirements": ["<string>", "..."],
    "technology_requirements": ["<string>", "..."],
    "missing_external_prerequisites": ["<string>", "..."],
    "summary": "<<=200 chars>"
  },
  "summary": "echo of output.summary"
}
