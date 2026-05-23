// GovernancePanel.js — Phase 9 operational panel

const SEVERITY_MAP = {
  OK:       'text-up border-up bg-green-950',
  INFO:     'text-muted border-border',
  LOW:      'text-muted border-border',
  WARNING:  'text-warn border-warn bg-yellow-950/50',
  CRITICAL: 'text-down border-down bg-red-950',
  HIGH:     'text-down border-down',
  default:  'text-muted border-border',
}

function SeverityBadge({ value }) {
  const cls = SEVERITY_MAP[value?.toUpperCase()] ?? SEVERITY_MAP.default
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono border ${cls}`}>
      {value ?? 'UNKNOWN'}
    </span>
  )
}

export default function GovernancePanel({ data }) {
  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Governance</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    drift_severity,
    drift_findings,
    quarantined_strategies,
    alpha_durability,
    current_regime,
  } = data

  const findings = drift_findings ?? []
  const quarantined = quarantined_strategies ?? []

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <p className="text-xs text-muted uppercase tracking-widest">Governance</p>

      {/* Top row: overall severity + regime + alpha */}
      <div className="flex flex-wrap gap-4 items-start">
        <div>
          <p className="text-xs text-muted mb-1">Drift Severity</p>
          <SeverityBadge value={drift_severity} />
        </div>
        <div>
          <p className="text-xs text-muted mb-1">Current Regime</p>
          <span className="px-2 py-0.5 rounded text-xs font-mono border border-accent text-accent">
            {current_regime ?? '—'}
          </span>
        </div>
        {alpha_durability && (
          <div>
            <p className="text-xs text-muted mb-1">Alpha Durability</p>
            <span className={`px-2 py-0.5 rounded text-xs font-mono border ${
              alpha_durability === 'HIGH' ? 'text-up border-up' :
              alpha_durability === 'MEDIUM' ? 'text-warn border-warn' :
              'text-down border-down'
            }`}>
              {alpha_durability}
            </span>
          </div>
        )}
      </div>

      {/* Quarantined strategies */}
      {quarantined.length > 0 && (
        <div>
          <p className="text-xs text-muted mb-2">Quarantined Strategies</p>
          <div className="flex flex-wrap gap-2">
            {quarantined.map((s, i) => (
              <span key={i} className="px-2 py-0.5 rounded text-xs font-mono border text-down border-down bg-red-950">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Drift findings table */}
      <div>
        <p className="text-xs text-muted mb-2">Drift Findings</p>
        {findings.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="pb-2 text-left font-normal pr-4">Metric</th>
                  <th className="pb-2 text-left font-normal pr-4">Severity</th>
                  <th className="pb-2 text-left font-normal">Description</th>
                </tr>
              </thead>
              <tbody>
                {findings.map((f, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                    <td className="py-1.5 pr-4 font-mono text-white">{f.metric ?? f.name ?? '—'}</td>
                    <td className="py-1.5 pr-4">
                      <SeverityBadge value={f.severity} />
                    </td>
                    <td className="py-1.5 text-muted">{f.description ?? f.message ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-muted">No drift findings</p>
        )}
      </div>
    </div>
  )
}
