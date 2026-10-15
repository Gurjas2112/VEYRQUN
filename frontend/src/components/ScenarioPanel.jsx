import { AlertTriangle, Ban, CheckCircle2, Gauge, UserX, XCircle, Zap } from 'lucide-react';

const SCENARIOS = [
  { id: 'approved', title: '1. Approved Action', text: 'Travel Agent rebooks a $450 flight — within permissions and caps.', icon: CheckCircle2, color: '#4C9A6A' },
  { id: 'unpermitted', title: '2. Unpermitted Action', text: 'Travel Agent requests a credit increase — on its block-list.', icon: XCircle, color: '#C1443B' },
  { id: 'spend_cap', title: '3. Spend Cap Breach', text: 'Travel Agent keeps rebooking $550 flights until the daily cap trips.', icon: Gauge, color: '#E8A33D' },
  { id: 'revoke', title: '4. Revoke Agent', text: 'Credit Limit Agent is revoked, then its next action is refused.', icon: UserX, color: '#B48BE6' },
  { id: 'fleet_stop', title: '5. Fleet Emergency Stop', text: 'Pull the global plug — every agent halts instantly.', icon: AlertTriangle, color: '#C1443B' },
];

export default function ScenarioPanel({ onTrigger, busy }) {
  return (
    <section className="panel-demo p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-mono font-bold uppercase tracking-wider text-[#E8A33D] flex items-center gap-2">
          <Zap className="w-3.5 h-3.5" /> Presenter View — Scenario Triggers
        </h2>
        <span className="text-[11px] text-[#8A93A3] font-mono font-semibold flex items-center gap-1">
          <Ban className="w-3 h-3" /> Every trigger goes through the live policy engine
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-3">
        {SCENARIOS.map(({ id, title, text, icon: Icon, color }) => (
          <button
            key={id}
            disabled={busy}
            onClick={() => onTrigger(id)}
            className="p-3 bg-[#14171B] hover:bg-[#1C2128] border border-[#2D333B] rounded-xl text-left transition-all group disabled:opacity-60"
            style={{ '--accent': color }}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-bold group-hover:underline font-mono" style={{ color }}>{title}</span>
              <Icon className="w-4 h-4" style={{ color }} />
            </div>
            <p className="text-[11px] text-[#8A93A3]">{text}</p>
          </button>
        ))}
      </div>
    </section>
  );
}
