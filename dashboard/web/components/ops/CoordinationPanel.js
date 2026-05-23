// CoordinationPanel.js — Phase 9 operational panel

function ScoreBar({ label, value, max = 1 }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const color = pct >= 80 ? 'bg-up' : pct >= 50 ? 'bg-warn' : 'bg-down'
  return (
    <div>
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-muted">{label}</span>
        <span className="text-white font-mono">
          {value != null ? (max === 1 ? `${(value * 100).toFixed(0)}%` : value) : '—'}
        </span>
      </div>
      <div className="h-1.5 bg-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function CoordinationPanel({ data }) {
  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Coordination</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    leader_node_id,
    is_leader,
    election_epoch,
    quorum_health_score,
    fencing_token,
    split_brain_audit_count,
    lock_contention_count,
  } = data

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <p className="text-xs text-muted uppercase tracking-widest">Coordination</p>

      {/* Leader info */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
        <div>
          <p className="text-xs text-muted mb-1">Leader Node</p>
          <p className="text-sm font-mono text-white truncate">{leader_node_id ?? '—'}</p>
        </div>
        <div>
          <p className="text-xs text-muted mb-1">Is Leader</p>
          <span className={`px-2 py-0.5 rounded text-xs font-mono border ${
            is_leader ? 'text-up border-up bg-green-950' : 'text-muted border-border'
          }`}>
            {is_leader ? 'YES' : 'NO'}
          </span>
        </div>
        <div>
          <p className="text-xs text-muted mb-1">Election Epoch</p>
          <p className="text-lg font-mono font-bold text-accent">{election_epoch ?? '—'}</p>
        </div>
      </div>

      {/* Quorum health */}
      <ScoreBar label="Quorum Health" value={quorum_health_score} max={1} />

      {/* Fencing token */}
      <div>
        <p className="text-xs text-muted mb-1">Fencing Token</p>
        <p className="text-sm font-mono text-white">{fencing_token ?? '—'}</p>
      </div>

      {/* Counters */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-xs text-muted mb-1">Split-Brain Audits</p>
          <span className={`px-2 py-0.5 rounded text-sm font-mono border ${
            split_brain_audit_count > 0
              ? 'text-warn border-warn bg-yellow-950/50'
              : 'text-muted border-border'
          }`}>
            {split_brain_audit_count ?? 0}
          </span>
          {split_brain_audit_count > 0 && (
            <p className="text-xs text-warn mt-1">⚠ Split-brain events detected</p>
          )}
        </div>
        <div>
          <p className="text-xs text-muted mb-1">Lock Contention</p>
          <span className={`px-2 py-0.5 rounded text-sm font-mono border ${
            lock_contention_count > 0
              ? 'text-warn border-warn'
              : 'text-muted border-border'
          }`}>
            {lock_contention_count ?? 0}
          </span>
        </div>
      </div>
    </div>
  )
}
