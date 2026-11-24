import { useEffect, useState } from 'react'
import type { ExperimentPlan } from '../api'
import { Card, Field } from './ui'

export function PlanEditor({ plan, editable, pending, onApprove, onReject }: {
  plan: ExperimentPlan
  editable: boolean
  pending: boolean
  onApprove: (plan: ExperimentPlan) => void
  onReject: () => void
}) {
  const [draft, setDraft] = useState(plan)
  useEffect(() => setDraft(plan), [plan])

  const set = <K extends keyof ExperimentPlan>(key: K, value: ExperimentPlan[K]) => setDraft({ ...draft, [key]: value })
  const arms = [draft.control, ...draft.variants]
  const share = (100 / arms.length).toFixed(0)

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
          {draft.metric.name} · <span className="mono">{draft.metric.event}</span> · {draft.metric.aggregation},{' '}
          {draft.metric.direction === 'up' ? 'higher is better' : 'lower is better'}
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
