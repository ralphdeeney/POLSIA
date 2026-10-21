# Company Blueprint Generation (v1)

ROLE: produce the final, validated-able company blueprint from the prior step
outputs. This prompt composes the artifact; it does not verify it.

## Inputs

- RAW IDEA (verbatim): {{raw_idea}}
- STATED MARKET: {{target_market}}
- STATED INDUSTRY: {{industry}}
- BUDGET CONSTRAINTS: {{budget_constraints}}
- ADDITIONAL CONSTRAINTS: {{additional_constraints}}
- GEOGRAPHIC PREFERENCES: {{geo_preferences}}
- MISSING DETAILS (from interpretation): {{missing_details}}
- COMPANY NAME (provisional): {{company_name}}
- CANONICAL DESCRIPTION: {{canonical_description}}
- PROBLEM FRAMINGS: {{problem_framings}}
- BUSINESS MODEL FAMILY: {{business_model}}
- REVENUE MODEL: {{revenue_model}}
- TARGET CUSTOMER: {{target_customer}}
- DIFFERENTIATION: {{differentiation}}
- PRODUCT/SERVICE: {{product_service}}
- MVP: {{mvp}}
- OPERATIONAL REQUIREMENTS: {{operational_requirements}}
- TECHNOLOGY REQUIREMENTS: {{technology_requirements}}
- PRICING HYPOTHESES: {{pricing_hypotheses}}
- GO-TO-MARKET: {{go_to_market}}
- CUSTOMER ACQUISITION: {{customer_acquisition}}
- INITIAL GOALS: {{initial_goals}}
- INITIAL KPIS: {{initial_kpis}}
- STARTUP RISKS: {{startup_risks}}
- REQUIRED OWNER ACTIONS: {{required_owner_actions}}

## Rules

1. Every market, competitor, pricing and financial statement must carry a
   non-verified `evidence_status` (hypothesis / inference / unknown).
2. `research_used` is always false in this step — no external research tool is
   attached. Set it to false.
3. Company identity: name must be usable as a slug piece (no special chars),
   description stays one concise sentence.
4. All sections must be non-empty. No placeholder text like "TBD".
5. `initial_kpis[].goal_title` must match an `initial_goals[].title` exactly.
6. Agent roster: 2-5 agents, each with a `name`, a `role` and non-empty
   `capabilities`. Exactly one agent must have the role `"CEO"` (the company's
   initial chief executive). This roster seeds company-scoped agent
   registration; authorization is applied later and never granted by this
   prompt.
7. Do not invent market numbers, customer counts or revenue figures.
8. Never drop an explicit constraint. When BUDGET CONSTRAINTS is not
   `<absent>`, `startup_risks` must include a statement containing "budget" or
   "spend", and `go_to_market` plus `customer_acquisition` must explicitly say
   "no paid acquisition" or "organic".
9. When constraints conflict (for example zero budget alongside a paid
   marketing plan, or an immediate global launch with no resources), include a
   `startup_risks` statement containing "conflict" or "contradiction", and a
   `required_owner_actions` item asking the owner to resolve it.
10. When MISSING DETAILS says the target customer or segment is unknown, set
    `target_customer` to a string containing "unspecified" and add a
    `required_owner_actions` item containing "segment" that asks the owner to
    define it.
11. Carry every GEOGRAPHIC PREFERENCE into `business_idea_summary` or
    `required_owner_actions`.
12. `business_model` must echo the supplied family exactly. If it is
    `<absent>`, infer it and prefer `other` when the idea is vague; never
    default to `saas` merely because software may be involved.

## Output contract

Return JSON only — the full blueprint payload:

{
  "type": "final",
  "output": {
    "company_identity": {"name": "<string>", "description": "<string>"},
    "business_model": "<exactly one of: saas, ecommerce, agency, ai_automation, local_services, digital_education, marketplace, subscription, physical_product, other; lower-case; echo the supplied family>",
    "business_idea_summary": "<string>",
    "customer_problem": "<string>",
    "target_customer": "<string>",
    "target_market": "<string or null>",
    "industry": "<string or null>",
    "value_proposition": "<string>",
    "product_service": "<string>",
    "revenue_model": "<string; hypothesis>",
    "differentiation": "<string>",
    "go_to_market": "<string>",
    "customer_acquisition": "<string>",
    "mvp": "<string>",
    "operational_requirements": ["<string>", "..."],
    "technology_requirements": ["<string>", "..."],
    "pricing_hypotheses": [{"statement": "<string>", "evidence_status": "hypothesis", "source": null}],
    "market_assumptions": [{"statement": "<string>", "evidence_status": "unknown", "source": null}],
    "competitive_landscape": [{"statement": "<string>", "evidence_status": "unknown", "source": null}],
    "financial_assumptions": [{"statement": "<string>", "evidence_status": "hypothesis", "source": null}],
    "startup_risks": [{"statement": "<string>", "evidence_status": "hypothesis", "source": null}],
    "initial_goals": [{"title": "<string>", "description": "<string>", "success_metric": "<string>", "priority": "<high|medium|low>"}],
    "initial_kpis": [{"name": "<string>", "goal_title": "<exact title>", "direction": "<increase|decrease|maintain>", "target_value": 0.0, "unit": "<string or null>"}],
    "proposed_agent_roster": [{"name": "<string>", "role": "<string>", "capabilities": ["<string>"], "description": "<string>"}],
    "required_owner_actions": ["<string>", "..."],
    "validation_requirements": ["<string>", "..."],
    "research_used": false,
    "prompt_injection_risk": false
  },
  "summary": "<<=300 chars>"
}
