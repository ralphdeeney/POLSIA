# Company Blueprint Generation (v1)

ROLE: produce the final, validated-able company blueprint from the prior step
outputs. This prompt composes the artifact; it does not verify it.

## Inputs

- RAW IDEA (verbatim): {{raw_idea}}
- STATED MARKET: {{target_market}}
- STATED INDUSTRY: {{industry}}
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
   `capabilities`. This roster seeds company-scoped agent registration;
   authorization is applied later and never granted by this prompt.
7. Do not invent market numbers, customer counts or revenue figures.

## Output contract

Return JSON only — the full blueprint payload:

{
  "type": "final",
  "output": {
    "company_identity": {"name": "<string>", "description": "<string>"},
    "business_model": "<family from prior step>",
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
