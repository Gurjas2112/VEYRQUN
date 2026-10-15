import { useState } from 'react';
import { Button, Field, Modal, inputClass } from './ui';
import { KNOWN_ACTIONS, parseActionLimits, splitList } from '../policyFormat';

export default function CreateAgentModal({ onClose, onCreate }) {
  const [form, setForm] = useState({
    agentId: '',
    name: '',
    allowed: '',
    blocked: '',
    spendCap: 1000,
    perTx: '',
    rate: '',
    actionLimits: '',
  });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const submit = (e) => {
    e.preventDefault();
    onCreate({
      agent_id: form.agentId.trim(),
      display_name: form.name.trim(),
      allowed_actions: splitList(form.allowed),
      blocked_actions: splitList(form.blocked),
      spend_cap_daily: parseFloat(form.spendCap),
      max_per_transaction: form.perTx === '' ? null : parseFloat(form.perTx),
      rate_limit_per_minute: form.rate === '' ? null : parseInt(form.rate, 10),
      action_limits: parseActionLimits(form.actionLimits),
    });
  };

  return (
    <Modal title="Register Agent" subtitle="New agents start active with policy v1 — least privilege by default." onClose={onClose}>
      <p className="text-[10px] text-[#8A93A3] mb-4 font-mono">Known actions: {KNOWN_ACTIONS}</p>
      <form onSubmit={submit} className="space-y-3 text-xs">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Agent ID" hint="lowercase, digits, _ or -">
            <input required pattern="[a-z0-9][a-z0-9_\-]{2,63}" value={form.agentId} onChange={set('agentId')} className={inputClass} placeholder="dispute_agent_06" />
          </Field>
          <Field label="Display name">
            <input required value={form.name} onChange={set('name')} className={inputClass} placeholder="Dispute Resolution Agent" />
          </Field>
        </div>
        <Field label="Allowed actions" hint="comma separated">
          <input value={form.allowed} onChange={set('allowed')} className={inputClass} />
        </Field>
        <Field label="Blocked actions" hint="comma separated">
          <input value={form.blocked} onChange={set('blocked')} className={inputClass} />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Daily cap ($)">
            <input type="number" step="0.01" min="0" required value={form.spendCap} onChange={set('spendCap')} className={inputClass} />
          </Field>
          <Field label="Per-tx limit ($)" hint="blank = none">
            <input type="number" step="0.01" min="0" value={form.perTx} onChange={set('perTx')} className={inputClass} />
          </Field>
          <Field label="Rate (/min)" hint="blank = none">
            <input type="number" step="1" min="1" value={form.rate} onChange={set('rate')} className={inputClass} />
          </Field>
        </div>
        <Field label="Per-action caps" hint="action:amount, comma separated">
          <input value={form.actionLimits} onChange={set('actionLimits')} className={inputClass} />
        </Field>
        <div className="flex items-center justify-end gap-3 pt-4 border-t border-[#1E232A]">
          <Button type="button" onClick={onClose} className="px-4 py-2 rounded-xl">Cancel</Button>
          <Button type="submit" variant="signal" className="px-4 py-2 rounded-xl">Register</Button>
        </div>
      </form>
    </Modal>
  );
}
