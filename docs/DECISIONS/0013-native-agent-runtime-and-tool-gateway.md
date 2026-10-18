# ADR-0013: Native agent runtime and strict tool gateway

Status: accepted (Phase 4)
Supersedes-in-practice: the "Hermes adapter" slot of the Phase 4 plan Wave 2
(assignment decision: no external agent framework is mandated).

## Context

Phase 4 required a real, bounded agent executor behind the existing
`RuntimePort`/`ModelPort` contracts (`polsia/core/runtime/ports.py`). External
agent frameworks (Hermes, CrewAI, AutoGen, LangGraph) were explicitly not
required, and would add Orchestration/event/memory duplication that Polish
already owns (services/orchestration.py, event_triggers.py, memory.py).

## Decision

Implement `NativeAgentRuntime` (polsia/adapters/agent_runtime/native.py):

- an explicit state machine (`AgentRuntimeState`: INITIALIZED, RUNNING,
  WAITING_FOR_TOOL, WAITING_FOR_APPROVAL, COMPLETED, FAILED, CANCELLED,
  TIMED_OUT) — never an unbounded recursive loop;
- hard budgets per execution: max iterations, elapsed time, cumulative model
  tokens, cost (USD, provider-reported only), tool calls. Request metadata may
  only *tighten* runtime budgets (`ExecutionBudget`); the guard
  (`guard.py`) clamps and validates;
- verifiable completion contract: a run is COMPLETED only when the model emits
  the JSON final contract `{"type":"final","output":{...},"summary":...}` (or
  `allow_text_final` is explicitly enabled), **and** the declared output is
  backed by evidence: non-empty output, or a summary plus at least one
  successful tool call. A model *claiming* completion without evidence —
  especially after failed tool executions — fails with "unverifiable
  completion" and the orchestration layer records a task failure, never a
  false success;
- deterministic trace/idempotency identity: trace id == idempotency key =
  sha256(company | task | agent | skill | input | prompt-version), so retried
  executions remain correlatable; traces are sanitized metadata nodes;
- versioned prompt composition (`prompt_composer.py`) from the six templates in
  `polsia/adapters/runtime/prompts/*.md`; company context, role, objective,
  skills (from the existing Skills Registry), memory (tenant-scoped
  MemoryPort), authorized tools and budgets are bounded sections; composition
  happens in executable code, never as permission enforcement;
- cancellation (constructor `is_cancelled` predicate + terminal CANCELLED
  result; the runtime owns cancellation handling so callers always receive a
  `RuntimeResult`).

Tool execution goes exclusively through `ToolGateway`
(`tool_gateway.py`): only `read_only_file_access` tools may be registered;
shell/filesystem-write/outbound-network/payment/deployment/public-messaging
capabilities are structurally unreachable (`UNSAFE_TOOL_CAPABILITIES` +
`StrictToolCapabilityPolicy`), not prompt-negotiated. The runtime passes the
resolved `tool_permissions` decision set through: when a decision set is
present, only tools with an explicit `allow` are exposed to the model or
executable; the trusted company/task context always comes from the domain, and
model-generated ids can never override it. Tool output is untrusted data
conveyed as an observation message, truncated before it can flood context.

## Alternatives rejected

- Hermes or CrewAI as mandatory runtime: conflicts with the provider-neutral
  mandate and duplicates orchestration.
- Prompt-level safety ("the model promises to stay read-only"): rejected —
  every restriction here is enforced in executable code.
- Allowing the model to derive its own authority from a task-level ALLOW:
  rejected; tool authorization is capability+decision-set based per call.

## Limitations

- The evaluator/verification layer treats non-empty declared output as
  evidence only when tool calls were absent; content-level verification of
  free-form output against ground truth remains a Phase 5 concern.
- Race-condition handling relies on the orchestration service's existing
  single-dispatch-per-execution invariants; the runtime itself executes one
  request at a time and is safe to share across concurrent requests.
- Laya/model-backed routing cannot override any of the above: it is advisory
  (ADR-0015).

## Verification

- Runtime conformance suite (`tests/adapters/runtime_contract.py`) applied to
  both `MockRuntime` and `NativeAgentRuntime` (`report[s] == all True`).
- Native behavior tests: 18 scenarios (`test_native_runtime.py`) including
  forged-evidence rejection, budgets, cancellation, provider failure/timeout.
- Tool gateway security tests (`test_tool_gateway.py`), incl. path traversal,
  file-type whitelist, unsafe-capability refusal.
- End-to-end through the real orchestration stack
  (`tests/integration/test_native_runtime_e2e.py`): deny/approval/outage/
  forgery/tenant-isolation negative paths included.
