import { useCallback, useEffect, useState } from 'react'
import { api, type Run, type Status } from './api'
import { NewRun } from './components/NewRun'
import { RunView } from './components/RunView'
import { StatusPill } from './components/ui'

export default function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [status, setStatus] = useState<Status | null>(null)

  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch(() => {}), [])

  useEffect(() => {
    refreshRuns()
    api.status().then(setStatus).catch(() => {})
    const t = setInterval(refreshRuns, 4000)
    return () => clearInterval(t)
  }, [refreshRuns])

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◆</span>
          <div>
            <div className="brand-name">FlagPilot</div>
            <div className="brand-sub">Experiments, from prompt to rollout</div>
          </div>
        </div>
        <button className="btn btn-primary wide" onClick={() => setSelected(null)}>New experiment</button>
        <nav className="run-list" aria-label="Experiments">
          {runs.map((r) => (
            <button
              key={r.id}
              className={`run-item ${selected === r.id ? 'active' : ''}`}
              onClick={() => setSelected(r.id)}
            >
              <span className="run-item-title">{r.plan?.title ?? r.goal}</span>
              <StatusPill status={r.status} />
            </button>
          ))}
          {runs.length === 0 && <p className="muted small">No experiments yet.</p>}
        </nav>
      </aside>
      <main className="main">
        {selected ? (
          <RunView runId={selected} onChange={refreshRuns} />
        ) : (
          <NewRun
            status={status}
            onCreated={(run) => {
              refreshRuns()
              setSelected(run.id)
            }}
          />
        )}
      </main>
    </div>
  )
}
