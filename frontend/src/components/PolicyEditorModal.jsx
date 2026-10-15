import { useState } from 'react';
import { Button, Field, Modal, inputClass } from './ui';
import { KNOWN_ACTIONS, formatActionLimits, parseActionLimits, splitList } from '../policyFormat';

// Blank optional fields are sent as explicit null, which clears that limit server-side.
const optionalNumber = (text, parse = parseFloat) => (String(text).trim() === '' ? null : parse(text));

export default function PolicyEditorModal({ agent, onClose, onSave }) {
  const [form, setForm] = useState({
    allowed: agent.allowed_actions.join(', '),
    blocked: agent.blocked_actions.join(', '),
    spendCap: agent.spend_cap_daily,
    perTx: agent.max_per_transaction ?? '',
    rate: agent.rate_limit_per_minute ?? '',
    actionLimits: formatActionLimits(agent.action_limits),
    note: '',
  });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const submit = (e) => {
    e.preventDefault();
    onSave(agent.agent_id, {
      allowed_actions: splitList(form.allowed),
      blocked_actions: splitList(form.blocked),
      spend_cap_daily: parseFloat(form.spendCap),
      max_per_transaction: optionalNumber(form.perTx),
      rate_limit_per_minute: optionalNumber(form.rate, (v) => parseInt(v, 10)),
      action_limits: parseActionLimits(form.actionLimits),
      note: form.note || undefined,
    });
  };

  return (
    <Modal title={`Edit Policy — ${agent.display_name}`} subtitle={`Publishes policy v${agent.policy_version + 1} · takes effect on the next action`} onClose={onClose}>
      <p className="text-[10px] text-[#8A93A3] mb-4 font-mono">Known actions: {KNOWN_ACTIONS}</p>
      <form onSubmit={submit} className="space-y-3 text-xs">
        <Field label="Allowed actions" hint="comma separated — anything not listed is denied">
          <input value={form.allowed} onChange={set('allowed')} className={inputClass} />
        </Field>
        <Field label="Blocked actions" hint="explicit deny — always wins over the allow-list">
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
        <Field label="Per-action caps" hint="action:amount, e.g. waive_fee:50, wire_transfer:200">
          <input value={form.actionLimits} onChange={set('actionLimits')} className={inputClass} />
        </Field>
        <Field label="Change note" hint="stored with the policy version">
          <input value={form.note} onChange={set('note')} className={inputClass} placeholder="e.g. tighten cap after incident #142" />
        </Field>
        <div className="flex items-center justify-end gap-3 pt-4 border-t border-[#1E232A]">
          <Button type="button" onClick={onClose} className="px-4 py-2 rounded-xl">Cancel</Button>
          <Button type="submit" variant="signal" className="px-4 py-2 rounded-xl">Publish Policy</Button>
        </div>
      </form>
    </Modal>
  );
}
