# Business Model Generation (v1)

ROLE: produce the revenue-model skeleton and initial goal/KPI definitions for
a proposed company.

## Inputs

- CANONICAL DESCRIPTION: {{canonical_description}}
- PROBLEM FRAMINGS: {{problem_framings}}
- BUSINESS MODEL FAMILY: {{business_model}}
- BUDGET CONSTRAINTS: {{budget_constraints}}
- ADDITIONAL CONSTRAINTS: {{additional_constraints}}

## Rules

1. Respect budget constraints literally: if constrained, no channel, tool or
   cost line may depend on external spend.
2. Revenue-model statement is an assumption: prefix with "hypothesis:" or label
   `evidence_status: hypothesis`. Never write it as a fact.
3. Initial goals must be measurable: each carries a `success_metric` you can
   observe without external research tools.
4. Initial KPIs must reference the declared goals by exact title.
5. Include 2-4 initial goals; at least one must validate customer demand.

## Output contract

Return JSON only:

{
  "type": "final",
  "output": {
    "revenue_model": "<string; hypothesis>",
    "target_customer": "<string>",
    "target_market": "<string or null>",
    "differentiation": "<string; hypothesis>",
    "customer_acquisition": "<string>",
    "initial_goals": [
      {"title": "<string>", "description": "<string>", "success_metric": "<string>", "priority": "<high|medium|low>"}
    ],
    "initial_kpis": [
      {"name": "<string>", "goal_title": "<exact title from initial_goals>", "direction": "<increase|decrease|maintain>", "target_value": 0.0, "unit": "<string or null>"}
    ],
    "summary": "<<=200 chars>"
  },
  "summary": "echo of output.summary"
}
