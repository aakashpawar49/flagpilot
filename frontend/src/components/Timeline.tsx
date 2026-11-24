import type { AgentEvent } from '../api'

const ICON: Record<AgentEvent['kind'], string> = {
  info: '•',
  tool: '›',
  thinking: '∴',
  approval: '✋',
  error: '!',
  success: '✓',
}

export function Timeline({ events, busy }: { events: AgentEvent[]; busy: boolean }) {
  const ordered = [...events].reverse()
  return (
    <aside className="timeline card">
      <header className="card-head">
        <h2>Agent log</h2>
        {busy && <span className="spinner" aria-label="Working" />}
      </header>
      <ol className="events">
        {ordered.map((e, i) => (
          <li key={`${e.ts}-${i}`} className={`event event-${e.kind}`}>
            <span className="event-icon" aria-hidden>{ICON[e.kind]}</span>
            <div className="event-body">
              {e.kind === 'thinking' ? (
                <details>
                  <summary>Reasoning</summary>
                  <p className="pre">{e.message}</p>
                </details>
              ) : e.kind === 'tool' ? (
                <details>
                  <summary className="mono">{e.message}</summary>
                  <pre className="pre small">{JSON.stringify(e.detail?.input ?? e.detail, null, 2)}</pre>
                </details>
              ) : (
                <p>
                  {e.message}
                  {typeof e.detail?.url === 'string' && (
                    <> <a href={e.detail.url} target="_blank" rel="noreferrer">Open ↗</a></>
                  )}
                </p>
              )}
              <time className="muted small">{new Date(e.ts).toLocaleTimeString()}</time>
            </div>
          </li>
        ))}
      </ol>
    </aside>
  )
}
