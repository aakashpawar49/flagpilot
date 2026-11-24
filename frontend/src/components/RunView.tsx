import { useCallback, useEffect, useState } from 'react'
import { api, BUSY, type ExperimentPlan, type Run, type RunStatus } from '../api'
import { PlanEditor } from './PlanEditor'
import { Results } from './Results'
import { Timeline } from './Timeline'
import { Card, StatusPill } from './ui'

const STEPS: { label: string; statuses: RunStatus[] }[] = [
  { label: 'Plan', statuses: ['planning', 'plan_ready'] },
  { label: 'Provision', statuses: ['provisioning'] },
  { label: 'Code + PR', statuses: ['coding', 'pr_open'] },
  { label: 'Run', statuses: ['launching', 'running', 'analysing'] },
  { label: 'Decide', statuses: ['ship_ready', 'shipping'] },
  { label: 'Shipped', statuses: ['shipped'] },
]

function stepIndex(run: Run): number {
  const i = STEPS.findIndex((s) => s.statuses.includes(run.status))
  if (i >= 0) return i
  // failed / rejected: show progress up to the furthest artifact
  if (run.ship_pr) return 5
  if (run.launch_pr) return 2
  if (run.flagsmith.experiment_id) return 1
  return 0
}

export function RunView({ runId, onChange }: { runId: string; onChange: () => void }) {
  const [run, setRun] = useState<Run | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const load = useCallback(() => api.run(runId).then(setRun).catch((e) => setError(e.message)), [runId])

  useEffect(() => {
    setRun(null)
    load()
  }, [load])

  useEffect(() => {
    if (!run) return
    const busy = BUSY.includes(run.status) || !!run.activity
    const t = setInterval(load, busy ? 1500 : 5000)
    return () => clearInterval(t)
  }, [run, load])

  async function act(fn: () => Promise<Run>) {
    setPending(true)
    setError(null)
    try {
      setRun(await fn())
      onChange()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending(false)
    }
  }

  if (!run) return <div className="page"><p className="muted">Loading…</p></div>

  const current = stepIndex(run)
  const busy = BUSY.includes(run.status) || !!run.activity

  return (
    <div className="page">
      <header className="page-head">
        <div className="row between">
          <h1>{run.plan?.title ?? 'Designing experiment…'}</h1>
          <div className="row">
            {run.activity && <span className="pill pill-busy">{run.activity}…</span>}
            <StatusPill status={run.status} />
          </div>
        </div>
        <p className="muted">{run.goal}</p>
        <p className="muted small mono">{run.repo} · {run.base_branch}{run.app_path ? ` · ${run.app_path}` : ''}</p>
        <ol className="stepper" aria-label="Progress">
          {STEPS.map((s, i) => (
            <li
              key={s.label}
              className={i < current ? 'done' : i === current ? (run.status === 'failed' ? 'failed' : 'current') : ''}
            >
              {s.label}
            </li>
          ))}
        </ol>
      </header>

      {(error || run.error) && (
        <div className="banner banner-error row between">
          <span>{error ?? run.error}</span>
          {run.status === 'failed' && run.failed_step && (
            <button className="btn" disabled={pending} onClick={() => act(() => api.retry(run.id))}>
              Retry {run.failed_step}
            </button>
          )}
        </div>
      )}

      <div className="layout">
        <div className="stack">
          {run.plan && (
            <PlanEditor
              plan={run.plan}
              editable={run.status === 'plan_ready'}
              pending={pending}
              onApprove={(plan: ExperimentPlan) => act(() => api.approvePlan(run.id, plan))}
              onReject={() => act(() => api.reject(run.id))}
            />
          )}

          {(run.flagsmith.feature_id || run.launch_pr) && (
            <Card
              title="Flagsmith and code"
              tone={run.status === 'pr_open' ? 'attention' : undefined}
              actions={
                run.status === 'pr_open' && (
                  <>
                    <button className="btn btn-ghost" disabled={pending} onClick={() => act(() => api.launch(run.id, true))}>
                      Launch without merge
                    </button>
                    <button className="btn btn-primary" disabled={pending} onClick={() => act(() => api.launch(run.id))}>
                      Launch experiment
                    </button>
                  </>
                )
              }
            >
              <dl className="facts">
                {run.plan && <><dt>Flag</dt><dd className="mono">{run.plan.flag_name}</dd></>}
                {run.flagsmith.experiment_url && (
                  <><dt>Experiment</dt><dd><a href={run.flagsmith.experiment_url} target="_blank" rel="noreferrer">Open in Flagsmith ↗</a></dd></>
                )}
                {run.launch_pr && (
                  <><dt>Launch PR</dt><dd><a href={run.launch_pr.url} target="_blank" rel="noreferrer">#{run.launch_pr.number} {run.launch_pr.title} ↗</a></dd></>
                )}
                {run.ship_pr && (
                  <><dt>Cleanup PR</dt><dd><a href={run.ship_pr.url} target="_blank" rel="noreferrer">#{run.ship_pr.number} {run.ship_pr.title} ↗</a></dd></>
                )}
              </dl>
              {run.file_changes.length > 0 && (
                <ul className="files">
                  {run.file_changes.map((f) => (
                    <li key={f.path}>
                      <span className="mono">{f.path}</span>
                      <span className="muted small">{f.summary}</span>
                    </li>
                  ))}
                </ul>
              )}
              {run.status === 'pr_open' && (
                <p className="muted small">
                  Review the PR, merge it, then launch. Launching starts the experiment in Flagsmith and begins
                  splitting traffic.
                </p>
              )}
            </Card>
          )}

          {['running', 'analysing', 'ship_ready', 'shipping', 'shipped'].includes(run.status) && run.plan && (
            <Results run={run} pending={pending || busy} act={act} />
          )}
        </div>

        <Timeline events={run.events} busy={busy} />
      </div>
    </div>
  )
}
