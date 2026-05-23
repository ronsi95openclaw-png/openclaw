// ChaosPanel.js — Phase 9 operational panel
import { useState } from 'react'

const CHAOS_EVENT_TYPES = [
  'network_delay',
  'packet_loss',
  'exchange_timeout',
  'ws_disconnect',
  'memory_pressure',
  'cpu_spike',
  'disk_fill',
  'clock_skew',
  'partial_fill',
  'order_rejection',
]

const fmtTs = (ts) => {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleTimeString() } catch { return ts }
}

function ResourceBar({ label, value, max, unit = '' }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const color = pct >= 90 ? 'bg-down' : pct >= 70 ? 'bg-warn' : 'bg-up'
  return (
    <div>
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-muted">{label}</span>
        <span className="text-white font-mono">{value != null ? `${value}${unit}` : '—'}</span>
      </div>
      <div className="h-1.5 bg-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function ChaosPanel({ data, events, onInjectChaos }) {
  const [eventType, setEventType]       = useState(CHAOS_EVENT_TYPES[0])
  const [seed, setSeed]                 = useState('')
  const [showConfirm, setShowConfirm]   = useState(false)
  const [injectResult, setInjectResult] = useState(null)

  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Chaos Engineering</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    active_incidents,
    thread_count,
    memory_mb,
    fd_count,
    demo_mode,
  } = data

  const chaosEvents   = events ?? data.recent_events ?? []
  const isDemo        = demo_mode !== false  // default assume demo

  const maxThreads  = 500
  const maxMemoryMB = 4096
  const maxFd       = 1024

  const handleInjectClick = () => {
    if (!isDemo) return
    setShowConfirm(true)
  }

  const handleConfirm = () => {
    setShowConfirm(false)
    setInjectResult(null)
    if (onInjectChaos) {
      onInjectChaos({ event_type: eventType, seed: seed || undefined })
        .then((res) => setInjectResult({ ok: true, message: res?.message ?? 'Injected' }))
        .catch((err) => setInjectResult({ ok: false, message: err?.message ?? 'Failed' }))
    } else {
      setInjectResult({ ok: true, message: `Simulated injection of ${eventType}` })
    }
  }

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted uppercase tracking-widest">Chaos Engineering</p>
        {active_incidents > 0 && (
          <span className="px-2 py-0.5 rounded text-xs font-mono border text-down border-down bg-red-950 animate-pulse">
            {active_incidents} ACTIVE
          </span>
        )}
      </div>

      {/* Active incidents */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted">Active Incidents</span>
        <span className={`text-2xl font-mono font-bold ${active_incidents > 0 ? 'text-down' : 'text-up'}`}>
          {active_incidents ?? 0}
        </span>
      </div>

      {/* Resource bars */}
      <div className="space-y-2">
        <ResourceBar label="Thread Count" value={thread_count}  max={maxThreads}  />
        <ResourceBar label="Memory"       value={memory_mb}     max={maxMemoryMB} unit=" MB" />
        <ResourceBar label="File Handles" value={fd_count}      max={maxFd}       />
      </div>

      {/* Recent chaos events */}
      {chaosEvents.length > 0 ? (
        <div>
          <p className="text-xs text-muted mb-2">Recent Chaos Events</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="pb-2 text-left font-normal pr-4">Type</th>
                  <th className="pb-2 text-left font-normal pr-4">Time</th>
                  <th className="pb-2 text-left font-normal">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {chaosEvents.slice(0, 5).map((ev, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                    <td className="py-1.5 pr-4 font-mono text-warn">{ev.type ?? ev.event_type ?? '—'}</td>
                    <td className="py-1.5 pr-4 text-muted">{fmtTs(ev.ts ?? ev.timestamp)}</td>
                    <td className={`py-1.5 font-mono ${
                      ev.outcome === 'recovered' ? 'text-up' :
                      ev.outcome === 'failed'    ? 'text-down' : 'text-muted'
                    }`}>
                      {ev.outcome ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <p className="text-xs text-muted">No chaos events recorded</p>
      )}

      {/* Chaos injection form */}
      <div className="border border-border rounded-lg p-3">
        <p className="text-xs text-muted mb-3">Chaos Injection</p>

        {!isDemo ? (
          <div className="px-3 py-2 rounded border border-down bg-red-950/50 text-down text-xs font-mono text-center">
            Chaos injection disabled when DEMO_MODE=false
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <div>
                <label className="text-xs text-muted block mb-1">Event Type</label>
                <select
                  value={eventType}
                  onChange={e => setEventType(e.target.value)}
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
                >
                  {CHAOS_EVENT_TYPES.map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted block mb-1">Seed (optional)</label>
                <input
                  type="text"
                  value={seed}
                  onChange={e => setSeed(e.target.value)}
                  placeholder="random seed"
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
                />
              </div>
            </div>
            <button
              onClick={handleInjectClick}
              className="w-full py-1.5 text-xs rounded border border-down text-down hover:bg-red-950 transition"
            >
              Inject Chaos Event
            </button>
            {injectResult && (
              <p className={`text-xs mt-2 text-center ${injectResult.ok ? 'text-up' : 'text-down'}`}>
                {injectResult.message}
              </p>
            )}
          </>
        )}
      </div>

      {/* Confirmation modal */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
          <div className="bg-card border border-border rounded-lg p-6 max-w-sm w-full mx-4">
            <p className="text-sm font-bold text-down mb-2">Inject Chaos?</p>
            <p className="text-xs text-muted mb-4">
              Injecting <span className="text-warn font-mono">{eventType}</span> chaos into the live system. This may affect trading operations.
            </p>
            <p className="text-xs text-warn mb-4">
              ⚠ Confirm you want to inject chaos event type: <span className="font-mono">{eventType}</span>
              {seed && <><br />Seed: <span className="font-mono">{seed}</span></>}
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleConfirm}
                className="flex-1 py-2 text-xs bg-red-900 hover:bg-red-800 text-down border border-down rounded transition"
              >
                Inject
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
