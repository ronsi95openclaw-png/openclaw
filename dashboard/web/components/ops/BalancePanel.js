// BalancePanel.js — Phase 9 operational panel

const fmtUSD = (n) =>
  n == null ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const fmtPct = (n, d = 2) => (n == null ? '—' : `${Number(n).toFixed(d)}%`)

const fmtTs = (ts) => {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleTimeString()
  } catch {
    return ts
  }
}

function ValueCard({ label, value, sub, highlight }) {
  return (
    <div className={`bg-card border rounded-lg p-3 ${highlight ? highlight : 'border-border'}`}>
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className="text-base font-mono font-bold text-white">{fmtUSD(value)}</p>
      {sub && <p className="text-xs text-muted mt-0.5">{sub}</p>}
    </div>
  )
}

function DivergenceBadge({ pct }) {
  if (pct == null) return <span className="text-xs text-muted">—</span>
  const v = Math.abs(Number(pct))
  const cls = v < 2 ? 'text-up border-up bg-green-950' : v < 5 ? 'text-warn border-warn bg-yellow-950/50' : 'text-down border-down bg-red-950'
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono border ${cls}`}>
      {fmtPct(pct)}
    </span>
  )
}

export default function BalancePanel({ data, history }) {
  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Balance</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    exchange_balance,
    capital_engine_equity,
    replay_equity,
    divergence_pct,
    ewma_divergence,
    stale_feed,
    stale_seconds,
    negative_collateral,
    audit_entries,
  } = data

  const auditRows = audit_entries ?? history?.slice(0, 5) ?? []

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <p className="text-xs text-muted uppercase tracking-widest">Balance</p>

      {/* Negative collateral alert */}
      {negative_collateral && (
        <div className="px-3 py-2 rounded border border-down bg-red-950 text-down text-xs font-mono font-bold text-center animate-pulse">
          ⚠ NEGATIVE COLLATERAL DETECTED — HALT RISK
        </div>
      )}

      {/* Stale feed warning */}
      {stale_feed && (
        <div className="px-3 py-2 rounded border border-warn bg-yellow-950/50 text-warn text-xs font-mono">
          ⚠ Stale balance feed — last update {stale_seconds != null ? `${stale_seconds}s ago` : 'unknown'}
        </div>
      )}

      {/* 3 equity cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <ValueCard label="Exchange Balance" value={exchange_balance} sub="Live exchange" />
        <ValueCard label="Capital Engine Equity" value={capital_engine_equity} sub="Engine estimate" />
        <ValueCard label="Replay Equity" value={replay_equity} sub="Audit replay" />
      </div>

      {/* Divergence row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
        <div>
          <p className="text-xs text-muted mb-1">Divergence</p>
          <DivergenceBadge pct={divergence_pct} />
          <p className="text-xs text-muted mt-1">
            {divergence_pct == null ? '' :
              Math.abs(divergence_pct) < 2 ? 'Within tolerance' :
              Math.abs(divergence_pct) < 5 ? 'Investigate' :
              'Action required'}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted mb-1">EWMA Divergence</p>
          <span className={`text-sm font-mono font-bold ${
            ewma_divergence == null ? 'text-muted' :
            Math.abs(ewma_divergence) < 2 ? 'text-up' :
            Math.abs(ewma_divergence) < 5 ? 'text-warn' : 'text-down'
          }`}>
            {ewma_divergence != null ? fmtPct(ewma_divergence) : '—'}
          </span>
        </div>
        <div>
          <p className="text-xs text-muted mb-1">Feed Status</p>
          <span className={`px-2 py-0.5 rounded text-xs font-mono border ${
            stale_feed ? 'text-warn border-warn bg-yellow-950/50' : 'text-up border-up bg-green-950'
          }`}>
            {stale_feed ? `STALE ${stale_seconds != null ? `(${stale_seconds}s)` : ''}` : 'LIVE'}
          </span>
        </div>
      </div>

      {/* Audit table */}
      {auditRows.length > 0 && (
        <div>
          <p className="text-xs text-muted mb-2">Balance Audit (last 5)</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="pb-2 text-left font-normal pr-4">Time</th>
                  <th className="pb-2 text-left font-normal pr-4">Exchange</th>
                  <th className="pb-2 text-left font-normal pr-4">Engine</th>
                  <th className="pb-2 text-left font-normal pr-4">Divergence</th>
                  <th className="pb-2 text-left font-normal">Status</th>
                </tr>
              </thead>
              <tbody>
                {auditRows.slice(0, 5).map((row, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                    <td className="py-1.5 pr-4 text-muted">{fmtTs(row.ts ?? row.timestamp)}</td>
                    <td className="py-1.5 pr-4 font-mono">{fmtUSD(row.exchange_balance ?? row.exchange)}</td>
                    <td className="py-1.5 pr-4 font-mono">{fmtUSD(row.engine_equity ?? row.engine)}</td>
                    <td className="py-1.5 pr-4"><DivergenceBadge pct={row.divergence_pct ?? row.divergence} /></td>
                    <td className="py-1.5 text-muted">{row.status ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
