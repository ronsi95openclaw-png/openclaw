// SecurityPanel.js — Phase 9 operational panel
import { useState } from 'react'

const fmtTs = (ts) => {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleTimeString() } catch { return ts }
}

function MetricCard({ label, value, alert }) {
  return (
    <div className={`bg-surface border rounded-lg p-3 ${alert && value > 0 ? 'border-down' : 'border-border'}`}>
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className={`text-xl font-mono font-bold ${alert && value > 0 ? 'text-down' : 'text-white'}`}>
        {value ?? 0}
      </p>
    </div>
  )
}

export default function SecurityPanel({ data, approvals }) {
  const [telegramResult, setTelegramResult]   = useState(null)
  const [telegramLoading, setTelegramLoading] = useState(false)
  const [showResultModal, setShowResultModal] = useState(false)

  const API_URL = typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000')
    : 'http://localhost:8000'

  if (!data) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-xs text-muted uppercase tracking-widest mb-3">Security</p>
        <p className="text-xs text-muted">Loading…</p>
      </div>
    )
  }

  const {
    failed_approvals_24h,
    replay_attacks_24h,
    integrity_criticals_24h,
    rollback_triggers_24h,
    integrity_critical_findings,
  } = data

  const approvalRows = approvals ?? data.approval_audit ?? []

  const handleValidateTelegram = async () => {
    setTelegramLoading(true)
    setTelegramResult(null)
    try {
      const res = await fetch(`${API_URL}/api/v2/security/validate-telegram`, { method: 'POST' })
      const json = await res.json()
      setTelegramResult(json)
    } catch (err) {
      setTelegramResult({ ok: false, error: err.message ?? 'Request failed' })
    } finally {
      setTelegramLoading(false)
      setShowResultModal(true)
    }
  }

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <p className="text-xs text-muted uppercase tracking-widest">Security</p>

      {/* Metrics row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="Failed Approvals 24h"    value={failed_approvals_24h}    alert />
        <MetricCard label="Replay Attacks 24h"       value={replay_attacks_24h}       alert />
        <MetricCard label="Integrity CRITICALs 24h"  value={integrity_criticals_24h}  alert />
        <MetricCard label="Rollback Triggers 24h"    value={rollback_triggers_24h}    alert />
      </div>

      {/* Integrity critical findings */}
      {integrity_critical_findings && integrity_critical_findings.length > 0 && (
        <div className="px-3 py-2 rounded border border-down bg-red-950/50">
          <p className="text-xs text-down font-bold mb-2">Integrity CRITICAL Findings</p>
          <ul className="space-y-1">
            {integrity_critical_findings.map((f, i) => (
              <li key={i} className="text-xs text-down flex items-start gap-1">
                <span className="flex-shrink-0">⚠</span>
                <span>{typeof f === 'string' ? f : (f.description ?? f.message ?? JSON.stringify(f))}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Telegram validation */}
      <div>
        <button
          onClick={handleValidateTelegram}
          disabled={telegramLoading}
          className={`px-4 py-2 text-xs rounded border transition ${
            telegramLoading
              ? 'border-border text-muted opacity-50 cursor-not-allowed'
              : 'border-accent text-accent hover:bg-accent hover:text-white'
          }`}
        >
          {telegramLoading ? 'Validating…' : 'Validate Telegram'}
        </button>
      </div>

      {/* Approval audit table */}
      <div>
        <p className="text-xs text-muted mb-2">Approval Audit (last 5)</p>
        {approvalRows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="pb-2 text-left font-normal pr-4">Time</th>
                  <th className="pb-2 text-left font-normal pr-4">Action</th>
                  <th className="pb-2 text-left font-normal pr-4">Operator</th>
                  <th className="pb-2 text-left font-normal">Result</th>
                </tr>
              </thead>
              <tbody>
                {approvalRows.slice(0, 5).map((row, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                    <td className="py-1.5 pr-4 text-muted">{fmtTs(row.ts ?? row.timestamp)}</td>
                    <td className="py-1.5 pr-4 text-white">{row.action ?? '—'}</td>
                    <td className="py-1.5 pr-4 font-mono text-accent">{row.operator_id ?? row.operator ?? '—'}</td>
                    <td className={`py-1.5 font-mono ${
                      row.result === 'approved' ? 'text-up' :
                      row.result === 'rejected' ? 'text-down' : 'text-muted'
                    }`}>
                      {row.result ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-muted">No approval audit entries</p>
        )}
      </div>

      {/* Telegram result modal */}
      {showResultModal && telegramResult && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
          <div className="bg-card border border-border rounded-lg p-6 max-w-sm w-full mx-4">
            <p className="text-sm font-bold text-white mb-3">Telegram Validation Result</p>
            {telegramResult.ok !== false ? (
              <div className="space-y-2">
                <span className="px-2 py-0.5 rounded text-xs font-mono border text-up border-up bg-green-950">
                  VALID
                </span>
                {telegramResult.message && (
                  <p className="text-xs text-muted mt-2">{telegramResult.message}</p>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                <span className="px-2 py-0.5 rounded text-xs font-mono border text-down border-down bg-red-950">
                  FAILED
                </span>
                <p className="text-xs text-muted mt-2">{telegramResult.error ?? telegramResult.message ?? 'Validation failed'}</p>
              </div>
            )}
            <button
              onClick={() => setShowResultModal(false)}
              className="mt-4 w-full py-2 text-xs border border-border text-muted hover:text-white rounded transition"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
