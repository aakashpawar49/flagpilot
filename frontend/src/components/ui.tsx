import type { ReactNode } from 'react'
import type { RunStatus } from '../api'

const LABELS: Record<RunStatus, string> = {
  planning: 'Planning',
  plan_ready: 'Needs approval',
  provisioning: 'Provisioning',
  coding: 'Writing code',
  pr_open: 'PR open',
  launching: 'Launching',
  running: 'Running',
  analysing: 'Analysing',
  ship_ready: 'Needs decision',
  shipping: 'Shipping',
  shipped: 'Shipped',
  rejected: 'Rejected',
  failed: 'Failed',
}

const TONE: Record<RunStatus, string> = {
  planning: 'busy', provisioning: 'busy', coding: 'busy', launching: 'busy', analysing: 'busy', shipping: 'busy',
  plan_ready: 'attention', pr_open: 'attention', ship_ready: 'attention',
  running: 'live', shipped: 'done', rejected: 'muted', failed: 'error',
}

export function StatusPill({ status }: { status: RunStatus }) {
  return <span className={`pill pill-${TONE[status]}`}>{LABELS[status]}</span>
}

export function Card({ title, actions, children, tone }: {
  title: ReactNode
  actions?: ReactNode
  children: ReactNode
  tone?: 'attention'
}) {
  return (
    <section className={`card ${tone ? `card-${tone}` : ''}`}>
      <header className="card-head">
        <h2>{title}</h2>
        {actions && <div className="card-actions">{actions}</div>}
      </header>
      <div className="card-body">{children}</div>
    </section>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? '—' : `${(x * 100).toFixed(digits)}%`
