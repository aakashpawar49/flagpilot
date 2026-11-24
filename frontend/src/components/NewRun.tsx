import { useEffect, useState } from 'react'
import { api, type Run, type Status } from '../api'
import { Card, Field } from './ui'

const EXAMPLES = [
  'Test whether a 3-field signup form gets more signups than our current 6-field form.',
  'Does showing the annual plan first on the pricing page increase checkouts?',
  'Try a benefit-led hero headline against the current one and measure clicks on "Get started".',
]

export function NewRun({ status, onCreated }: { status: Status | null; onCreated: (run: Run) => void }) {
  const [goal, setGoal] = useState('')
  const [repo, setRepo] = useState('')
  const [branch, setBranch] = useState('main')
  const [appPath, setAppPath] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!status) return
    setRepo((r) => r || status.defaults.repo)
    setBranch((b) => (b === 'main' ? status.defaults.base_branch || 'main' : b))
    setAppPath((p) => p || status.defaults.app_path)
  }, [status])

  const ready = status?.anthropic && status.flagsmith.ok && status.github.ok

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onCreated(await api.create({ goal, repo, base_branch: branch, app_path: appPath }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <h1>What do you want to test?</h1>
        <p className="muted">
          Describe the change in plain words. FlagPilot reads your repo and Flagsmith project, proposes an
          experiment, and waits for your approval before it creates anything.
        </p>
      </header>

      <form className="stack" onSubmit={submit}>
        <Card title="Experiment idea">
          <Field label="Idea">
            <textarea
              rows={4}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="e.g. Test whether a shorter signup form increases signups"
              required
              minLength={10}
            />
          </Field>
          <div className="chips">
            {EXAMPLES.map((ex) => (
              <button type="button" key={ex} className="chip" onClick={() => setGoal(ex)}>{ex}</button>
            ))}
          </div>
          <div className="grid-3">
            <Field label="GitHub repository" hint="owner/name">
              <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="owner/repo" required />
            </Field>
            <Field label="Base branch">
              <input value={branch} onChange={(e) => setBranch(e.target.value)} required />
            </Field>
            <Field label="App folder" hint="optional, for monorepos">
              <input value={appPath} onChange={(e) => setAppPath(e.target.value)} placeholder="apps/web" />
            </Field>
          </div>
          {error && <p className="error-text">{error}</p>}
          <div className="row end">
            <button className="btn btn-primary" disabled={busy || !ready}>
              {busy ? 'Starting…' : 'Plan experiment'}
            </button>
          </div>
        </Card>
      </form>

      <Card title="Setup">
        {!status ? (
          <p className="muted">Checking backend…</p>
        ) : (
          <ul className="checklist">
            <li className={status.anthropic ? 'ok' : 'bad'}>
              Claude ({status.model}) {status.anthropic ? 'connected' : '— set ANTHROPIC_API_KEY'}
            </li>
            <li className={status.flagsmith.ok ? 'ok' : 'bad'}>
              Flagsmith{' '}
              {status.flagsmith.ok
                ? `environment “${status.flagsmith.environment}”`
                : `— ${status.flagsmith.error}`}
            </li>
            {status.flagsmith.ok && (
              <li className={status.flagsmith.warehouse_connected ? 'ok' : 'warn'}>
                {status.flagsmith.warehouse_connected
                  ? 'Experimentation warehouse connected'
                  : 'No warehouse connected — enable it in Environment Settings › Warehouse so events are stored'}
              </li>
            )}
            <li className={status.github.ok ? 'ok' : 'bad'}>
              GitHub {status.github.ok ? `as @${status.github.login}` : `— ${status.github.error}`}
            </li>
          </ul>
        )}
      </Card>
    </div>
  )
}
