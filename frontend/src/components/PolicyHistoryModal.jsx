import { useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { api, formatMoney } from '../api';
import { Badge, Button, Modal } from './ui';
import { formatActionLimits } from '../policyFormat';

export default function PolicyHistoryModal({ agent, onClose, onRollback }) {
  const [versions, setVersions] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api(`/agents/${agent.agent_id}/policy-history`).then(setVersions).catch((e) => setError(e.message));
  }, [agent.agent_id, agent.policy_version]);

  return (
    <Modal title={`Policy History — ${agent.display_name}`} subtitle="Rollbacks are forward-only: an old version is re-published as a new one." onClose={onClose} wide>
      {error && <p className="text-xs text-[#C1443B] font-mono">{error}</p>}
      {!versions ? (
        <p className="text-xs text-[#8A93A3] font-mono">Loading…</p>
      ) : (
        <ol className="space-y-2 text-[11px] font-mono">
          {versions.map((v) => {
            const current = v.version === agent.policy_version;
            return (
              <li key={v.policy_id} className={`p-3 rounded-lg border ${current ? 'border-[#E8A33D]/50 bg-[#1C1810]' : 'border-[#1E232A] bg-[#0E1115]'}`}>
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-[#E6E8EB]">v{v.version}</span>
                    {current && <Badge className="bg-[#E8A33D]/20 text-[#E8A33D]">current</Badge>}
                    <span className="text-[#8A93A3]">{v.created_at.replace('T', ' ').slice(0, 19)} UTC</span>
                  </div>
                  {!current && (
                    <Button onClick={() => onRollback(agent.agent_id, v.version)} className="px-2 py-1">
                      <RotateCcw className="w-3 h-3 text-[#E8A33D]" /> Roll back to v{v.version}
                    </Button>
                  )}
                </div>
                <div className="text-[#8A93A3] italic mb-1">{v.note}</div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[#8A93A3]">
                  <span>allow: <b className="text-[#4C9A6A]">{v.allowed_actions.join(', ') || '—'}</b></span>
                  <span>deny: <b className="text-[#C1443B]">{v.blocked_actions.join(', ') || '—'}</b></span>
                  <span>daily cap: <b className="text-[#E6E8EB]">{formatMoney(v.spend_cap_daily)}</b></span>
                  <span>per-tx: <b className="text-[#E6E8EB]">{formatMoney(v.max_per_transaction)}</b></span>
                  <span>rate: <b className="text-[#E6E8EB]">{v.rate_limit_per_minute ? `${v.rate_limit_per_minute}/min` : '—'}</b></span>
                  <span>action caps: <b className="text-[#E6E8EB]">{formatActionLimits(v.action_limits) || '—'}</b></span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Modal>
  );
}
