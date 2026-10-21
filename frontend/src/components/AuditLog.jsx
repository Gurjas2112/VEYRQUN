import { useState } from 'react';
import { Check, CheckCircle2, Copy, Lock, ShieldCheck, XCircle } from 'lucide-react';
import { isOperatorEvent } from '../api';
import { Button } from './ui';
import { OUTCOME_STYLES } from '../styles';

export default function AuditLog({ logs, agents, filters, onFilterChange, onLoadMore, onVerify, verifying, verification }) {
  const [copied, setCopied] = useState(null);

  const copy = (hash) => {
    navigator.clipboard?.writeText(hash);
    setCopied(hash);
    setTimeout(() => setCopied(null), 2000);
  };

  return (
    <section id="audit-log" className="panel-base p-6 border border-[#1E232A]">
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 mb-4 pb-4 border-b border-[#1E232A]">
        <div>
          <h2 className="text-sm font-mono font-bold text-[#E6E8EB] flex items-center gap-2">
            <Lock className="w-4 h-4 text-[#E8A33D]" /> IMMUTABLE HASH-CHAINED AUDIT LOG
          </h2>
          <p className="text-xs text-[#8A93A3] mt-0.5">
            Every agent decision and every operator action is SHA-256 chained to the one before it.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button variant="signal" onClick={onVerify} disabled={verifying} className="px-4 py-2 rounded-xl text-xs font-mono">
            <ShieldCheck className="w-4 h-4" /> {verifying ? 'Verifying Chain...' : 'Verify Chain Integrity'}
          </Button>
          {verification && (
            <div
              className={`px-3 py-1.5 rounded-xl border text-xs font-mono font-bold flex items-center gap-2 ${
                verification.valid ? 'bg-[#4C9A6A]/10 border-[#4C9A6A]/40 text-[#4C9A6A]' : 'bg-[#C1443B]/10 border-[#C1443B]/40 text-[#C1443B]'
              }`}
            >
              {verification.valid ? <CheckCircle2 className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
              {verification.valid
                ? `VALID (${verification.entries_checked} ENTRIES UNBROKEN)`
                : `CHAIN BROKEN AT ${verification.broken_at ?? '?'} — ${verification.reason}`}
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 mb-4 text-xs font-mono">
        <label className="flex items-center gap-2">
          <span className="text-[#8A93A3]">Agent:</span>
          <select
            value={filters.agent}
            onChange={(e) => onFilterChange({ ...filters, agent: e.target.value })}
            className="bg-[#0E1115] border border-[#2D333B] text-[#E6E8EB] rounded-lg px-2.5 py-1"
          >
            <option value="all">All Agents</option>
            <option value="*fleet*">Fleet (operator)</option>
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>{a.display_name}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-[#8A93A3]">Outcome:</span>
          <select
            value={filters.outcome}
            onChange={(e) => onFilterChange({ ...filters, outcome: e.target.value })}
            className="bg-[#0E1115] border border-[#2D333B] text-[#E6E8EB] rounded-lg px-2.5 py-1"
          >
            <option value="all">All Outcomes</option>
            <option value="allow">Allowed</option>
            <option value="redact">Redacted</option>
            <option value="require_approval">Held for approval</option>
            <option value="block">Blocked</option>
            <option value="executed">Operator actions</option>
          </select>
        </label>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs font-mono">
          <thead>
            <tr className="border-b border-[#1E232A] text-[#8A93A3] uppercase tracking-wider text-[10px]">
              {['Timestamp', 'Agent', 'Action', 'Amount', 'Outcome', 'Rule Triggered', 'Policy', 'SHA-256 Entry Hash'].map((h) => (
                <th key={h} className="p-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1E232A] text-[#E6E8EB]">
            {logs.length === 0 ? (
              <tr>
                <td colSpan={8} className="text-center py-6 text-[#8A93A3]">No audit records found.</td>
              </tr>
            ) : (
              logs.map((log) => (
                <tr key={log.action_id} className="hover:bg-[#14181F] transition-all">
                  <td className="p-3 text-[#8A93A3] text-[11px]">{log.timestamp.split('T')[1]?.replace('Z', '').slice(0, 12)}</td>
                  <td className="p-3 font-semibold">{log.agent_id}</td>
                  <td className={`p-3 ${isOperatorEvent(log) ? 'text-[#E8A33D]' : ''}`}>{log.action_type}</td>
                  <td className="p-3">{isOperatorEvent(log) ? '—' : `$${log.amount}`}</td>
                  <td className="p-3">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${OUTCOME_STYLES[log.outcome]}`}>{log.outcome}</span>
                  </td>
                  <td className="p-3 text-[#C1443B]">{log.rule_triggered || '-'}</td>
                  <td className="p-3 text-[#8A93A3]">v{log.policy_version}</td>
                  <td className="p-3 text-[10px]">
                    <button
                      onClick={() => copy(log.entry_hash)}
                      className="flex items-center gap-1.5 text-[#E8A33D] hover:text-[#F3B85E] bg-[#1C2128] border border-[#2D333B] px-2 py-1 rounded transition-all group"
                      title={`entry ${log.entry_hash}\nprev  ${log.prev_hash}`}
                    >
                      <span className="truncate max-w-[140px]">{log.entry_hash}</span>
                      {copied === log.entry_hash ? <Check className="w-3 h-3 text-[#4C9A6A]" /> : <Copy className="w-3 h-3 text-[#8A93A3]" />}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="flex justify-center mt-4">
        <Button onClick={onLoadMore} className="px-5 py-2 rounded-xl text-xs font-mono">Load 50 more</Button>
      </div>
    </section>
  );
}
