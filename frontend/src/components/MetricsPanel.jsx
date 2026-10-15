import { BarChart3 } from 'lucide-react';
import { SectionTitle } from './ui';

function Stat({ label, value, sub, tone = 'text-[#E6E8EB]' }) {
  return (
    <div className="bg-[#0E1115] border border-[#1E232A] rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-wider text-[#8A93A3] font-mono">{label}</div>
      <div className={`text-lg font-bold font-mono ${tone}`}>{value}</div>
      {sub && <div className="text-[10px] text-[#8A93A3] font-mono">{sub}</div>}
    </div>
  );
}

const fmtUs = (us) => (us >= 1000 ? `${(us / 1000).toFixed(2)} ms` : `${us.toFixed(1)} µs`);

export default function MetricsPanel({ metrics }) {
  if (!metrics) return null;
  const rules = Object.entries(metrics.blocked_by_rule).sort((a, b) => b[1] - a[1]);
  const maxRule = Math.max(1, ...rules.map(([, n]) => n));
  const fleet = metrics.fleet;

  return (
    <section id="metrics" className="panel-base p-4 mb-6 border border-[#1E232A]">
      <SectionTitle
        icon={BarChart3}
        right={<span className="text-[10px] font-mono text-[#8A93A3]">GET /metrics · /metrics/prometheus</span>}
      >
        Enforcement Metrics
      </SectionTitle>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-4">
        <Stat label="Decisions" value={metrics.decisions_total} sub={`${metrics.approved_total} approved · ${metrics.blocked_total} blocked`} />
        <Stat
          label="Approval rate"
          value={metrics.approval_rate === null ? '—' : `${(metrics.approval_rate * 100).toFixed(1)}%`}
          tone="text-[#4C9A6A]"
        />
        <Stat label="Policy eval p99" value={fmtUs(metrics.policy_eval_latency.p99_us)} sub={`p50 ${fmtUs(metrics.policy_eval_latency.p50_us)}`} tone="text-[#E8A33D]" />
        <Stat label="Full decision p99" value={fmtUs(metrics.decision_latency.p99_us)} sub={`p50 ${fmtUs(metrics.decision_latency.p50_us)} · incl. DB + audit`} tone="text-[#E8A33D]" />
        <Stat
          label="Fleet"
          value={fleet.fleet_stopped ? 'STOPPED' : 'LIVE'}
          sub={`${fleet.agents_by_status.active} active · ${fleet.agents_by_status.stopped} stopped · ${fleet.agents_by_status.revoked} revoked`}
          tone={fleet.fleet_stopped ? 'text-[#C1443B]' : 'text-[#4C9A6A]'}
        />
        <Stat label="Operator actions" value={Object.values(metrics.operator_events).reduce((a, b) => a + b, 0)} sub="all hash-chained" />
      </div>
      <div className="space-y-1.5">
        {rules.length === 0 ? (
          <p className="text-[11px] font-mono text-[#8A93A3]">No blocks yet.</p>
        ) : (
          rules.map(([rule, n]) => (
            <div key={rule} className="flex items-center gap-3 text-[11px] font-mono">
              <span className="w-56 text-[#8A93A3] truncate">{rule}</span>
              <div className="flex-1 bg-[#181C22] rounded h-2 overflow-hidden">
                <div className="h-full bg-[#C1443B]" style={{ width: `${(n / maxRule) * 100}%` }} />
              </div>
              <span className="w-10 text-right text-[#E6E8EB]">{n}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
