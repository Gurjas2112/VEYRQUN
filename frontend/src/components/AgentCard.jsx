import { useState } from 'react';
import { History, Sliders } from 'lucide-react';
import { formatMoney } from '../api';
import { Badge, Button } from './ui';
import { STATUS_STYLES } from '../styles';

export default function AgentCard({ agent, onEdit, onHistory, onResetSpend, onStop, onResume, onRevoke, onReinstate }) {
  const [tab, setTab] = useState('allowed');
  const { status } = agent;
  const spendPct = agent.spend_cap_daily > 0 ? Math.min(100, (agent.spend_used_today / agent.spend_cap_daily) * 100) : 100;
  const actions = tab === 'allowed' ? agent.allowed_actions : agent.blocked_actions;
  const actionLimits = Object.entries(agent.action_limits || {});

  const border = {
    active: 'border-[#1E232A] hover:border-[#2D333B]',
    stopped: 'border-[#C1443B]/60 bg-[#1A1214]',
    revoked: 'border-[#9B6BD3]/60 bg-[#17131D]',
  }[status];

  return (
    <div data-agent={agent.agent_id} className={`panel-base p-4 transition-all border ${border}`}>
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-sm font-bold text-[#E6E8EB]">{agent.display_name}</h3>
          <p className="text-[11px] text-[#8A93A3] font-mono">
            ID: {agent.agent_id} · policy v{agent.policy_version}
          </p>
        </div>
        <Badge className={STATUS_STYLES[status]}>{status}</Badge>
      </div>

      {status !== 'active' && agent.status_reason && (
        <p className="text-[10px] font-mono mb-2 text-[#8A93A3]">Reason: {agent.status_reason}</p>
      )}

      <div className="bg-[#0E1115] rounded-lg p-2.5 mb-3 border border-[#1E232A]">
        <div className="flex justify-between text-[11px] mb-1 font-mono">
          <span className="text-[#8A93A3]">Daily Spend</span>
          <span className="text-[#E6E8EB] font-bold">
            {formatMoney(agent.spend_used_today)} / {formatMoney(agent.spend_cap_daily)}
          </span>
        </div>
        <div className="w-full bg-[#181C22] rounded-full h-1.5 overflow-hidden">
          <div
            className={`h-full transition-all duration-300 ${spendPct >= 90 ? 'bg-[#C1443B]' : spendPct >= 70 ? 'bg-[#E8A33D]' : 'bg-[#4C9A6A]'}`}
            style={{ width: `${spendPct}%` }}
          />
        </div>
        <div className="grid grid-cols-2 gap-2 mt-2 text-[10px] font-mono text-[#8A93A3]">
          <span>Per-tx limit: <b className="text-[#E6E8EB]">{formatMoney(agent.max_per_transaction)}</b></span>
          <span>Rate limit: <b className="text-[#E6E8EB]">{agent.rate_limit_per_minute ? `${agent.rate_limit_per_minute}/min` : '—'}</b></span>
          {actionLimits.length > 0 && (
            <span className="col-span-2">
              Action caps:{' '}
              <b className="text-[#E6E8EB]">{actionLimits.map(([a, v]) => `${a} ≤ ${formatMoney(v)}`).join(', ')}</b>
            </span>
          )}
        </div>
      </div>

      <div className="mb-3">
        <div className="flex border-b border-[#1E232A] mb-2 font-mono">
          {['allowed', 'blocked'].map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`pb-1 text-[10px] font-bold mr-3 capitalize transition-all ${
                tab === t
                  ? t === 'allowed' ? 'text-[#4C9A6A] border-b border-[#4C9A6A]' : 'text-[#C1443B] border-b border-[#C1443B]'
                  : 'text-[#8A93A3] hover:text-[#E6E8EB]'
              }`}
            >
              {t} ({(t === 'allowed' ? agent.allowed_actions : agent.blocked_actions).length})
            </button>
          ))}
        </div>
        <div className="min-h-[24px] flex flex-wrap gap-1.5 items-center text-[10px] font-mono">
          {actions.length ? (
            actions.map((a) => (
              <span key={a} className={`px-1.5 py-0.5 rounded bg-[#0E1115] ${tab === 'allowed' ? 'text-[#4C9A6A]' : 'text-[#C1443B]'}`}>
                {a}
              </span>
            ))
          ) : (
            <span className="italic text-[#8A93A3]">None</span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-[#1E232A] font-mono text-[11px]">
        <div className="flex items-center gap-1.5">
          <Button onClick={() => onEdit(agent)} className="p-1.5" title="Edit policy">
            <Sliders className="w-3.5 h-3.5 text-[#E8A33D]" /> Policy
          </Button>
          <Button onClick={() => onHistory(agent)} className="p-1.5" title="Policy version history">
            <History className="w-3.5 h-3.5 text-[#E8A33D]" /> History
          </Button>
          <Button onClick={() => onResetSpend(agent.agent_id)} className="p-1.5 text-[#8A93A3]">
            Reset spend
          </Button>
        </div>
        <div className="flex items-center gap-1.5">
          {status === 'stopped' && <Button variant="softOk" onClick={() => onResume(agent.agent_id)} className="px-3 py-1">Resume</Button>}
          {status === 'active' && <Button variant="softDanger" onClick={() => onStop(agent.agent_id)} className="px-3 py-1">Stop</Button>}
          {status === 'revoked' ? (
            <Button variant="softOk" onClick={() => onReinstate(agent.agent_id)} className="px-3 py-1">Reinstate</Button>
          ) : (
            <Button variant="softViolet" onClick={() => onRevoke(agent.agent_id)} className="px-3 py-1">Revoke</Button>
          )}
        </div>
      </div>
    </div>
  );
}
