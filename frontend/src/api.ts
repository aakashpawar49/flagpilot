// Types mirror backend/src/flagpilot_backend/models.py

export type RunStatus =
  | 'planning' | 'plan_ready' | 'provisioning' | 'coding' | 'pr_open' | 'launching'
  | 'running' | 'analysing' | 'ship_ready' | 'shipping' | 'shipped' | 'rejected' | 'failed'

export interface Variant { key: string; value: string; description: string }

export interface MetricPlan {
  name: string
  event: string
  aggregation: 'occurrence' | 'count' | 'sum' | 'mean'
  direction: 'up' | 'down'
  description: string
}

export interface ExperimentPlan {
  title: string
  hypothesis: string
  flag_name: string
  control: Variant
  variants: Variant[]
  metric: MetricPlan
  rollout_percentage: number
  baseline_rate: number
  min_detectable_lift: number
  expected_daily_visitors: number | null
  required_per_arm: number
  estimated_days: number | null
  files_to_change: string[]
  implementation_notes: string
  risks: string[]
}

export interface AgentEvent {
  ts: string
  kind: 'info' | 'tool' | 'thinking' | 'approval' | 'error' | 'success'
  message: string
  detail: Record<string, unknown> | null
}

export interface PullRequestRef { number: number; url: string; branch: string; title: string }

export interface VariantResult {
  key: string
  identities: number | null
  conversions: number | null
  rate: number | null
  lift: number | null
  ci_low: number | null
  ci_high: number | null
  chance_to_win: number | null
}

export type Decision = 'ship_variant' | 'keep_control' | 'keep_running' | 'invalid'

export interface Check { name: string; ok: boolean | null; detail: string }

export interface Verdict { decision: Decision; variant_key: string | null; summary: string; checks: Check[] }

export interface Health {
  checked_at: string
  exposures: Record<string, number>
  conversions: Record<string, number>
  srm_p_value: number | null
  issues: string[]
}

export interface Recommendation {
  decision: Decision
  variant_key: string | null
  headline: string
  explanation: string
  caveats: string[]
}

export interface ResultsSnapshot {
  fetched_at: string
  as_of: string | null
  srm_p_value: number | null
  srm_source: 'flagsmith' | 'flagpilot' | null
  required_per_arm: number | null
  exposures: Record<string, number>
  conversions: Record<string, number>
  variants: VariantResult[]
  verdict: Verdict | null
  recommendation: Recommendation | null
}

export interface FileChange { path: string; content: string | null; summary: string }

export interface Run {
  id: string
  created_at: string
  updated_at: string
  goal: string
  repo: string
  base_branch: string
  app_path: string
  status: RunStatus
  plan: ExperimentPlan | null
  file_changes: FileChange[]
  flagsmith: {
    feature_id: number | null
    option_ids: Record<string, number>
    metric_id: number | null
    experiment_id: number | null
    experiment_url: string | null
  }
  launch_pr: PullRequestRef | null
  ship_pr: PullRequestRef | null
  results: ResultsSnapshot | null
  health: Health | null
  launched_at: string | null
  shipped: string | null
  events: AgentEvent[]
  error: string | null
  failed_step: string | null
  activity: string | null
  ship_choice: string | null
}

export interface Status {
  model: string
  llm: boolean
  defaults: { repo: string; base_branch: string; app_path: string }
  flagsmith: { ok: boolean; error?: string; environment?: string; project_id?: number; warehouse_status?: string }
  github: { ok: boolean; error?: string; login?: string }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  status: () => call<Status>('GET', '/status'),
  runs: () => call<Run[]>('GET', '/runs'),
  run: (id: string) => call<Run>('GET', `/runs/${id}`),
  create: (body: { goal: string; repo: string; base_branch: string; app_path: string }) =>
    call<Run>('POST', '/runs', body),
  approvePlan: (id: string, plan: ExperimentPlan) => call<Run>('POST', `/runs/${id}/approve-plan`, plan),
  reject: (id: string) => call<Run>('POST', `/runs/${id}/reject`),
  retry: (id: string) => call<Run>('POST', `/runs/${id}/retry`),
  launch: (id: string, force = false) => call<Run>('POST', `/runs/${id}/launch?force=${force}`),
  simulate: (id: string, visitors: number, rates: Record<string, number>) =>
    call<Run>('POST', `/runs/${id}/simulate`, { visitors, rates }),
  analyse: (id: string) => call<Run>('POST', `/runs/${id}/analyse`),
  ship: (id: string, winner: string, early = false) => call<Run>('POST', `/runs/${id}/ship`, { winner, early }),
}

export const BUSY: RunStatus[] = ['planning', 'provisioning', 'coding', 'launching', 'analysing', 'shipping']
