// ExecutionPanel.js — Phase 9 operational panel

function ScoreBar({ label, value, max = 1, color }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const barColor = color || (value / max > 0.7 ? 'bg-down' : value / max > 0.4 ? 'bg-warn' : 'bg-up')
  return (
    <div>
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-muted">{label}</span>
        <span className="text-white font-mono">{typeof value === 'number' ? value.toFixed(3) : '—'}</span>
      </div>
      <div className="h-1.5 bg-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function LatencyRow({ op }) {
  const fmt = (v) => v != null ? `${Number(v).toFixed(1)}ms` : '—'
  return (
    <tr className="border-b border-border/30 hover:bg-white/5 transition">
      <td className="py-1.5 pr-4 text-white font-mono text-xs">{op.operation ?? op.name ?? '—'}</td>
      <td className="py-1.5 pr-4 text-xs font-mono text-muted">{fmt(op.p50)}</td>
      <td className="py-1.5 pr-4 text-xs font-mono text-warn">{fmt(op.p95)}</td>
      <td className="py-1.5 pr-4 text-xs font-mono text-down">{fmt(op.p99)}</td>
      <td className="py-1.5 text-xs font-mono text-muted">{op.sample_count ?? '—'}</td>
    </tr>
  )
}

export default function ExecutionPanel({ data }) {
  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Execution</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    latency_table,
    exchange_degradation_score,
    slippage_ewma,
    fill_rate,
  } = data

  const degradation = exchange_degradation_score ?? 0
  const degradationColor = degradation > 0.7 ? 'bg-down' : degradation > 0.4 ? 'bg-warn' : 'bg-up'

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <p className="text-xs text-muted uppercase tracking-widest">Execution</p>

      {/* Exchange degradation */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
        <div>
          <p className="text-xs text-muted mb-1">Exchange Degradation</p>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-2 bg-border rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${degradationColor}`}
                style={{ width: `${Math.min(100, degradation * 100)}%` }}
              />
            </div>
            <span className={`text-xs font-mono font-bold ${
              degradation > 0.7 ? 'text-down' : degradation > 0.4 ? 'text-warn' : 'text-up'
            }`}>
              {degradation.toFixed(2)}
            </span>
          </div>
          {degradation > 0.7 && (
            <p className="text-xs text-down mt-1">⚠ Exchange degraded — high risk</p>
          )}
        </div>

        <div>
          <p className="text-xs text-muted mb-1">Fill Rate</p>
          <p className={`text-lg font-mono font-bold ${
            fill_rate >= 0.9 ? 'text-up' : fill_rate >= 0.7 ? 'text-warn' : 'text-down'
          }`}>
            {fill_rate != null ? `${(fill_rate * 100).toFixed(1)}%` : '—'}
          </p>
        </div>

        <div>
          <p className="text-xs text-muted mb-1">Slippage EWMA</p>
          {slippage_ewma != null ? (
            <p className={`text-lg font-mono font-bold ${slippage_ewma > 0.001 ? 'text-down' : 'text-up'}`}>
              {(slippage_ewma * 100).toFixed(4)}%
            </p>
          ) : (
            <p className="text-xs text-muted">Unavailable</p>
          )}
        </div>
      </div>

      {/* Latency table */}
      <div>
        <p className="text-xs text-muted mb-2">Latency Breakdown</p>
        {latency_table && latency_table.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="pb-2 text-left font-normal pr-4">Operation</th>
                  <th className="pb-2 text-left font-normal pr-4">p50</th>
                  <th className="pb-2 text-left font-normal pr-4">p95</th>
                  <th className="pb-2 text-left font-normal pr-4">p99</th>
                  <th className="pb-2 text-left font-normal">Samples</th>
                </tr>
              </thead>
              <tbody>
                {latency_table.map((op, i) => <LatencyRow key={i} op={op} />)}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-muted">No latency data available</p>
        )}
      </div>
    </div>
  )
}
