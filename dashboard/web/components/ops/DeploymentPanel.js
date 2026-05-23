// DeploymentPanel.js — Phase 10 operational panel (Phase 4 → STABLE approval UI)
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

function CopyButton({ text, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    }).catch(() => {})
  }
  return (
    <button
      onClick={handleCopy}
      className="text-xs px-2 py-0.5 rounded border border-border text-muted hover:text-white hover:border-accent transition"
    >
      {copied ? '✓ Copied' : label}
    </button>
  )
}

// ─── Canary Health Check Modal ───────────────────────────────────────────────
const CHECK_CMD = 'python3 scripts/run_canary_shadow.py --check-only --output-json'

function CanaryCheckModal({ onClose }) {
  const [pastedOutput, setPastedOutput] = useState('')
  const [verifyResult, setVerifyResult]  = useState(null)
  const [verifyError,  setVerifyError]   = useState(null)

  const handleVerify = () => {
    setVerifyResult(null)
    setVerifyError(null)
    try {
      const parsed = JSON.parse(pastedOutput.trim())
      // Surface key health fields if present
      const summary = {
        composite_score:  parsed.composite_score  ?? parsed.health?.composite_score  ?? '—',
        survivability:    parsed.survivability    ?? parsed.health?.survivability    ?? '—',
        integrity:        parsed.integrity        ?? parsed.health?.integrity        ?? '—',
        ws:               parsed.ws               ?? parsed.health?.ws               ?? '—',
        phase:            parsed.phase            ?? parsed.current_phase            ?? '—',
        status:           parsed.status           ?? '—',
      }
      setVerifyResult(summary)
    } catch {
      setVerifyError('Invalid JSON — please paste the raw JSON output from the command.')
    }
  }

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center z-50 p-4">
      <div className="bg-card border border-border rounded-lg p-6 max-w-lg w-full space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-sm font-bold text-white">Run Phase 4 Health Check</p>
          <button onClick={onClose} className="text-muted hover:text-white text-xs">✕ Close</button>
        </div>

        <p className="text-xs text-muted">
          Run this command on the deployment server, then paste the JSON output below to verify
          Phase 4 health before requesting approval.
        </p>

        {/* Command block */}
        <div className="bg-black/40 border border-border rounded p-3 flex items-center justify-between gap-2">
          <code className="text-xs font-mono text-accent break-all">{CHECK_CMD}</code>
          <CopyButton text={CHECK_CMD} />
        </div>

        {/* Paste area */}
        <div>
          <p className="text-xs text-muted mb-1">Paste JSON output here:</p>
          <textarea
            rows={6}
            value={pastedOutput}
            onChange={(e) => setPastedOutput(e.target.value)}
            placeholder='{"composite_score": 92, "phase": "PHASE_4", ...}'
            className="w-full bg-black/40 border border-border rounded p-2 text-xs font-mono text-white resize-y focus:outline-none focus:border-accent"
          />
        </div>

        {/* Verify button — NO API calls, local parse only */}
        <button
          onClick={handleVerify}
          disabled={!pastedOutput.trim()}
          className={`w-full py-2 text-xs rounded border transition ${
            pastedOutput.trim()
              ? 'border-accent text-accent hover:bg-accent hover:text-white'
              : 'border-border text-muted opacity-50 cursor-not-allowed'
          }`}
        >
          Verify Output (local parse only)
        </button>

        {/* Error */}
        {verifyError && (
          <div className="px-3 py-2 rounded border border-down bg-red-950/40 text-down text-xs font-mono">
            {verifyError}
          </div>
        )}

        {/* Result */}
        {verifyResult && (
          <div className="space-y-1.5">
            <p className="text-xs text-muted font-semibold uppercase tracking-widest">Health Scores</p>
            {Object.entries(verifyResult).map(([k, v]) => (
              <div key={k} className="flex justify-between text-xs">
                <span className="text-muted font-mono">{k}</span>
                <span className={`font-mono font-bold ${
                  typeof v === 'number' && v >= 80 ? 'text-up' :
                  typeof v === 'number' && v >= 50 ? 'text-warn' :
                  typeof v === 'number'             ? 'text-down' :
                  'text-white'
                }`}>{String(v)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Ed25519 signing steps ────────────────────────────────────────────────────
const ED_STEPS = [
  {
    num: 1,
    text: 'Generate an Ed25519 key pair (if not already done):',
    cmd: `python3 -c "from security.operator_approval import generate_key_pair; kp = generate_key_pair(); print('Public:', kp.public_key_b64); print('Private:', kp.private_key_b64)"`,
  },
  {
    num: 2,
    text: 'Register your public key with a second operator.',
    cmd: null,
  },
  {
    num: 3,
    text: "Operator 1 creates and signs an approval:",
    cmd: `python3 -c "from security.operator_approval import create_approval; ..."`,
  },
  {
    num: 4,
    text: 'Operator 2 verifies and counter-signs (quorum requires 2+ operators).',
    cmd: null,
  },
  {
    num: 5,
    text: 'Submit the approval record to:',
    cmd: `POST /admin/canary/approve\n(This endpoint requires the approval JSON with Ed25519 signatures)`,
  },
  {
    num: 6,
    text: 'Once quorum is met, deployment advances to STABLE automatically.',
    cmd: null,
  },
]

// ─── Phase 4 → STABLE Approval section ───────────────────────────────────────
function Phase4ApprovalSection({ data }) {
  const [collapsed, setCollapsed] = useState(false)
  const [showCheckModal, setShowCheckModal] = useState(false)

  const isPhase4 = (data.current_phase ?? data.phase) === 'PHASE_4'

  const releaseTraceId      = data.release_trace_id ?? 'Not available'
  const convergenceState    = data.convergence_state ?? data.deployment_convergence_state ?? 'Unknown'
  const quorumApprovals     = data.quorum_approvals
  const quorumDisplay       = quorumApprovals != null
    ? `${quorumApprovals} / 2 required`
    : '0 / 2 required'
  const freezeActive        = data.freeze_window === true || data.freeze_window_active === true
  const auditEvents         = Array.isArray(data.audit_events) ? data.audit_events.slice(-3).reverse() : []

  return (
    <div className="border border-amber-500/40 rounded-lg overflow-hidden">
      {/* Section header — always visible */}
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-amber-950/30 hover:bg-amber-950/50 transition"
      >
        <span className="text-xs font-semibold text-amber-400 uppercase tracking-widest">
          Phase 4 → STABLE Approval
        </span>
        <span className="text-amber-500 text-xs">
          {isPhase4 ? '● ACTIVE' : '○ not current phase'} {collapsed ? '▸' : '▾'}
        </span>
      </button>

      {!collapsed && (
        <div className="p-4 space-y-4 bg-black/20">

          {/* 1. Status banner */}
          <div className="px-3 py-2 rounded border border-amber-500/50 bg-amber-950/40 text-amber-300 text-xs font-mono leading-relaxed">
            ⚠ Phase 4 → STABLE Requires Ed25519 Quorum Approval — NOT available via dashboard
          </div>

          {/* 2. Freeze window warning (highest priority) */}
          {freezeActive && (
            <div className="px-3 py-2 rounded border border-down bg-red-950/50 text-down text-xs font-mono font-bold">
              🔒 DEPLOYMENT FREEZE WINDOW ACTIVE — No phase advancement permitted
            </div>
          )}

          {/* 3. Current approval status card */}
          <div className="border border-border rounded p-3 space-y-2">
            <p className="text-xs text-muted uppercase tracking-widest font-semibold mb-1">Approval Status</p>
            <div className="flex justify-between text-xs">
              <span className="text-muted">Release Trace ID</span>
              <span className="font-mono text-accent truncate max-w-[55%]">{releaseTraceId}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-muted">Convergence State</span>
              <span className="font-mono text-white">{convergenceState}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-muted">Quorum Status</span>
              <span className={`font-mono font-bold ${
                quorumApprovals >= 2 ? 'text-up' : 'text-warn'
              }`}>{quorumDisplay}</span>
            </div>
          </div>

          {/* 4. Check Canary Status button */}
          <button
            onClick={() => setShowCheckModal(true)}
            className="w-full py-2 text-xs rounded border border-amber-500/50 text-amber-400 hover:bg-amber-950/40 transition"
          >
            Check Canary Status
          </button>

          {/* 5. Ed25519 Signing Instructions */}
          <div className="border border-border rounded p-3 space-y-3">
            <p className="text-xs text-muted uppercase tracking-widest font-semibold">Ed25519 Signing Instructions</p>
            <ol className="space-y-3">
              {ED_STEPS.map((step) => (
                <li key={step.num} className="space-y-1">
                  <div className="flex gap-2 text-xs text-white">
                    <span className="text-amber-500 font-mono font-bold flex-shrink-0">{step.num}.</span>
                    <span>{step.text}</span>
                  </div>
                  {step.cmd && (
                    <div className="ml-4 bg-black/50 border border-border rounded p-2 flex items-start justify-between gap-2">
                      <pre className="text-xs font-mono text-accent whitespace-pre-wrap break-all leading-relaxed flex-1">{step.cmd}</pre>
                      <CopyButton text={step.cmd} />
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </div>

          {/* 6. Audit trail */}
          {auditEvents.length > 0 && (
            <div>
              <p className="text-xs text-muted uppercase tracking-widest font-semibold mb-2">Audit Trail (last 3)</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted border-b border-border">
                      <th className="pb-1 text-left font-normal pr-3">Time</th>
                      <th className="pb-1 text-left font-normal pr-3">Action</th>
                      <th className="pb-1 text-left font-normal">Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditEvents.map((ev, i) => (
                      <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                        <td className="py-1 pr-3 text-muted font-mono whitespace-nowrap">{fmtTs(ev.ts ?? ev.timestamp)}</td>
                        <td className="py-1 pr-3 text-white">{ev.action ?? '—'}</td>
                        <td className={`py-1 font-mono ${
                          String(ev.result).toLowerCase().includes('ok') || String(ev.result).toLowerCase().includes('pass')
                            ? 'text-up' : 'text-muted'
                        }`}>{ev.result ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Canary check modal */}
      {showCheckModal && <CanaryCheckModal onClose={() => setShowCheckModal(false)} />}
    </div>
  )
}

// ─── Main export ──────────────────────────────────────────────────────────────
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

      {/* ── Phase 4 → STABLE Approval UI (Phase 10 addition) ── */}
      <Phase4ApprovalSection data={data} />

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
