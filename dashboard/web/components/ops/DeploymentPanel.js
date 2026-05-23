// DeploymentPanel.js — Phase 9 operational panel
import { useState } from 'react'

const PHASE_ORDER = ['PENDING', 'PHASE_1', 'PHASE_2', 'PHASE_3', 'PHASE_4', 'STABLE']

function PhaseProgress({ current }) {
  const idx = PHASE_ORDER.indexOf(current)
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {PHASE_ORDER.map((step, i) => {
        const done    = i < idx
        const active  = i === idx
        return (
          <div key={step} className="flex items-center gap-1">
            <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
              active ? 'bg-accent ring-2 ring-accent/40' :
              done   ? 'bg-up' : 'bg-border'
            }`} />
            <span className={`text-xs font-mono ${active ? 'text-white font-bold' : done ? 'text-up' : 'text-muted'}`}>
              {step}
            </span>
            {i < PHASE_ORDER.length - 1 && <span className="text-border text-xs mx-0.5">›</span>}
          </div>
        )
      })}
    </div>
  )
}

function HealthBar({ label, value, max = 100 }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const color = pct >= 80 ? 'bg-up' : pct >= 50 ? 'bg-warn' : 'bg-down'
  return (
    <div>
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-muted">{label}</span>
        <span className="text-white font-mono">{value != null ? Number(value).toFixed(0) : '—'}</span>
      </div>
      <div className="h-1.5 bg-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

const fmtTs = (ts) => {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleTimeString() } catch { return ts }
}

export default function DeploymentPanel({ data, health, onAdvancePhase }) {
  const [showConfirm, setShowConfirm] = useState(false)

  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Deployment</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    current_phase,
    release_trace_id,
    rollback_history,
    freeze_window_active,
  } = data

  const healthData = health ?? data.health ?? {}
  const {
    survivability,
    integrity,
    ws,
    latency,
    execution,
    composite_score,
  } = healthData

  const isPhase4     = current_phase === 'PHASE_4'
  const isStable     = current_phase === 'STABLE'
  const canAdvance   = !isStable && !freeze_window_active
  const needsEd25519 = isPhase4

  const handleAdvanceClick = () => {
    if (needsEd25519) return
    setShowConfirm(true)
  }

  const handleConfirm = () => {
    setShowConfirm(false)
    if (onAdvancePhase) onAdvancePhase(current_phase)
  }

  const rollbacks = rollback_history ?? []

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <p className="text-xs text-muted uppercase tracking-widest">Deployment</p>

      {/* Freeze window banner */}
      {freeze_window_active && (
        <div className="px-3 py-2 rounded border border-warn bg-yellow-950/50 text-warn text-xs font-mono">
          🔒 Freeze window active — phase advancement locked
        </div>
      )}

      {/* Phase progress */}
      <div>
        <p className="text-xs text-muted mb-2">Canary Phase</p>
        <PhaseProgress current={current_phase ?? 'PENDING'} />
      </div>

      {/* Release trace */}
      {release_trace_id && (
        <div>
          <p className="text-xs text-muted mb-1">Release Trace ID</p>
          <p className="text-xs font-mono text-accent truncate">{release_trace_id}</p>
        </div>
      )}

      {/* Health breakdown */}
      <div>
        <div className="flex justify-between items-center mb-2">
          <p className="text-xs text-muted">Composite Health</p>
          <span className={`text-sm font-mono font-bold ${
            composite_score >= 80 ? 'text-up' : composite_score >= 50 ? 'text-warn' : 'text-down'
          }`}>
            {composite_score != null ? Number(composite_score).toFixed(0) : '—'}
          </span>
        </div>
        <div className="space-y-2">
          <HealthBar label="Survivability" value={survivability} />
          <HealthBar label="Integrity"     value={integrity} />
          <HealthBar label="WebSocket"     value={ws} />
          <HealthBar label="Latency"       value={latency} />
          <HealthBar label="Execution"     value={execution} />
        </div>
      </div>

      {/* Advance phase button */}
      <div>
        {needsEd25519 ? (
          <div className="group relative">
            <button
              disabled
              className="w-full py-2 text-xs rounded border border-border text-muted opacity-50 cursor-not-allowed"
              title="Requires Ed25519 cryptographic approval"
            >
              Advance Phase → STABLE (Locked)
            </button>
            <p className="text-xs text-muted text-center mt-1">
              Requires Ed25519 cryptographic approval
            </p>
          </div>
        ) : isStable ? (
          <p className="text-xs text-muted text-center py-2">System is STABLE — no further phases</p>
        ) : (
          <button
            onClick={handleAdvanceClick}
            disabled={!canAdvance}
            className={`w-full py-2 text-xs rounded border transition ${
              canAdvance
                ? 'border-accent text-accent hover:bg-accent hover:text-white'
                : 'border-border text-muted opacity-50 cursor-not-allowed'
            }`}
          >
            Advance Phase →
          </button>
        )}
      </div>

      {/* Rollback history */}
      {rollbacks.length > 0 && (
        <div>
          <p className="text-xs text-muted mb-2">Rollback History (last 3)</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="pb-2 text-left font-normal pr-4">Time</th>
                  <th className="pb-2 text-left font-normal pr-4">From Phase</th>
                  <th className="pb-2 text-left font-normal">Reason</th>
                </tr>
              </thead>
              <tbody>
                {rollbacks.slice(0, 3).map((r, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                    <td className="py-1.5 pr-4 text-muted">{fmtTs(r.ts ?? r.timestamp)}</td>
                    <td className="py-1.5 pr-4 font-mono text-warn">{r.from_phase ?? r.phase ?? '—'}</td>
                    <td className="py-1.5 text-muted">{r.reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Confirmation modal */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
          <div className="bg-card border border-border rounded-lg p-6 max-w-sm w-full mx-4">
            <p className="text-sm font-bold text-white mb-2">Advance Phase?</p>
            <p className="text-xs text-muted mb-1">
              Current phase: <span className="text-accent font-mono">{current_phase}</span>
            </p>
            {composite_score != null && (
              <p className="text-xs text-muted mb-4">
                Composite health score: <span className={`font-mono font-bold ${
                  composite_score >= 80 ? 'text-up' : composite_score >= 50 ? 'text-warn' : 'text-down'
                }`}>{Number(composite_score).toFixed(0)}</span>
              </p>
            )}
            <p className="text-xs text-warn mb-4">
              ⚠ This will advance the canary deployment to the next phase.
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleConfirm}
                className="flex-1 py-2 text-xs bg-accent hover:bg-purple-500 text-white rounded transition"
              >
                Confirm Advance
              </button>
              <button
                onClick={() => setShowConfirm(false)}
                className="flex-1 py-2 text-xs border border-border text-muted hover:text-white rounded transition"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
