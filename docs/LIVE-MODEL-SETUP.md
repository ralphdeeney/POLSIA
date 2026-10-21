# Live model setup (optional, opt-in)

Polsia runs fully offline by default: tests, the API and the default blueprint
generator need no credentials. This page is only for the **opt-in live smoke
test** and the **live blueprint evaluation**.

## 1. Where `.env` lives

The file is at the project root (next to `pyproject.toml`):

```
C:\Users\MR USER\Desktop\Project-Polsia\.env
```

It is hidden on Windows and already listed in `.gitignore`. `.env.example` is
the committed template; `.env` itself is local-only. If `.env` is missing,
create it from the template:

```powershell
Copy-Item .env.example .env
```

Check it without opening an editor:

```powershell
Get-Content .env -Force
```

## 2. Variable names Polsia actually reads

The project does **not** read `MODEL_PROVIDER`, `MODEL_BASE_URL`,
`MODEL_API_KEY` or `MODEL_NAME`. The live scripts read:

| Variable | Meaning |
| --- | --- |
| `POLSIA_LIVE_BASE_URL` | OpenAI-compatible endpoint, e.g. `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `POLSIA_LIVE_API_KEY_ENV` | **Name** of the variable that holds the key, e.g. `GEMINI_API_KEY` or `9ROUTER_API_KEY` |
| `POLSIA_LIVE_MODEL` | Exact model id served by that endpoint, e.g. `cx/gpt-5.6-sol` |
| `GEMINI_API_KEY` / `9ROUTER_API_KEY` | The secret itself (never logged, never written to reports) |

The key variable name is free-form; a name beginning with a digit
(`9ROUTER_API_KEY`) is valid for `python-dotenv`, though an identifier such as
`NINE_ROUTER_API_KEY` is less surprising in shells.

The adapter appends `/chat/completions` to the base URL, so do not include that
suffix yourself.

## 3. Configure a Google AI Studio key

1. Open Google AI Studio and create an API key in a free-tier project.
2. Open the root `.env` and set:

```dotenv
POLSIA_LIVE_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
POLSIA_LIVE_API_KEY_ENV=GEMINI_API_KEY
POLSIA_LIVE_MODEL=<exact-model-id-from-ai-studio>
GEMINI_API_KEY=<paste-the-key-here>
```

Use the exact model id your AI Studio project lists; model names change, and a
name that does not exist at the endpoint fails with a provider error. Never
commit `.env`, never paste the key into a tracked file, an issue or a chat.

Shell-exported values take precedence over `.env`, so CI can inject them
without writing a file:

```powershell
$env:POLSIA_LIVE_MODEL = "your-model-id"
$env:GEMINI_API_KEY = "your-key"
```

### Other OpenAI-compatible endpoints (9router)

Any OpenAI-compatible `/v1` endpoint works. For a 9router endpoint:

```dotenv
POLSIA_LIVE_BASE_URL=https://<router-host>/v1
POLSIA_LIVE_API_KEY_ENV=9ROUTER_API_KEY
POLSIA_LIVE_MODEL=cx/gpt-5.6-sol
9ROUTER_API_KEY=<paste-the-key-here>
```

The endpoint's `/models` route should list the configured model; a name that is
not served fails the preflight with the provider's own error. The validated run
in this repository used this shape (model `cx/gpt-5.6-sol`) through the
production `OpenAICompatibleModelProvider`, not the OpenCode bridge.

## 4. Run the bounded smoke test

```powershell
python scripts\live_model_smoke.py
```

Without configuration it prints `LIVE TEST NOT RUN` and exits 0. With
configuration it makes at most a handful of calls with hard budgets
(`max_iterations=3`, `max_tool_calls=2`, 300 s, 6,000 tokens, $0.10; 120 s per
request) and checks connection, JSON/tool-call parsing, gateway authorization,
tool execution, token accounting and trace persistence. It never posts
publicly, deploys or spends money.

If it fails it prints the provider error and any failed trace node. Override the
per-request timeout when the provider is slow:

```powershell
$env:POLSIA_LIVE_TIMEOUT = "180"
python scripts\live_model_smoke.py
```

## 5. Run the live blueprint evaluation

Quick check (one scenario, useful before spending quota on the full suite):

```powershell
python scripts\run_business_evaluations.py --live --limit=1 "C:\Users\MR USER\AppData\Local\Temp\opencode\live_eval_1.json"
```

Full suite:

```powershell
python scripts\run_business_evaluations.py --live "C:\Users\MR USER\AppData\Local\Temp\opencode\live_eval.json"
```

For a long run, execute durable chunks with `--offset` and `--limit`; each
chunk writes its own report, so an interruption only loses the active chunk:

```powershell
python scripts\run_business_evaluations.py --live --offset=0 --limit=5 "chunk_0_5.json"
python scripts\run_business_evaluations.py --live --offset=5 --limit=5 "chunk_5_10.json"
python scripts\run_business_evaluations.py --live --offset=10 --limit=5 "chunk_10_15.json"
```

Without configuration it prints `LIVE EVALUATION NOT RUN` and exits 2 with no
network call. With configuration it first makes one tiny preflight call, then
runs the same 15 labeled scenarios through `ModelBackedBlueprintGenerator`
(three versioned steps per scenario; 45+ requests plus retries).

Each scenario gets a **fresh** generator and its own budget (60k tokens, 12
model calls, 900 s), so one slow or rate-limited scenario cannot exhaust the
suite. A provider error is recorded as that scenario's result and the run
continues; results are written to the JSON report, which records
`mode: "live"`, the provider model and a redacted key.

Expect failures: the scenarios assert deterministic properties such as
"zero-budget plans contain no paid acquisition", "contradictory constraints are
surfaced" and "injected instructions never upgrade evidence". A failing live
scenario is a real evaluation result, not a test bug, and the deterministic
suite remains the reproducible CI arm.

### Quota and latency are the real constraint

Observed with `gemini-3.8-flash` through the OpenAI-compatible endpoint:

- a trivial call takes **10-40 s** (reasoning tokens are billed but the
  endpoint may report them outside `completion_tokens`);
- the free tier allows **20 requests per day** for that model. The smoke test
  fits; the full 15-scenario suite needs at least 45 successful calls and is
  therefore **not runnable on the free tier**. Use a paid key / higher-quota
  project, or accept a partial run with `--limit=N`.

A quota exhaustion returns HTTP 429 with `RESOURCE_EXHAUSTED`; the scripts print
the provider's own message (including the retry delay) so it is not mistaken for
a code failure.

Gemini 3.x attaches an opaque `thought_signature` to every function call and
rejects the next turn unless it is replayed. Polsia preserves unknown
provider-controlled `extra_content` on tool calls and echoes it on the assistant
turn; this is covered by adapter and runtime tests and needs no configuration.

### Validated runs

- **Production adapter / 9router (`cx/gpt-5.6-sol`)** — `/models` returned 200,
  the configured model was present, and the preflight passed. The 15 labeled
  scenarios ran in three contiguous chunks: **14/15** in the first pass. The
  one failure (`budget_constrained_business`) was a malformed-JSON model
  response, not a content-check failure; after the schema-repair feedback was
  improved to include the concrete parse error, the scenario passed **1/1** on
  a targeted retry. The merged report is **15/15**, digest
  `a95c47a9e61110ed`; its notes preserve the original 14/15 pass and the retry.
  Latency was roughly 60-120 s per scenario (three model calls each).
- **Gemini 3.8 Flash** — the bounded smoke test passed (one `current_date`
  tool call, 2,242 prompt + 103 completion tokens, six trace nodes). The full
  15-scenario suite was not runnable on the free tier: 20 requests/day versus
  45+ required.
- **OpenCode/DeepSeek test-only bridge** — the exploratory arm used
  `scripts/opencode_session_model.py` (`--opencode-session`) against the local
  OpenCode one-shot API because the gateway rejects direct HTTP
  (`403 FreeTierError`). It found real prompt-wiring gaps (11/15 before the
  propagation fixes) but is **not** the production transport and is not a
  substitute for the 9router result. It creates and deletes short-lived
  sessions, which can surface "Session not found" toasts in the desktop UI.
- **Real-world boundary** — every run is read-only with respect to the outside
  world: no legal entity, domain purchase, paid account, public post or spend.


## 6. The API stays deterministic

`POST /organizations/{org_id}/business-ideas/{idea_id}/blueprint` uses
`DeterministicBlueprintGenerator` unless a composition root injects one:

```python
app = create_app(..., services={"blueprint_generator": ModelBackedBlueprintGenerator(provider)})
```

There is no UI yet; all workflow operations are HTTP endpoints or service
calls. Live model use is deliberately not enabled globally by an environment
variable, so a missing key can never silently change production behaviour.
