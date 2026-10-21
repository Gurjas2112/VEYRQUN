-- Atomic check-and-reserve for live counters. Shared by the gateway (TypeScript) and the
-- control plane (Python) so both enforce identical semantics.
--
-- KEYS[1] daily spend (integer minor units)        vq:{agent}:spend:<local-date>
-- KEYS[2] per-action daily spend                     vq:{agent}:aspend:<action>:<local-date>
-- KEYS[3] sliding rate window (sorted set ms -> id)  vq:{agent}:rate
-- ARGV: amount_minor, spend_cap_minor, action_cap_minor, rate_limit, now_ms, window_ms, reservation_id, ttl_s
--       (a cap/limit of -1 means "no limit")
-- Returns {1, "", new_spend} on success, {0, rule_id, current_spend} when a limit would be exceeded.
-- Order matches the policy oracle: rate -> daily spend -> per-action daily spend.

local amount = tonumber(ARGV[1])
local spend_cap = tonumber(ARGV[2])
local action_cap = tonumber(ARGV[3])
local rate_limit = tonumber(ARGV[4])
local now_ms = tonumber(ARGV[5])
local window_ms = tonumber(ARGV[6])
local reservation = ARGV[7]
local ttl = tonumber(ARGV[8])

redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now_ms - window_ms)
if rate_limit >= 0 and redis.call('ZCARD', KEYS[3]) >= rate_limit then
  return {0, 'rate_limit_exceeded', tonumber(redis.call('GET', KEYS[1]) or '0')}
end

local spend = tonumber(redis.call('GET', KEYS[1]) or '0')
if spend_cap >= 0 and spend + amount > spend_cap then
  return {0, 'spend_cap_exceeded', spend}
end

local action_spend = tonumber(redis.call('GET', KEYS[2]) or '0')
if action_cap >= 0 and action_spend + amount > action_cap then
  return {0, 'action_spend_cap_exceeded', spend}
end

local new_spend = redis.call('INCRBY', KEYS[1], amount)
redis.call('INCRBY', KEYS[2], amount)
redis.call('ZADD', KEYS[3], now_ms, reservation)
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('EXPIRE', KEYS[2], ttl)
redis.call('PEXPIRE', KEYS[3], window_ms * 2)
return {1, '', new_spend}
