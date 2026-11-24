# FlagPilot

FlagPilot is an AI agent that runs A/B experiments in Flagsmith from start to finish. You describe what you want to test in plain words. FlagPilot plans the experiment, sets it up in Flagsmith, writes the code, reads the results and cleans up afterwards. A human approves every step that changes something.

Built for the WeMakeDevs × Flagsmith "Toggle Wars" hackathon, Track 2: AI Agents and Feature Flags.

## The problem

Running a proper A/B test is a lot of small, error-prone steps. You have to write a hypothesis, create a multivariate flag with keyed variants, define a metric, configure the traffic split, and wire the SDK into the right components with the right identity and event names. Then you have to read Bayesian results correctly, roll out the winner, and delete the dead code. Most teams skip steps, so experiments end up biased or never get cleaned up.

## What FlagPilot does

```
"Test whether a 3-field signup form beats our 6-field form"
        │
        ▼
 1. Plan      Agent reads the repo and the Flagsmith project, proposes a hypothesis,
              variants, metric and rollout.                    ── human approves ──
 2. Provision Creates the multivariate flag, keyed variants, metric and a draft
              experiment through the Flagsmith Admin API.
 3. Code      Agent writes the variants behind the flag, adds the SDK, identity and
              conversion event, then opens a pull request.      ── human merges ──
 4. Launch    Starts the experiment; Flagsmith splits traffic.
 5. Analyse   Pulls results (lift, credible interval, chance to win, SRM) and
              explains them in plain English with a recommendation.
 6. Ship      Completes the experiment with the chosen arm and opens a cleanup PR
              that removes the losing code.                   ── human approves ──
```

The agent only ever reads during its reasoning loop. Every write to Flagsmith or GitHub happens in the backend pipeline, after an explicit approval in the UI.

## How Flagsmith is used

| Step | Flagsmith feature | API |
|---|---|---|
| Plan | Existing flags and metrics, so names don't collide | `GET /projects/{id}/features/`, `GET /environments/{key}/experiment-metrics/` |
| Provision | Multivariate flag with keyed variants | `POST /projects/{id}/features/`, `POST .../features/{id}/mv-options/` |
| Provision | Experiment metric on a custom event | `POST /environments/{key}/experiment-metrics/` |
| Provision | Experiment with rollout and variant weights | `POST /environments/{key}/experiments/` |
| Launch | Start the experiment | `POST .../experiments/{id}/start/` |
| Code | Exposure and conversion tracking in the app | JS SDK `getExperimentFlag`, `trackEvent` with `enableEvents: true` |
| Analyse | Bayesian results and exposures | `POST .../results/refresh/`, `GET .../results/`, `GET .../exposures/` |
| Ship | Roll out the winner and end the experiment | `POST .../experiments/{id}/complete/` |
| Demo | Simulated visitors through the Python SDK | `get_experiment_flag`, `track_event` |

## Repository layout

```
backend/    FastAPI service, the agent (Claude tool-use loop), Flagsmith and GitHub clients
frontend/   React + TypeScript dashboard: plan review, approvals, results, agent log
demo-app/   "Lumen", a sample signup page that FlagPilot experiments on
```

## Setup

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 20+, a Flagsmith account, an Anthropic API key, and a GitHub fine-grained token.

### 1. Flagsmith

1. Create a project and an environment at [app.flagsmith.com](https://app.flagsmith.com).
2. In **Environment Settings › Warehouse**, enable the Flagsmith warehouse so experiment events are stored.
3. Copy the environment's **client-side key** (Environment Settings › Keys).
4. Create an **Organisation API key** (Organisation Settings › API Keys). This admin key stays on the backend.

### 2. GitHub

Create a fine-grained personal access token with **Contents** and **Pull requests** read/write access to the repository FlagPilot will change (for the demo, this repository).

### 3. Backend

```bash
cd backend
cp .env.example .env     # fill in the keys
uv sync
uv run uvicorn flagpilot_backend.main:app --reload --port 8000
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev              # http://localhost:5173
```

### 5. Demo app (optional, to see the variants live)

```bash
cd demo-app
cp .env.example .env     # VITE_FLAGSMITH_ENVIRONMENT_ID = client-side key
npm install
npm run dev              # http://localhost:5174
```

## Using it

1. Open the dashboard, check that the setup list is green, and describe an experiment.
2. Review the plan. Edit the hypothesis, variant descriptions or rollout if needed, then approve.
3. Watch the agent log while FlagPilot provisions Flagsmith and opens a PR. Review and merge the PR.
4. Click **Launch experiment**.
5. Send traffic: real visitors to the demo app, or **Simulate traffic** (clearly labelled) for a quick demo.
6. Click **Fetch and analyse results**. When there is a clear answer, pick the arm to ship. FlagPilot completes the experiment and opens a cleanup PR.

## Tests

```bash
cd backend && uv run pytest
```

## Tech

- Claude (`claude-opus-5-5`) through the Anthropic Python SDK: a tool-use loop for planning and coding, and structured outputs for analysis
- FastAPI, httpx, SQLite
- Flagsmith Admin API, Flagsmith JS SDK and Python SDK (Experimentation)
- React 19, TypeScript, Vite

## License

MIT
