import { useEffect, useRef, useState, useCallback } from 'react'
import Head from 'next/head'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts'

const API  = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const WS   = process.env.NEXT_PUBLIC_WS_URL  || 'ws://localhost:8000/ws'

// ── Helpers ──────────────────────────────────────────────────────────────────

const fmt   = (n, d = 2) => (n == null ? '—' : Number(n).toFixed(d))
const fmtPx = (n)        => n == null ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const pnlCls = (v)       => Number(v) >= 0 ? 'text-up' : 'text-down'
const pnlPfx = (v)       => Number(v) >= 0 ? '+' : ''

const CAPITAL_COLORS = {
  SAFE: 'text-up border-up',
  DEFENSIVE: 'text-warn border-warn',
  CRITICAL: 'text-down border-down',
  EMERGENCY_HALT: 'text-down border-down bg-red-950',
}

const STRATEGY_COLORS = ['#6c63ff','#22c55e','#f59e0b','#06b6d4','#ec4899']

// ── Sub-components ────────────────────────────────────────────────────────────

function Card({ title, children, className = '' }) {
  return (
    <div className={`bg-card border border-border rounded-lg p-4 ${className}`}>
      {title && <p className="text-xs text-muted uppercase tracking-widest mb-3">{title}</p>}
      {children}
    </div>
  )
}

function Pill({ label, active }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono border ${
      active ? 'text-up border-up bg-green-950' : 'text-muted border-border'
    }`}>
      {label}
    </span>
  )
}

function StatusHeader({ status, running, onStart, onStop }) {
  const demo    = status?.demo_mode
  const balance = status?.balance ?? 1000
  const pnl     = status?.total_pnl ?? 0
  const msg     = status?.status_msg ?? 'Idle'

  return (
    <div className="flex items-center justify-between bg-card border border-border rounded-lg px-5 py-3">
      <div className="flex items-center gap-4">
        <span className="text-lg font-bold tracking-tight text-white">OPENCLAW</span>
        <Pill label={demo ? 'PAPER' : 'LIVE'} active={!demo} />
        <span className={`flex items-center gap-1.5 text-xs ${running ? 'text-up' : 'text-muted'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${running ? 'bg-up animate-pulse' : 'bg-muted'}`} />
          {running ? 'RUNNING' : 'STOPPED'}
        </span>
        <span className="text-xs text-muted hidden sm:block">{msg}</span>
      </div>
      <div className="flex items-center gap-6">
        <div className="text-right">
          <p className="text-xs text-muted">Balance</p>
          <p className="text-sm font-mono text-white">{fmtPx(balance)}</p>
        </div>
        <div className="text-right">
          <p className="text-xs text-muted">Total PnL</p>
          <p className={`text-sm font-mono ${pnlCls(pnl)}`}>{pnlPfx(pnl)}{fmtPx(pnl)}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onStart}
            disabled={running}
            className="px-3 py-1.5 text-xs bg-accent hover:bg-purple-500 disabled:opacity-30 text-white rounded transition"
          >Start</button>
          <button
            onClick={onStop}
            disabled={!running}
            className="px-3 py-1.5 text-xs border border-down hover:bg-red-950 disabled:opacity-30 text-down rounded transition"
          >Stop</button>
        </div>
      </div>
    </div>
  )
}

function CapitalCard({ status }) {
  const state    = status?.capital_state ?? 'SAFE'
  const riskPct  = status?.risk_pct ?? 1.5
  const trades   = status?.trades_today ?? 0
  const colorCls = CAPITAL_COLORS[state] ?? 'text-muted border-border'

  return (
    <Card title="Capital Engine">
      <div className={`inline-block border px-2 py-0.5 rounded text-sm font-bold mb-3 ${colorCls}`}>
        {state}
      </div>
      <div className="space-y-2 text-xs">
        <div className="flex justify-between">
          <span className="text-muted">Risk/trade</span>
          <span className="text-white">{riskPct}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">Trades today</span>
          <span className="text-white">{trades}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">Leverage</span>
          <span className="text-white">3×</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">Unrealized</span>
          <span className={pnlCls(status?.unrealized_pnl ?? 0)}>
            {pnlPfx(status?.unrealized_pnl ?? 0)}{fmtPx(status?.unrealized_pnl ?? 0)}
          </span>
        </div>
      </div>
    </Card>
  )
}

function PipelineCard({ health }) {
  if (!health?.systems) return <Card title="Pipeline"><p className="text-xs text-muted">Loading…</p></Card>

  const layers = [
    { name: 'Claude Opus Analyst',  key: 'claude_analyst'   },
    { name: 'Qwen Compressor',      key: 'qwen'             },
    { name: 'Ruflo Advisor',        key: 'ruflo'            },
    { name: 'Intent Pipeline',      key: 'intent'           },
    { name: 'Capital Engine',       key: 'capital'          },
    { name: 'Executor',             key: 'executor'         },
    { name: 'Crypto.com Exchange',  key: 'exchange'         },
  ]

  const statusMap = {}
  health.systems.forEach(s => {
    const k = s.system.toLowerCase()
    if (k.includes('claude') || k.includes('analyst')) statusMap['claude_analyst'] = s.status
    else if (k.includes('qwen'))    statusMap['qwen']     = s.status
    else if (k.includes('ruflo'))   statusMap['ruflo']    = s.status
    else if (k.includes('intent'))  statusMap['intent']   = s.status
    else if (k.includes('capital')) statusMap['capital']  = s.status
    else if (k.includes('executor'))statusMap['executor'] = s.status
    else if (k.includes('exchange'))statusMap['exchange'] = s.status
  })

  return (
    <Card title="AI Pipeline">
      <div className="space-y-2">
        {layers.map((l, i) => {
          const st = statusMap[l.key]
          return (
            <div key={l.key} className="flex items-center gap-2 text-xs">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                st === 'OK' ? 'bg-up' : st === 'PARTIAL' ? 'bg-warn' : st ? 'bg-down' : 'bg-muted'
              }`} />
              <span className="text-white flex-1">{l.name}</span>
              {i < layers.length - 1 && (
                <span className="text-muted ml-auto">↓</span>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function PositionsCard({ positions }) {
  if (!positions?.length)
    return <Card title="Open Positions" className="col-span-2"><p className="text-xs text-muted">No open positions</p></Card>

  return (
    <Card title={`Open Positions (${positions.length})`} className="col-span-2">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted border-b border-border">
              {['Symbol','Strategy','Side','Entry','Current','uPnL','DCA','SL','TP'].map(h =>
                <th key={h} className="pb-2 text-left font-normal pr-4">{h}</th>
              )}
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => (
              <tr key={i} className="border-b border-border/50 hover:bg-white/5 transition">
                <td className="py-1.5 pr-4 text-white font-bold">{p.symbol?.replace('_USDT','')}</td>
                <td className="py-1.5 pr-4 text-accent">{p.strategy}</td>
                <td className={`py-1.5 pr-4 font-bold ${p.side === 'long' ? 'text-up' : 'text-down'}`}>
                  {p.side?.toUpperCase()}
                </td>
                <td className="py-1.5 pr-4">{fmtPx(p.entry_price)}</td>
                <td className="py-1.5 pr-4">{fmtPx(p.current_price)}</td>
                <td className={`py-1.5 pr-4 font-bold ${pnlCls(p.unrealized_pnl)}`}>
                  {pnlPfx(p.unrealized_pnl)}{fmtPx(p.unrealized_pnl)}
                </td>
                <td className="py-1.5 pr-4 text-muted">{p.dca_count > 0 ? `✓ ×${p.dca_count}` : '—'}</td>
                <td className="py-1.5 pr-4 text-down text-opacity-80">{fmtPx(p.sl_price)}</td>
                <td className="py-1.5 text-up text-opacity-80">{fmtPx(p.tp_price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function StrategyCard({ weights }) {
  if (!weights) return <Card title="Strategies"><p className="text-xs text-muted">Loading…</p></Card>

  const entries = Object.entries(weights).map(([name, w]) => ({
    name: name.replace('_', ' '),
    fullName: name,
    weight: w.weight ?? 1,
    wr: w.trades > 0 ? Math.round(w.wins / w.trades * 100) : 0,
    trades: w.trades,
  }))

  return (
    <Card title="Strategy Weights">
      <div className="space-y-2.5">
        {entries.map((s, i) => (
          <div key={s.fullName} className="text-xs">
            <div className="flex justify-between mb-0.5">
              <span className="text-white">{s.name}</span>
              <span className="text-muted">{s.trades}T · {s.wr}%WR · {fmt(s.weight, 2)}×</span>
            </div>
            <div className="h-1 bg-border rounded-full overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.min(100, (s.weight / 2.0) * 100)}%`,
                  backgroundColor: STRATEGY_COLORS[i % STRATEGY_COLORS.length],
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function PnlChart({ trades }) {
  if (!trades?.length)
    return <Card title="P&L Curve"><p className="text-xs text-muted">No trades yet</p></Card>

  let running = 0
  const data = [...trades].reverse().map((t, i) => {
    running += t.pnl || 0
    return { i: i + 1, pnl: parseFloat(running.toFixed(2)) }
  })

  const min = Math.min(...data.map(d => d.pnl))
  const max = Math.max(...data.map(d => d.pnl))

  return (
    <Card title="P&L Curve">
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
          <XAxis dataKey="i" hide />
          <YAxis domain={[min - 2, max + 2]} tick={{ fontSize: 9, fill: '#6b7280' }} />
          <Tooltip
            contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a', fontSize: 11 }}
            formatter={(v) => [`$${v.toFixed(2)}`, 'PnL']}
          />
          <Line
            type="monotone" dataKey="pnl" dot={false} strokeWidth={1.5}
            stroke={data[data.length - 1]?.pnl >= 0 ? '#22c55e' : '#ef4444'}
          />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  )
}

function TradeLog({ trades }) {
  if (!trades?.length)
    return <Card title="Trade Log" className="col-span-full"><p className="text-xs text-muted">No trades yet</p></Card>

  return (
    <Card title={`Trade Log (${trades.length})`} className="col-span-full">
      <div className="overflow-x-auto max-h-60 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card">
            <tr className="text-muted border-b border-border">
              {['Time','Symbol','Strategy','Side','Entry','Exit','PnL','Outcome','Regime','DCA'].map(h =>
                <th key={h} className="pb-2 text-left font-normal pr-4 whitespace-nowrap">{h}</th>
              )}
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={i} className="border-b border-border/30 hover:bg-white/5 transition">
                <td className="py-1 pr-4 text-muted whitespace-nowrap">{t.closed_at || t.opened_at || '—'}</td>
                <td className="py-1 pr-4 text-white">{t.symbol?.replace('_USDT','')}</td>
                <td className="py-1 pr-4 text-accent">{t.strategy}</td>
                <td className={`py-1 pr-4 font-bold ${t.side === 'long' ? 'text-up' : 'text-down'}`}>
                  {t.side?.toUpperCase()}
                </td>
                <td className="py-1 pr-4">{fmtPx(t.entry_price)}</td>
                <td className="py-1 pr-4">{t.exit_price ? fmtPx(t.exit_price) : '—'}</td>
                <td className={`py-1 pr-4 font-bold ${pnlCls(t.pnl)}`}>
                  {t.pnl != null ? `${pnlPfx(t.pnl)}$${Math.abs(t.pnl).toFixed(2)}` : '—'}
                </td>
                <td className="py-1 pr-4">
                  {t.outcome && (
                    <span className={`px-1.5 py-0.5 rounded text-xs ${
                      t.outcome === 'win' ? 'bg-green-950 text-up' : 'bg-red-950 text-down'
                    }`}>
                      {t.outcome.toUpperCase()}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-4 text-muted whitespace-nowrap">{t.regime_label ?? '—'}</td>
                <td className="py-1 text-muted">{t.dca_count > 0 ? `×${t.dca_count}` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function AnalysisCard({ analysis }) {
  if (!analysis || analysis.error)
    return (
      <Card title="Claude Opus Analysis">
        <p className="text-xs text-muted">{analysis?.error ?? 'No analysis yet — triggers nightly at midnight UTC.'}</p>
        <p className="text-xs text-muted mt-1">Or use the Flush button after 5+ trades to trigger now.</p>
      </Card>
    )

  const health = analysis.overall_health
  const healthCls = { STRONG: 'text-up', MODERATE: 'text-warn', WEAK: 'text-down', UNKNOWN: 'text-muted' }

  return (
    <Card title="Claude Opus Analysis">
      <div className="flex items-center gap-4 mb-3">
        <span className={`text-sm font-bold ${healthCls[health] ?? 'text-muted'}`}>{health}</span>
        <span className="text-xs text-muted">WR {fmt(analysis.win_rate_pct, 0)}%</span>
        <span className="text-xs text-muted">E/trade {pnlPfx(analysis.expectancy_usd)}${fmt(Math.abs(analysis.expectancy_usd), 2)}</span>
        <span className="text-xs text-muted ml-auto">{analysis.session_date}</span>
      </div>

      {analysis.immediate_actions?.length > 0 && (
        <div className="mb-3">
          <p className="text-xs text-muted mb-1">Immediate Actions</p>
          {analysis.immediate_actions.slice(0,3).map((a, i) => (
            <p key={i} className="text-xs text-warn">▶ {a}</p>
          ))}
        </div>
      )}

      {analysis.ruflo_learning_directive && (
        <p className="text-xs text-accent mt-2">
          <span className="text-muted">Ruflo: </span>{analysis.ruflo_learning_directive}
        </p>
      )}

      {analysis.weight_adjustments && Object.keys(analysis.weight_adjustments).length > 0 && (
        <div className="mt-3 pt-3 border-t border-border">
          <p className="text-xs text-muted mb-1">Suggested Weight Changes</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(analysis.weight_adjustments).map(([k, v]) => (
              <span key={k} className="text-xs bg-border px-2 py-0.5 rounded">
                {k} → <span className={v >= 1 ? 'text-up' : 'text-down'}>{fmt(v, 2)}×</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}

function Controls({ status, onConfigure, onFlush }) {
  const [risk, setRisk] = useState(status?.risk_pct ?? 1.5)

  useEffect(() => {
    if (status?.risk_pct) setRisk(status.risk_pct)
  }, [status?.risk_pct])

  return (
    <Card title="Controls">
      <div className="space-y-4">
        <div>
          <label className="text-xs text-muted block mb-1">Risk per trade: {risk}%</label>
          <input
            type="range" min="0.5" max="4" step="0.5" value={risk}
            onChange={e => setRisk(parseFloat(e.target.value))}
            onMouseUp={() => onConfigure({ risk_pct: risk })}
            className="w-full accent-accent"
          />
          <div className="flex justify-between text-xs text-muted mt-0.5">
            <span>0.5%</span><span>4%</span>
          </div>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-xs text-muted">Mode</span>
          <div className="flex gap-2">
            <button
              onClick={() => onConfigure({ demo_mode: true })}
              className={`px-2 py-1 text-xs rounded border transition ${
                status?.demo_mode ? 'border-accent text-accent' : 'border-border text-muted hover:border-accent'
              }`}
            >PAPER</button>
            <button
              onClick={() => onConfigure({ demo_mode: false })}
              className={`px-2 py-1 text-xs rounded border transition ${
                !status?.demo_mode ? 'border-up text-up' : 'border-border text-muted hover:border-up'
              }`}
            >LIVE</button>
          </div>
        </div>

        <button
          onClick={onFlush}
          className="w-full py-1.5 text-xs border border-accent text-accent hover:bg-accent hover:text-white rounded transition"
        >
          ⚡ Trigger Claude Opus Analysis
        </button>
      </div>
    </Card>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [status,   setStatus]   = useState(null)
  const [health,   setHealth]   = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [wsStatus, setWsStatus] = useState('connecting')
  const wsRef = useRef(null)

  const API_URL = typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000')
    : 'http://localhost:8000'
  const WS_URL = typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws')
    : 'ws://localhost:8000/ws'

  // Load health + analysis once
  useEffect(() => {
    fetch(`${API_URL}/api/health`).then(r => r.json()).then(setHealth).catch(() => {})
    fetch(`${API_URL}/api/analysis`).then(r => r.json()).then(setAnalysis).catch(() => {})
  }, [API_URL])

  // WebSocket for live state
  useEffect(() => {
    let ws
    let reconnectTimer

    function connect() {
      ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen  = () => setWsStatus('connected')
      ws.onclose = () => {
        setWsStatus('reconnecting')
        reconnectTimer = setTimeout(connect, 3000)
      }
      ws.onerror = () => ws.close()

      ws.onmessage = ({ data }) => {
        try {
          const { type, data: payload } = JSON.parse(data)
          if (type === 'init' || type === 'state_update') setStatus(payload)
          if (type === 'analysis') setAnalysis(payload)
          if (type === 'system_health') setHealth(payload)
        } catch {}
      }
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [WS_URL])

  const running    = status?.running ?? false
  const trades     = status?.trade_log ?? []
  const positions  = status?.open_positions ?? []
  const weights    = status?.strategy_weights ?? null

  const handleStart     = () => fetch(`${API_URL}/api/bot/start`,  { method: 'POST' })
  const handleStop      = () => fetch(`${API_URL}/api/bot/stop`,   { method: 'POST' })
  const handleFlush     = () => fetch(`${API_URL}/api/bot/flush`,  { method: 'POST' })
  const handleConfigure = (cfg) =>
    fetch(`${API_URL}/api/bot/configure`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })

  return (
    <>
      <Head>
        <title>OpenClaw — Trading Control Center</title>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
      </Head>

      <div className="min-h-screen bg-surface p-4 space-y-4">
        {/* Header */}
        <StatusHeader status={status} running={running} onStart={handleStart} onStop={handleStop} />

        {/* WS status banner */}
        {wsStatus !== 'connected' && (
          <div className="text-xs text-center text-warn py-1 bg-yellow-950 border border-warn rounded">
            WebSocket {wsStatus} — data may be stale
          </div>
        )}

        {/* Row 1: Capital + Pipeline + Positions */}
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 sm:col-span-3 lg:col-span-2">
            <CapitalCard status={status} />
          </div>
          <div className="col-span-12 sm:col-span-3 lg:col-span-2">
            <PipelineCard health={health} />
          </div>
          <div className="col-span-12 sm:col-span-6 lg:col-span-8">
            <PositionsCard positions={positions} />
          </div>
        </div>

        {/* Row 2: Strategies + PnL + Controls */}
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 sm:col-span-4 lg:col-span-3">
            <StrategyCard weights={weights} />
          </div>
          <div className="col-span-12 sm:col-span-5 lg:col-span-5">
            <PnlChart trades={trades} />
          </div>
          <div className="col-span-12 sm:col-span-3 lg:col-span-4">
            <Controls status={status} onConfigure={handleConfigure} onFlush={handleFlush} />
          </div>
        </div>

        {/* Row 3: Analysis */}
        <AnalysisCard analysis={analysis} />

        {/* Row 4: Trade Log */}
        <TradeLog trades={trades} />
      </div>
    </>
  )
}
