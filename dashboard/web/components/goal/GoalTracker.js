import { useEffect, useState } from "react";

const MILESTONES = [200, 500, 1000, 2500, 5000, 10000, 25000, 50000];

function formatUSD(n) {
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}K`;
  return `$${Number(n).toFixed(2)}`;
}

function MilestoneRow({ value, hit, current }) {
  const pct = Math.min(100, (current / value) * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
      <span style={{
        width: 70, textAlign: "right", fontSize: 12,
        color: hit ? "#22c55e" : "#94a3b8", fontWeight: hit ? 700 : 400,
      }}>
        {formatUSD(value)}
      </span>
      <div style={{ flex: 1, background: "#1e293b", borderRadius: 4, height: 8, overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%", borderRadius: 4,
          background: hit ? "#22c55e" : "#3b82f6",
          transition: "width 0.6s ease",
        }} />
      </div>
      <span style={{ width: 36, fontSize: 11, color: "#64748b", textAlign: "right" }}>
        {hit ? "✓" : `${pct.toFixed(0)}%`}
      </span>
    </div>
  );
}

export default function GoalTracker() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/goal");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setData(await res.json());
        setError(null);
      } catch (e) {
        setError(e.message);
      }
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  if (error) return (
    <div style={{ padding: 16, color: "#ef4444", fontSize: 13 }}>
      Goal data unavailable: {error}
    </div>
  );
  if (!data) return (
    <div style={{ padding: 16, color: "#64748b", fontSize: 13 }}>Loading goal…</div>
  );

  const {
    starting_balance, target, current_balance, total_gain_usd, total_gain_pct,
    multiplier_needed, multiplier_achieved, progress_pct, next_milestone,
    milestones_hit = [], days_running, avg_daily_pct, eta_days,
  } = data;

  const gainColor = total_gain_usd >= 0 ? "#22c55e" : "#ef4444";

  return (
    <div style={{
      background: "#0f172a", border: "1px solid #1e293b", borderRadius: 12,
      padding: 20, color: "#e2e8f0", fontFamily: "monospace",
    }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 1 }}>
            GOAL TRACKER
          </div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#f8fafc" }}>
            {formatUSD(starting_balance)} → {formatUSD(target)}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 22, fontWeight: 800, color: gainColor }}>
            {formatUSD(current_balance)}
          </div>
          <div style={{ fontSize: 12, color: gainColor }}>
            {total_gain_usd >= 0 ? "+" : ""}{formatUSD(total_gain_usd)} ({total_gain_pct >= 0 ? "+" : ""}{total_gain_pct.toFixed(1)}%)
          </div>
        </div>
      </div>

      {/* Main progress bar */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#64748b", marginBottom: 4 }}>
          <span>Progress to ${(target / 1000).toFixed(0)}K</span>
          <span>{progress_pct.toFixed(3)}%</span>
        </div>
        <div style={{ background: "#1e293b", borderRadius: 6, height: 12, overflow: "hidden" }}>
          <div style={{
            width: `${Math.max(0.5, progress_pct)}%`,
            height: "100%", borderRadius: 6,
            background: "linear-gradient(90deg, #3b82f6, #8b5cf6)",
            transition: "width 0.8s ease",
          }} />
        </div>
      </div>

      {/* Stats row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 8, marginBottom: 16 }}>
        {[
          { label: "Multiplier", value: `${multiplier_achieved.toFixed(2)}×`, sub: `need ${multiplier_needed.toFixed(0)}×` },
          { label: "Days Running", value: days_running.toFixed(1), sub: "days" },
          { label: "Avg Daily", value: `${avg_daily_pct >= 0 ? "+" : ""}${avg_daily_pct.toFixed(2)}%`, sub: "return" },
          { label: "ETA", value: eta_days != null ? `${eta_days}d` : "—", sub: "to $50K" },
        ].map(({ label, value, sub }) => (
          <div key={label} style={{ background: "#1e293b", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
            <div style={{ fontSize: 10, color: "#64748b", marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "#f8fafc" }}>{value}</div>
            <div style={{ fontSize: 10, color: "#475569" }}>{sub}</div>
          </div>
        ))}
      </div>

      {/* Milestones */}
      <div>
        <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
          Milestones ({milestones_hit.length}/{MILESTONES.length})
          {next_milestone && (
            <span style={{ marginLeft: 8, color: "#3b82f6" }}>
              → next: {formatUSD(next_milestone)}
            </span>
          )}
        </div>
        {MILESTONES.map(ms => (
          <MilestoneRow
            key={ms}
            value={ms}
            hit={milestones_hit.includes(ms)}
            current={current_balance}
          />
        ))}
      </div>
    </div>
  );
}
