import { useState } from 'react'
import { api, type Run } from '../api'
import { Card, Field, pct } from './ui'

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
  const rec = results?.recommendation
  const canAct = run.status === 'running' || run.status === 'ship_ready'
  const hasResults = !!results && results.variants.length > 0
  const recommended =
    rec?.decision === 'ship_variant' ? rec.variant_key : rec?.decision === 'keep_control' ? 'control' : null

  return (
    <>
      <Card
        title="Results"
        tone={run.status === 'ship_ready' ? 'attention' : undefined}
        actions={
          canAct && (
            <button className="btn btn-primary" disabled={pending} onClick={() => act(() => api.analyse(run.id))}>
              {run.status === 'analysing' ? 'Analysing…' : 'Fetch and analyse results'}
            </button>
          )
        }
      >
        {!results || results.variants.length === 0 ? (
          <p className="muted">
            No results yet. Flagsmith needs at least 50 identities and 5 conversions per arm, and recomputes every few
            minutes.
          </p>
        ) : (
          <>
            <table className="results">
              <thead>
                <tr>
                  <th>Arm</th><th>Visitors</th><th>Conversions</th><th>Rate</th><th>Lift (95% CI)</th><th>Chance to win</th>
                </tr>
              </thead>
              <tbody>
                {results.variants.map((v) => (
                  <tr key={v.key} className={rec?.variant_key === v.key ? 'winner' : ''}>
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
            {results.srm_p_value !== null && results.srm_p_value < 0.001 && (
              <div className="banner banner-error">
                Sample ratio mismatch (p = {results.srm_p_value.toExponential(1)}): the traffic split looks broken.
              </div>
            )}
            {results.as_of && <p className="muted small">Computed by Flagsmith at {new Date(results.as_of).toLocaleString()}</p>}
          </>
        )}

        {rec && (
          <div className={`recommendation rec-${rec.decision}`}>
            <div className="row between">
              <strong>{rec.headline}</strong>
              <span className="pill pill-muted">{rec.confidence} confidence</span>
            </div>
            <p>{rec.explanation}</p>
            {rec.caveats.length > 0 && <ul>{rec.caveats.map((c) => <li key={c}>{c}</li>)}</ul>}
          </div>
        )}
      </Card>

      {canAct && hasResults && (
        <Card title="Decide and ship" tone={run.status === 'ship_ready' ? 'attention' : undefined}>
          <p className="muted small">
            Shipping completes the experiment in Flagsmith (everyone gets the chosen arm) and opens a PR that removes
            the experiment code. A completed experiment can't be restarted.
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

      {canAct && !hasResults && (
        <details className="card danger-zone">
          <summary>End the experiment early (no results yet)</summary>
          <div className="card-body">
            <p className="muted small">
              Only do this to abandon the test. Without results there's no evidence for either arm, and a completed
              experiment can't be restarted.
            </p>
            <div className="row wrap">
              {arms.map((k) => (
                <button
                  key={k}
                  className="btn btn-ghost"
                  disabled={pending}
                  onClick={() => {
                    const typed = prompt(`There are no results yet. Type END to finish the experiment and ship "${k}".`)
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
