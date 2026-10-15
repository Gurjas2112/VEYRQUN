export const KNOWN_ACTIONS =
  'flight_rebook, hotel_rebook, waive_fee, account_summary, balance_check, wire_transfer, credit_limit_increase, score_check, card_issue, card_lock';

export const splitList = (text) => text.split(',').map((s) => s.trim()).filter(Boolean);

export const parseActionLimits = (text) =>
  Object.fromEntries(
    splitList(text).map((pair) => {
      const [action, amount] = pair.split(':').map((s) => s.trim());
      return [action, parseFloat(amount)];
    }).filter(([action, amount]) => action && !Number.isNaN(amount))
  );

export const formatActionLimits = (limits) => Object.entries(limits || {}).map(([a, v]) => `${a}:${v}`).join(', ');
