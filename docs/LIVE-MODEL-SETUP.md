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
| `POLSIA_LIVE_API_KEY_ENV` | **Name** of the variable that holds the key, e.g. `GEMINI_API_KEY` |
| `POLSIA_LIVE_MODEL` | Exact model id served by that endpoint |
| `GEMINI_API_KEY` | The secret itself (never logged, never written to reports) |

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

## 4. Run the bounded smoke test

```powershell
python scripts\live_model_smoke.py
```

Without configuration it prints `LIVE TEST NOT RUN` and exits 0. With
configuration it makes at most a handful of calls with hard budgets
(`max_iterations=3`, `max_tool_calls=2`, 60 s, 2,000 tokens, $0.05) and checks
connection, JSON/tool-call parsing, gateway authorization, tool execution,
token accounting and trace persistence. It never posts publicly, deploys or
spends money.

## 5. Run the 15-scenario live evaluation

```powershell
python scripts\run_business_evaluations.py --live "C:\Users\MR USER\AppData\Local\Temp\opencode\live_eval.json"
```

Without configuration it prints `LIVE EVALUATION NOT RUN` and exits 2 with no
network call. With configuration it runs the same 15 labeled scenarios through
`ModelBackedBlueprintGenerator` (three versioned steps per scenario; up to 45+
requests plus retries), bounded by 300k tokens, 200 model calls and 1800 s.

Expect failures: the scenarios assert deterministic properties such as
"zero-budget plans contain no paid acquisition", "contradictory constraints are
surfaced" and "injected instructions never upgrade evidence". A failing live
scenario is a real evaluation result, not a test bug, and the deterministic
suite remains the reproducible CI arm. The JSON report records
`mode: "live"`, the provider model and a redacted key.

## 6. The API stays deterministic

`POST /organizations/{org_id}/business-ideas/{idea_id}/blueprint` uses
`DeterministicBlueprintGenerator` unless a composition root injects one:

```python
app = create_app(..., services={"blueprint_generator": ModelBackedBlueprintGenerator(provider)})
```

There is no UI yet; all workflow operations are HTTP endpoints or service
calls. Live model use is deliberately not enabled globally by an environment
variable, so a missing key can never silently change production behaviour.
