// SystemOverview.js — Phase 9 operational panel

function SeverityBadge({ value, map }) {
  const entry = map[value] || map['default'] || { cls: 'text-muted border-border', label: value ?? 'UNKNOWN' }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono border ${entry.cls}`}>
      {entry.label ?? value ?? 'UNKNOWN'}
    </span>
  )
}

const INTEGRITY_MAP = {
  OK:       { cls: 'text-up border-up bg-green-950',      label: 'OK'       },
  CRITICAL: { cls: 'text-down border-down bg-red-950',    label: 'CRITICAL' },
  UNKNOWN:  { cls: 'text-muted border-border',            label: 'UNKNOWN'  },
  default:  { cls: 'text-muted border-border',            label: 'UNKNOWN'  },
}

const GUARDIAN_MAP = {
  INFO:     { cls: 'text-up border-up',                   label: 'INFO'     },
  WARNING:  { cls: 'text-warn border-warn',               label: 'WARNING'  },
  CRITICAL: { cls: 'text-down border-down bg-red-950',    label: 'CRITICAL' },
  HALT:     { cls: 'text-down border-down bg-red-950 animate-pulse', label: 'HALT' },
  default:  { cls: 'text-muted border-border',            label: 'INFO'     },
}

const PHASE_STEPS = ['PENDING', 'PHASE_1', 'PHASE_2', 'PHASE_3', 'PHASE_4', 'STABLE']

function SurvivabilityGauge({ score }) {
  const s = score ?? 0
  const color = s >= 80 ? '#22c55e' : s >= 40 ? '#f59e0b' : '#ef4444'
  const textColor = s >= 80 ? 'text-up' : s >= 40 ? 'text-warn' : 'text-down'
  const pct = Math.min(100, Math.max(0, s))
  const radius = 44
  const circ = 2 * Math.PI * radius
  const dash = (pct / 100) * circ

  return (
    <div className="flex flex-col items-center">
      <svg width="110" height="60" viewBox="0 0 110 60">
        {/* background arc (half circle) */}
        <path
          d="M 10 55 A 45 45 0 0 1 100 55"
          fill="none" stroke="#2a2d3a" strokeWidth="10" strokeLinecap="round"
        />
        {/* foreground arc */}
        <path
          d="M 10 55 A 45 45 0 0 1 100 55"
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * 141.37} 141.37`}
        />
        <text x="55" y="52" textAnchor="middle" fontSize="18" fontWeight="bold" fill={color} fontFamily="monospace">
          {Math.round(s)}
        </text>
      </svg>
      <span className={`text-xs font-mono mt-1 ${textColor}`}>SURVIVABILITY</span>
    </div>
  )
}

function PhaseProgress({ phase }) {
  const idx = PHASE_STEPS.indexOf(phase)
  return (
    <div className="flex items-center gap-1">
      {PHASE_STEPS.map((step, i) => {
        const done    = i < idx
        const current = i === idx
        return (
          <div key={step} className="flex items-center gap-1">
            <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
              current ? 'bg-accent ring-1 ring-accent/50' :
              done    ? 'bg-up' : 'bg-border'
            }`} />
            <span className={`text-xs font-mono ${current ? 'text-white' : done ? 'text-up' : 'text-muted'}`}>
              {step}
            </span>
            {i < PHASE_STEPS.length - 1 && (
              <span className="text-muted text-xs">›</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ScoreBar({ label, value, max = 100, color = 'bg-accent' }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div>
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-muted">{label}</span>
        <span className="text-white font-mono">{typeof value === 'number' ? value.toFixed(1) : '—'}</span>
      </div>
      <div className="h-1.5 bg-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function SystemOverview({ data, wsConnected }) {
  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">System Overview</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    survivability_score,
    demo_mode,
    integrity_status,
    ws_health_score,
    balance_guardian_severity,
    deployment_phase,
    leader_election_state,
    active_chaos_incidents,
    active_rollback_cooldowns,
    uptime_seconds,
  } = data

  const uptimeFmt = (s) => {
    if (s == null) return '—'
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return `${h}h ${m}m`
  }

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <p className="text-xs text-muted uppercase tracking-widest mb-4">System Overview</p>

      {/* DEMO_MODE banner */}
      {demo_mode && (
        <div className="mb-4 px-3 py-2 rounded border border-warn bg-yellow-950/60 text-warn text-xs font-mono font-bold tracking-wider text-center animate-pulse">
          ⚠ DEMO / PAPER TRADING MODE ACTIVE — NO REAL ORDERS
        </div>
      )}
      {!demo_mode && (
        <div className="mb-4 px-3 py-2 rounded border border-down bg-red-950/60 text-down text-xs font-mono font-bold tracking-wider text-center">
          ⚡ LIVE TRADING MODE
        </div>
      )}

      {/* WS connection indicator */}
      <div className="flex items-center gap-1.5 mb-4">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${wsConnected ? 'bg-up animate-pulse' : 'bg-down'}`} />
        <span className={`text-xs font-mono ${wsConnected ? 'text-up' : 'text-down'}`}>
          WebSocket {wsConnected ? 'CONNECTED' : 'DISCONNECTED'}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-4">
        {/* Survivability gauge */}
        <div className="col-span-2 sm:col-span-1 flex justify-center">
          <SurvivabilityGauge score={survivability_score} />
        </div>

        {/* Status pills */}
        <div className="space-y-2">
          <div>
            <p className="text-xs text-muted mb-1">Integrity</p>
            <SeverityBadge value={integrity_status} map={INTEGRITY_MAP} />
          </div>
          <div>
            <p className="text-xs text-muted mb-1">Balance Guardian</p>
            <SeverityBadge value={balance_guardian_severity} map={GUARDIAN_MAP} />
          </div>
        </div>

        {/* WS health */}
        <div className="space-y-2">
          <div>
            <p className="text-xs text-muted mb-1">WS Health</p>
            <ScoreBar
              label=""
              value={ws_health_score != null ? ws_health_score * 100 : null}
              max={100}
              color={ws_health_score >= 0.8 ? 'bg-up' : ws_health_score >= 0.5 ? 'bg-warn' : 'bg-down'}
            />
            <span className="text-xs font-mono text-white">
              {ws_health_score != null ? (ws_health_score * 100).toFixed(0) + '%' : '—'}
            </span>
          </div>
        </div>

        {/* Incidents & Cooldowns */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">Chaos Incidents</span>
            <span className={`px-1.5 py-0.5 rounded text-xs font-mono border ${
              active_chaos_incidents > 0 ? 'text-down border-down bg-red-950' : 'text-muted border-border'
            }`}>
              {active_chaos_incidents ?? 0}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">Rollback Cooldowns</span>
            <span className={`px-1.5 py-0.5 rounded text-xs font-mono border ${
              active_rollback_cooldowns > 0 ? 'text-warn border-warn' : 'text-muted border-border'
            }`}>
              {active_rollback_cooldowns ?? 0}
            </span>
          </div>
        </div>

        {/* Leader & Uptime */}
        <div className="space-y-2">
          <div>
            <p className="text-xs text-muted">Leader</p>
            <p className="text-xs font-mono text-white truncate">{leader_election_state ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs text-muted">Uptime</p>
            <p className="text-xs font-mono text-white">{uptimeFmt(uptime_seconds)}</p>
          </div>
        </div>
      </div>

      {/* Deployment phase */}
      <div>
        <p className="text-xs text-muted mb-2">Deployment Phase</p>
        <PhaseProgress phase={deployment_phase ?? 'PENDING'} />
      </div>
    </div>
  )
}
