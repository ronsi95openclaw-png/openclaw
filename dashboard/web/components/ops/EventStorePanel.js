// EventStorePanel.js — Phase 9 operational panel

const fmtTs = (ts) => {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleTimeString()
  } catch {
    return ts
  }
}

function StatusPill({ ok, trueLabel = 'OK', falseLabel = 'MISSING' }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono border ${
      ok ? 'text-up border-up bg-green-950' : 'text-down border-down bg-red-950'
    }`}>
      {ok ? trueLabel : falseLabel}
    </span>
  )
}

export default function EventStorePanel({ data, recent }) {
  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Event Store</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    latest_seq,
    replay_divergence_count,
    checksum_ok,
    snapshot_exists,
    events_per_minute,
  } = data

  const recentEvents = recent ?? data.recent_events ?? []

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <p className="text-xs text-muted uppercase tracking-widest">Event Store</p>

      {/* Metrics row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {/* Latest seq */}
        <div>
          <p className="text-xs text-muted mb-1">Latest Seq</p>
          <p className="text-2xl font-mono font-bold text-white">
            {latest_seq != null ? latest_seq.toLocaleString() : '—'}
          </p>
        </div>

        {/* Replay divergence */}
        <div>
          <p className="text-xs text-muted mb-1">Replay Divergence</p>
          <div className="flex items-center gap-2">
            <span className={`px-2 py-0.5 rounded text-sm font-mono border ${
              replay_divergence_count > 0
                ? 'text-down border-down bg-red-950'
                : 'text-up border-up bg-green-950'
            }`}>
              {replay_divergence_count ?? 0}
            </span>
            {replay_divergence_count > 0 && (
              <span className="text-xs text-down">diverged</span>
            )}
          </div>
        </div>

        {/* Checksum */}
        <div>
          <p className="text-xs text-muted mb-1">Checksum</p>
          <StatusPill ok={checksum_ok} trueLabel="VALID" falseLabel="INVALID" />
        </div>

        {/* Snapshot */}
        <div>
          <p className="text-xs text-muted mb-1">Snapshot</p>
          <StatusPill ok={snapshot_exists} trueLabel="EXISTS" falseLabel="MISSING" />
        </div>
      </div>

      {/* Events per minute */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted">Events/min</span>
        <span className="text-sm font-mono text-white">
          {events_per_minute != null ? Number(events_per_minute).toFixed(1) : '—'}
        </span>
        <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden max-w-32">
          <div
            className="h-full bg-accent rounded-full"
            style={{ width: `${Math.min(100, ((events_per_minute ?? 0) / 100) * 100)}%` }}
          />
        </div>
      </div>

      {/* Recent events table */}
      {recentEvents.length > 0 ? (
        <div>
          <p className="text-xs text-muted mb-2">Recent Events (last 5)</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="pb-2 text-left font-normal pr-4">Seq</th>
                  <th className="pb-2 text-left font-normal pr-4">Type</th>
                  <th className="pb-2 text-left font-normal">Time</th>
                </tr>
              </thead>
              <tbody>
                {recentEvents.slice(0, 5).map((ev, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                    <td className="py-1.5 pr-4 font-mono text-accent">{ev.seq ?? '—'}</td>
                    <td className="py-1.5 pr-4 text-white">{ev.type ?? ev.event_type ?? '—'}</td>
                    <td className="py-1.5 text-muted">{fmtTs(ev.ts ?? ev.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <p className="text-xs text-muted">No recent events</p>
      )}
    </div>
  )
}
