import { useState } from 'react'
import { api, type Run } from '../api'
import { Card, Field, pct } from './ui'

const CHECK_ICON: Record<string, string> = { true: '✓', false: '✗', null: '…' }

export function Results({ run, pending, act }: {
  run: Run
  pending: boolean
  act: (fn: () => Promise<Run>) => void
}) {
  const plan = run.plan!
  const arms = [plan.control.key, ...plan.variants.map((v) => v.key)]
  const [visitors, setVisitors] = useState(600)
  const [rates, setRates] = useState<Record<string, number>>(
    Object.fromEntries(arms.map((k, i) => [k, i === 0 ? 0.1 : 0.14])),
  )
  const results = run.results
  const verdict = results?.verdict
  const rec = results?.recommendation
  const health = run.health
  const canAct = run.status === 'running' || run.status === 'ship_ready'
  const hasResults = !!results && results.variants.length > 0
  const decided = verdict?.decision === 'ship_variant' || verdict?.decision === 'keep_control'
  const recommended = verdict?.decision === 'ship_variant' ? verdict.variant_key : verdict?.decision === 'keep_control' ? 'control' : null
  const required = results?.required_per_arm ?? plan.required_per_arm
  const smallest = hasResults ? Math.min(...results.variants.map((v) => v.identities ?? 0)) : 0
  const progress = required ? Math.min(100, Math.round((100 * smallest) / required)) : 0

  return (
    <>
      <Card
        title="Stopping rule"
        tone={run.status === 'ship_ready' ? 'attention' : undefined}
        actions={
          canAct && (
            <button className="btn btn-primary" disabled={pending} onClick={() => act(() => api.analyse(run.id))}>
              Refresh and explain
            </button>
          )
        }
      >
        <p className="muted small">
          Pre-registered before launch: baseline {pct(plan.baseline_rate, 0)}, minimum detectable lift{' '}
          +{pct(plan.min_detectable_lift, 0)}, so each arm needs <strong>{required.toLocaleString()}</strong> visitors
          (α 0.05, power 0.8). FlagPilot won't call a winner before that; stopping early is how false positives happen.
        </p>
        <div className="progress" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
          <div className="progress-fill" style={{ width: `${progress}%` }} />
          <span>
            {smallest.toLocaleString()} / {required.toLocaleString()} visitors in the smallest arm ({progress}%)
          </span>
        </div>
        {verdict ? (
          <>
            <ul className="checks">
              {verdict.checks.map((c) => (
                <li key={c.name} className={`check check-${String(c.ok)}`}>
                  <span className="check-icon" aria-hidden>{CHECK_ICON[String(c.ok)]}</span>
                  <span><strong>{c.name}.</strong> {c.detail}</span>
                </li>
              ))}
            </ul>
            <div className={`recommendation rec-${verdict.decision}`}>
              <strong>{verdict.summary}</strong>
              {rec ? (
                <>
                  <p>{rec.explanation}</p>
                  {rec.caveats.length > 0 && <ul>{rec.caveats.map((c) => <li key={c}>{c}</li>)}</ul>}
                </>
              ) : (
                <p className="muted small">Click “Refresh and explain” for a plain-English reading of the numbers.</p>
              )}
            </div>
          </>
        ) : (
          <p className="muted">
            No results yet. Flagsmith needs at least 50 visitors and 5 conversions per arm before it reports inference.
            FlagPilot checks every 5 minutes.
          </p>
        )}
      </Card>

      {hasResults && (
        <Card title="Results">
          <table className="results">
            <thead>
              <tr>
                <th>Arm</th><th>Visitors</th><th>Conversions</th><th>Rate</th><th>Lift (95% interval)</th><th>Chance to win</th>
              </tr>
            </thead>
            <tbody>
              {results.variants.map((v) => (
                <tr key={v.key} className={recommended === v.key && decided ? 'winner' : ''}>
                  <td className="mono">{v.key}</td>
                  <td>{v.identities ?? '—'}</td>
                  <td>{v.conversions ?? '—'}</td>
                  <td>{pct(v.rate)}</td>
                  <td>
                    {v.key === 'control' || v.lift === null
                      ? '—'
                      : `${v.lift >= 0 ? '+' : ''}${pct(v.lift)} (${pct(v.ci_low, 0)} to ${pct(v.ci_high, 0)})`}
                  </td>
                  <td>
                    {v.chance_to_win === null ? '—' : (
                      <div className="meter" title={pct(v.chance_to_win)}>
                        <div
                          className={`meter-fill ${v.chance_to_win > 0.95 ? 'win' : v.chance_to_win < 0.05 ? 'lose' : ''}`}
                          style={{ width: `${v.chance_to_win * 100}%` }}
                        />
                        <span>{pct(v.chance_to_win, 0)}</span>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            Computed by Flagsmith {results.as_of ? `at ${new Date(results.as_of).toLocaleString()}` : ''}
            {results.srm_p_value !== null && ` · split check p = ${results.srm_p_value.toFixed(2)} (${results.srm_source})`}
          </p>
        </Card>
      )}

      {health && (
        <Card title="Integration health">
          <dl className="facts">
            <dt>Exposures</dt>
            <dd>{arms.map((a) => `${a}: ${health.exposures[a] ?? 0}`).join(' · ')}</dd>
            <dt>Conversions</dt>
            <dd>{arms.map((a) => `${a}: ${health.conversions[a] ?? 0}`).join(' · ')}</dd>
            <dt>Checked</dt>
            <dd>{new Date(health.checked_at).toLocaleTimeString()}</dd>
          </dl>
          {health.issues.length === 0 && Object.values(health.exposures).every((n) => n === 0) ? (
            <p className="muted small">
              No visitors yet. Open the app or run a simulation; FlagPilot pulls fresh numbers from Flagsmith every
              5 minutes and flags a problem if an arm stays empty.
            </p>
          ) : health.issues.length === 0 ? (
            <p className="ok-text">✓ Exposures and conversions are arriving for every arm; the split matches the plan.</p>
          ) : (
            <ul className="issues">{health.issues.map((i) => <li key={i}>{i}</li>)}</ul>
          )}
        </Card>
      )}

      {canAct && decided && (
        <Card title="Decide and ship" tone="attention">
          <p className="muted small">
            Shipping completes the experiment in Flagsmith (everyone gets the chosen arm) and opens a PR that removes
            the experiment code. A completed experiment can't be restarted. The rule recommends{' '}
            <span className="mono">{recommended}</span>; you have the final say and any override is logged.
          </p>
          <div className="row wrap">
            {arms.map((k) => (
              <button
                key={k}
                className={`btn ${recommended === k ? 'btn-primary' : 'btn-ghost'}`}
                disabled={pending}
                onClick={() => {
                  if (confirm(`Ship "${k}" to everyone and end the experiment? This can't be undone.`)) act(() => api.ship(run.id, k))
                }}
              >
                Ship {k}
              </button>
            ))}
          </div>
        </Card>
      )}

      {canAct && !decided && (
        <details className="card danger-zone">
          <summary>End the experiment early</summary>
          <div className="card-body">
            <p className="muted small">
              The stopping rule hasn't been met, so there is no evidence for either arm yet. Only do this to abandon
              the test. A completed experiment can't be restarted.
            </p>
            <div className="row wrap">
              {arms.map((k) => (
                <button
                  key={k}
                  className="btn btn-ghost"
                  disabled={pending}
                  onClick={() => {
                    const typed = prompt(`The stopping rule hasn't been met. Type END to finish the experiment and ship "${k}".`)
                    if (typed === 'END') act(() => api.ship(run.id, k, true))
                  }}
                >
                  End early and ship {k}
                </button>
              ))}
            </div>
          </div>
        </details>
      )}

      {canAct && (
        <Card title="Simulate traffic (demo)">
          <p className="muted small">
            Sends simulated visitors through the real Flagsmith SDK. Each one is evaluated against the experiment and
            converts at the rate you set. Results are labelled as simulated in the run log.
          </p>
          <div className="grid-3">
            <Field label="Visitors">
              <input type="number" min={50} max={5000} value={visitors} onChange={(e) => setVisitors(Number(e.target.value))} />
            </Field>
            {arms.map((k) => (
              <Field key={k} label={`${k} conversion rate`}>
                <input
                  type="number"
                  step={0.01}
                  min={0}
                  max={1}
                  value={rates[k]}
                  onChange={(e) => setRates({ ...rates, [k]: Number(e.target.value) })}
                />
              </Field>
            ))}
          </div>
          <div className="row end">
            <button className="btn btn-ghost" disabled={pending} onClick={() => act(() => api.simulate(run.id, visitors, rates))}>
              Run simulation
            </button>
          </div>
        </Card>
      )}
    </>
  )
}
