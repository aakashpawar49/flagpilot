import { useEffect, useState } from 'react'
import type { ExperimentPlan } from '../api'
import { Card, Field } from './ui'

// Mirrors backend stats.required_sample_per_arm so edits preview instantly (two-proportion z-test, α 0.05, power 0.8).
function requiredSamplePerArm(baseline: number, relativeLift: number): number {
  const p1 = baseline
  const p2 = Math.min(0.999, p1 * (1 + relativeLift))
  if (!(p1 > 0 && p1 < 1) || p2 <= p1) return NaN
  const zAlpha = 1.959964
  const zBeta = 0.841621
  const pBar = (p1 + p2) / 2
  const numerator = (zAlpha * Math.sqrt(2 * pBar * (1 - pBar)) + zBeta * Math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
  return Math.ceil(numerator / (p2 - p1) ** 2)
}

export function PlanEditor({ plan, editable, pending, onApprove, onReject }: {
  plan: ExperimentPlan
  editable: boolean
  pending: boolean
  onApprove: (plan: ExperimentPlan) => void
  onReject: () => void
}) {
  const [draft, setDraft] = useState(plan)
  // The run is re-fetched every few seconds; only reset the draft when the plan itself changed,
  // otherwise edits in progress would be wiped on every poll.
  const planKey = JSON.stringify(plan)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setDraft(plan), [planKey])

  const set = <K extends keyof ExperimentPlan>(key: K, value: ExperimentPlan[K]) => setDraft({ ...draft, [key]: value })
  const arms = [draft.control, ...draft.variants]
  const share = (100 / arms.length).toFixed(0)
  const requiredPerArm = requiredSamplePerArm(draft.baseline_rate, draft.min_detectable_lift)
  const estimatedDays = draft.expected_daily_visitors
    ? Math.round((10 * requiredPerArm * arms.length) / (draft.expected_daily_visitors * draft.rollout_percentage / 100)) / 10
    : null

  return (
    <Card
      title="Experiment plan"
      tone={editable ? 'attention' : undefined}
      actions={
        editable && (
          <>
            <button className="btn btn-ghost" disabled={pending} onClick={onReject}>Reject</button>
            <button className="btn btn-primary" disabled={pending} onClick={() => onApprove(draft)}>
              Approve and build
            </button>
          </>
        )
      }
    >
      {editable && (
        <p className="muted small">
          Approving creates the flag, variants, metric and a draft experiment in Flagsmith, then opens a PR. Nothing
          is served to users until you launch.
        </p>
      )}
      <Field label="Hypothesis">
        {editable ? (
          <textarea rows={3} value={draft.hypothesis} onChange={(e) => set('hypothesis', e.target.value)} />
        ) : (
          <p className="lead">{draft.hypothesis}</p>
        )}
      </Field>

      <div className="arms">
        {arms.map((arm, i) => (
          <div key={arm.key} className={`arm ${i === 0 ? 'arm-control' : ''}`}>
            <div className="row between">
              <span className="mono strong">{arm.key}</span>
              <span className="muted small">{share}% of enrolled</span>
            </div>
            {editable && i > 0 ? (
              <textarea
                rows={2}
                value={arm.description}
                onChange={(e) =>
                  set('variants', draft.variants.map((v) => (v.key === arm.key ? { ...v, description: e.target.value } : v)))
                }
              />
            ) : (
              <p>{arm.description}</p>
            )}
          </div>
        ))}
      </div>

      <dl className="facts">
        <dt>Flag</dt>
        <dd className="mono">{draft.flag_name}</dd>
        <dt>Primary metric</dt>
        <dd>
          {editable ? (
            <span className="row wrap">
              <input
                className="inline-input wide-inline"
                value={draft.metric.name}
                onChange={(e) => set('metric', { ...draft.metric, name: e.target.value })}
                placeholder="Metric name"
              />
              <input
                className="inline-input wide-inline mono"
                value={draft.metric.event}
                onChange={(e) => set('metric', { ...draft.metric, event: e.target.value.toLowerCase().replace(/[^a-z0-9_.:-]/g, '_') })}
                placeholder="event_name"
              />
              <select
                className="inline-input"
                value={draft.metric.direction}
                onChange={(e) => set('metric', { ...draft.metric, direction: e.target.value as 'up' | 'down' })}
              >
                <option value="up">higher is better</option>
                <option value="down">lower is better</option>
              </select>
            </span>
          ) : (
            <>
              {draft.metric.name} · <span className="mono">{draft.metric.event}</span> · {draft.metric.aggregation},{' '}
              {draft.metric.direction === 'up' ? 'higher is better' : 'lower is better'}
            </>
          )}
        </dd>
        <dt>Rollout</dt>
        <dd>
          {editable ? (
            <input
              type="number"
              min={1}
              max={100}
              className="inline-input"
              value={draft.rollout_percentage}
              onChange={(e) => set('rollout_percentage', Number(e.target.value))}
            />
          ) : (
            draft.rollout_percentage
          )}
          % of identities enter the experiment
        </dd>
        <dt>Power analysis</dt>
        <dd>
          baseline{' '}
          {editable ? (
            <input
              type="number"
              min={0.1}
              max={99}
              step={0.5}
              className="inline-input"
              value={Math.round(draft.baseline_rate * 1000) / 10}
              onChange={(e) => set('baseline_rate', Number(e.target.value) / 100)}
            />
          ) : (
            Math.round(draft.baseline_rate * 1000) / 10
          )}
          %, detect at least{' '}
          {editable ? (
            <input
              type="number"
              min={1}
              max={500}
              step={5}
              className="inline-input"
              value={Math.round(draft.min_detectable_lift * 100)}
              onChange={(e) => set('min_detectable_lift', Number(e.target.value) / 100)}
            />
          ) : (
            Math.round(draft.min_detectable_lift * 100)
          )}
          % relative lift → <strong>{requiredPerArm.toLocaleString()}</strong> visitors per arm
          {editable && (
            <>
              {' '}· daily visitors{' '}
              <input
                type="number"
                min={1}
                className="inline-input"
                placeholder="?"
                value={draft.expected_daily_visitors ?? ''}
                onChange={(e) => set('expected_daily_visitors', e.target.value ? Number(e.target.value) : null)}
              />
            </>
          )}
          {estimatedDays !== null && <> → about <strong>{estimatedDays}</strong> days</>}
        </dd>
        <dt>Files</dt>
        <dd className="mono small">{draft.files_to_change.join(', ')}</dd>
      </dl>

      <details>
        <summary>Implementation notes and risks</summary>
        <p className="pre">{draft.implementation_notes}</p>
        {draft.risks.length > 0 && (
          <ul>
            {draft.risks.map((r) => <li key={r}>{r}</li>)}
          </ul>
        )}
      </details>
    </Card>
  )
}
