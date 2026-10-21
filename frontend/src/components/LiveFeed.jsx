import { AlertTriangle, Zap } from 'lucide-react';
import { isOperatorEvent } from '../api';
import { OUTCOME_STYLES } from '../styles';

const CARD_STYLES = {
  allow: 'bg-[#121A15] border-[#4C9A6A]/30',
  redact: 'bg-[#121A15] border-[#4C9A6A]/30',
  require_approval: 'bg-[#1C1810] border-[#E8A33D]/30',
  block: 'bg-[#1F1314] border-[#C1443B]/30',
  executed: 'bg-[#1C1810] border-[#E8A33D]/30',
};

export default function LiveFeed({ entries, paused, onHoverChange }) {
  return (
    <div
      id="live-feed"
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
      className="panel-base p-4 flex flex-col h-[640px] border border-[#1E232A]"
    >
      <div className="flex items-center justify-between pb-3 border-b border-[#1E232A]">
        <h2 className="text-xs font-mono font-bold text-[#8A93A3] uppercase tracking-wider flex items-center gap-2">
          <Zap className="w-4 h-4 text-[#E8A33D]" /> Live Activity Feed
        </h2>
        <span className="font-mono text-[10px] text-[#E8A33D] font-bold">{paused ? 'PAUSED ON HOVER' : ''}</span>
      </div>

      <div className="flex-1 overflow-y-auto space-y-2.5 pt-3 pr-1">
        {entries.length === 0 ? (
          <p className="text-xs text-[#8A93A3] text-center py-10 font-mono">Awaiting live actions...</p>
        ) : (
          entries.map((entry) => (
            <div
              key={entry.action_id}
              className={`p-2.5 rounded-xl border text-xs font-mono text-[#E6E8EB] animate-slide-down ${CARD_STYLES[entry.outcome]}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-bold">{entry.agent_id === '*fleet*' ? 'FLEET (operator)' : entry.agent_id}</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${OUTCOME_STYLES[entry.outcome]}`}>
                  {entry.outcome}
                </span>
              </div>
              <div className="flex justify-between text-[#8A93A3] text-[11px]">
                <span>{entry.action_type}</span>
                {!isOperatorEvent(entry) && <span>${entry.amount}</span>}
              </div>
              <div className="text-[10px] text-[#8A93A3] mt-0.5">
                {entry.timestamp?.split('T')[1]?.slice(0, 8)} UTC · policy v{entry.policy_version}
              </div>
              {entry.rule_triggered && (
                <div className="mt-1 text-[10px] text-[#C1443B] flex items-center gap-1 font-semibold">
                  <AlertTriangle className="w-3 h-3" /> Rule: {entry.rule_triggered}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
