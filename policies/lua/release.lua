-- Roll back a reservation made by reserve.lua (for example when the upstream call fails).
-- KEYS: same as reserve.lua.  ARGV: amount_minor, reservation_id
-- Only releases if the reservation still exists, so a double release is a no-op.

local amount = tonumber(ARGV[1])
if redis.call('ZREM', KEYS[3], ARGV[2]) == 0 then
  return 0
end
local spend = tonumber(redis.call('GET', KEYS[1]) or '0')
redis.call('SET', KEYS[1], math.max(0, spend - amount), 'KEEPTTL')
local action_spend = tonumber(redis.call('GET', KEYS[2]) or '0')
redis.call('SET', KEYS[2], math.max(0, action_spend - amount), 'KEEPTTL')
return 1
