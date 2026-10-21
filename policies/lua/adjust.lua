-- Correct a reservation once the real cost is known (e.g. LLM token usage vs the estimate).
-- KEYS[1] daily spend, KEYS[2] per-action daily spend.  ARGV[1] = delta in minor units (may be negative).
-- Floors at 0. A positive delta can take spend past the cap; that overrun is recorded, not hidden.

local delta = tonumber(ARGV[1])
local out = 0
for i = 1, 2 do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  local next_value = math.max(0, current + delta)
  redis.call('SET', KEYS[i], next_value, 'KEEPTTL')
  if i == 1 then out = next_value end
end
return out
