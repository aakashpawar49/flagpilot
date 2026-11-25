# FlagPilot

**An AI experimentation engineer that runs Flagsmith A/B tests the rigorous way, with a human approving every irreversible step.**

You describe what you want to test in one sentence. FlagPilot reads your repository and your Flagsmith project, proposes a pre-registered experiment, creates the flag, variants, metric and experiment through the Flagsmith API, writes the code and verifies it builds, opens a pull request, launches, watches the data, applies a fixed stopping rule, explains the result in plain English, ships the winner, and opens the cleanup PR that removes the dead code.

Built for the WeMakeDevs × Flagsmith "Toggle Wars" hackathon, Track 2: AI Agents and Feature Flags.

## The problem

Feature-flag platforms make it easy to *run* an experiment and easy to run it *wrong*. The ways teams get burned are well known:

| Failure | What happens | How FlagPilot prevents it |
|---|---|---|
| **Peeking** | Someone checks results after a day, sees "97% chance to win", ships. With early stopping the false-positive rate climbs far above the nominal 5%. | The required sample size is computed and pre-registered before launch. The ship button does not appear until the smallest arm reaches it. Ending early needs a typed confirmation and is logged as an override. |
| **Underpowered tests** | 600 visitors to detect a 20% lift on a 10% conversion rate. The result will be inconclusive no matter what, and the team "learns" nothing. | A two-proportion power calculation (α 0.05, power 0.8) turns baseline rate and minimum detectable lift into visitors per arm, and into days at the expected traffic, while the plan is still editable. |
| **Sample ratio mismatch** | A caching layer, a bot, or an exposure recorded in only one code path skews the split. Every downstream number is wrong. | FlagPilot runs its own chi-square test of exposures against the configured split every 5 minutes. On a mismatch the verdict is `invalid` and shipping is blocked. |
| **Silent integration bugs** | The variant key in code doesn't match Flagsmith, the visitor isn't identified, or the conversion event has a typo. The experiment runs for a week collecting nothing. | Post-launch health checks flag an arm with zero exposures, exposures without conversions, and a dead event stream, with the most likely cause. |
| **Broken experiment code** | The generated change doesn't compile, the PR gets merged anyway, or the experiment launches against code that never shipped. | Every change is applied to a clean clone and built before a PR opens. Build errors go back to the model to fix. |
| **Flag debt** | The experiment ends, the flag and both code paths stay forever. | Shipping completes the experiment with the chosen arm and opens a cleanup PR that removes the losing path and the flag read. |

The language model does only what needs language: proposing the plan, writing the code, explaining the numbers. Every decision (sample size, split check, stop rule, build pass/fail) is deterministic Python that the model cannot override.

## How a run works

```
 "Test whether a 3-field signup form beats our 6-field form"
          │
          ▼
  1 PLAN       model reads repo + Flagsmith; proposes hypothesis, variants, metric,
               baseline rate, minimum detectable lift → required sample per arm
                                                          ── human reviews, edits, approves ──
  2 PROVISION  multivariate flag + keyed variants + metric + experiment (Flagsmith Admin API)
  3 CODE       model writes variants behind the flag with exposure + conversion tracking
  4 VERIFY     change built on a clean clone; failures go back to the model (≤2 fixes)
  5 PR         pull request with Experiment / What changed / How to verify / Rollout
                                                          ── human reviews, merges, launches ──
  6 RUN        experiment started; every 5 min: refresh results, SRM check, health checks,
               stopping rule
  7 DECIDE     rule says ship / keep control / keep running; model explains the numbers
                                                          ── human approves the rollout ──
  8 SHIP       experiment completed with the winner; cleanup PR removes the losing code
```

### The stopping rule

Before launch the plan fixes a baseline conversion rate *p₁* and a minimum detectable relative lift *d*. The visitors needed per arm is the standard two-sided two-proportion calculation:

```
p₂ = p₁ · (1 + d),  p̄ = (p₁ + p₂) / 2
n  = ( z₀.₉₇₅ · √(2 p̄ (1 − p̄)) + z₀.₈ · √(p₁(1 − p₁) + p₂(1 − p₂)) )² / (p₂ − p₁)²
```

For a 10% baseline and a +20% lift that is 3,841 visitors per arm. Once the smallest arm reaches *n*, FlagPilot reads Flagsmith's Bayesian inference: a variant ships only if its chance to beat control is ≥ 95% **and** the 95% credible interval on lift excludes zero. If the planned sample is reached without a clear winner, the verdict is *keep control*: continuing "until it wins" is the peeking error the rule exists to stop. All of this is in [`backend/src/flagpilot_backend/stats.py`](backend/src/flagpilot_backend/stats.py), with tests.

## How Flagsmith is used

| Step | Flagsmith feature | API |
|---|---|---|
| Plan | Existing flags and metrics, so names don't collide and metrics are reused | `GET /projects/{id}/features/`, `GET /environments/{key}/experiment-metrics/` |
| Provision | Multivariate flag with keyed variants (0% default allocation; only the experiment serves them) | `POST /projects/{id}/features/`, `POST .../features/{id}/mv-options/` |
| Provision | Experiment metric on a custom event | `POST /environments/{key}/experiment-metrics/` |
| Provision | Experiment with rollout percentage and variant weights | `POST /environments/{key}/experiments/` |
| Launch | Start the experiment | `POST .../experiments/{id}/start/` |
| Code | Exposure and conversion tracking in the app | JS SDK `useExperiment` / `getExperimentFlag`, `trackEvent`, `enableEvents: true` |
| Monitor | Recompute and read results and exposures (every 5 min) | `POST .../results/refresh/`, `GET .../results/`, `GET .../exposures/` |
| Ship | Complete the experiment serving the chosen arm | `POST .../experiments/{id}/complete/` |
| Demo | Simulated visitors through the Python SDK, labelled as simulated | `get_experiment_flag`, `track_event` |

Flagsmith Experimentation is in beta; the endpoint shapes were taken from the Flagsmith source and verified against a live account. The results payload used in tests ([`backend/tests/fixtures/results_416.json`](backend/tests/fixtures/results_416.json)) is a real capture.

## Repository layout

```
backend/    FastAPI service: pipeline, stats, verify, Flagsmith + GitHub clients, agent steps
frontend/   React + TypeScript dashboard: plan review, approvals, stopping rule, health, agent log
demo-app/   "Lumen", a sample SaaS landing page FlagPilot experiments on (6-field signup form)
```

Backend modules worth reading:

- [`pipeline.py`](backend/src/flagpilot_backend/pipeline.py): the run lifecycle; every write to Flagsmith or GitHub sits behind an approval, failed steps resume without repeating work
- [`stats.py`](backend/src/flagpilot_backend/stats.py): sample size, SRM chi-square, the stopping rule
- [`results.py`](backend/src/flagpilot_backend/results.py): parses Flagsmith's payload, runs the rule and the health checks
- [`verify.py`](backend/src/flagpilot_backend/verify.py): builds the generated change on a clean clone
- [`agent/`](backend/src/flagpilot_backend/agent/): the three model steps (plan, code, explain) as single schema-validated JSON calls with self-correction, and the provider fallback chain

## Setup

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 20+ (also used to verify builds), git, a Flagsmith account, a free Gemini API key (or any OpenAI-compatible model API), and a GitHub fine-grained token.

### 1. Flagsmith

1. Create a project and an environment at [app.flagsmith.com](https://app.flagsmith.com).
2. In **Environment Settings › Warehouse**, enable the Flagsmith warehouse so experiment events are stored.
3. Copy the environment's **client-side key** (Environment Settings › Keys).
4. Create an **Organisation API key** (Organisation Settings › API Keys). This admin key stays on the backend.

### 2. AI model

Get a free Gemini key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey). FlagPilot talks to any OpenAI-compatible API; a second provider (for example Groq) can be configured as a backup for when the free tier is overloaded. A full experiment needs about five model calls.

### 3. GitHub

Create a fine-grained personal access token with **Contents** and **Pull requests** read/write on the repository FlagPilot will change (for the demo, this one).

### 4. Run it

```bash
cd backend && cp .env.example .env   # fill in the keys
uv sync && uv run uvicorn flagpilot_backend.main:app --reload --port 8000

cd ../frontend && npm install && npm run dev      # http://localhost:5173

cd ../demo-app && cp .env.example .env            # optional: see the variants live
npm install && npm run dev                        # http://localhost:5174
```

## Using it

1. Describe the experiment. FlagPilot reads the repo and proposes a plan with a required sample size. Edit the hypothesis, metric, variants or power assumptions, then approve.
2. Watch the agent log: flag, variants, metric and experiment appear in Flagsmith; the code change is built on a clean clone; a PR opens. Review and merge it.
3. Launch. From here FlagPilot checks exposures, the split and results every 5 minutes.
4. Send traffic: real visitors to the app, or **Simulate traffic** for a demo (labelled as simulated in the log).
5. When the stopping rule is met the dashboard asks for a rollout decision and the model explains the numbers. Approve, and FlagPilot completes the experiment and opens the cleanup PR.

## Tests

```bash
cd backend && uv run pytest
```

Covers the Flagsmith client payloads, the statistics (textbook sample sizes, chi-square tail, every branch of the stopping rule), parsing of a real results payload, the resumable pipeline, build verification helpers and the model fallback chain.

## License

MIT
