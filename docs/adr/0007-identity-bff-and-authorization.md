# 0007. Identity: OIDC backend-for-frontend; the IdP authenticates, VEYRQUN authorizes

**Status:** accepted · 2026-10-22

## Context
Operators (admins, security operators, approvers, auditors, app owners, viewers) need strong login with MFA, while
customers will bring their own IdP (Entra ID, Okta, Google Workspace, Keycloak). Roles are per workspace, and some
changes need a second person. Tokens in the browser (localStorage) are exposed to XSS.

## Decision
- **Authentication** is delegated to any OIDC provider via Authorization Code + PKCE (S256), state and nonce, in a
  backend-for-frontend inside the control plane. ID tokens are verified strictly: signature via JWKS by `kid` (refreshed
  once on an unknown kid), an algorithm allowlist (`RS256/PS256/ES256/EdDSA`; never `none` or HS* from an external
  issuer), plus `iss`, `aud`, `exp`, `nbf`/`iat` and `nonce`. The IdP must assert `email_verified`.
- **Tokens never reach the browser.** The browser holds an opaque, `HttpOnly`, `Secure`, `SameSite=Lax`,
  `__Host-` session cookie. Sessions live in Redis under a hash of their id. Idle/absolute timeouts are 15 min / 8 h for
  admin roles and 30 min / 12 h otherwise. State-changing requests need a double-submit CSRF token and an allowed `Origin`.
- **Authorization is VEYRQUN's**: per-workspace roles in `iam.memberships`, enforced on every endpoint from one matrix
  (`auth/roles.py`). The same matrix generates the endpoint x role tests (358 cases). The IdP carries no VEYRQUN roles,
  so swapping IdPs needs no role mapping.
- **MFA** comes from the IdP's `amr` (`otp`, `hwk`, …), Supabase's `aal2`, or configured `acr` values. It is mandatory
  for `platform_admin`, `security_operator` and `approver`.
- **Step-up** (`prompt=login&max_age=0`; `auth_time` must be < 5 min old) is required for the fleet kill switch,
  kill-switch release, policy publish approvals, role changes and key rotation.
- **Maker-checker**: production policy publishes and rollbacks, kill-switch release and grants of `platform_admin` become
  change requests that a different person with `approver` must approve. Self-approval is refused, and both identities
  are recorded.
- Agents are not users. They use OAuth client credentials and receive short-lived Ed25519 JWTs (ADR 0004).
- Org users are **invite-only** (72 h, single-use, bound to the IdP-verified email). Public self-signup only creates a
  quota-limited, 7-day sandbox workspace (rate-limited; Cloudflare Turnstile when configured). The first admin comes
  from `veyrqun admin bootstrap` (CLI), never a web form.

## Consequences
- Keycloak 26 in compose provides password + TOTP with NIST SP 800-63B password policy and brute-force lockout. Playwright
  e2e tests log in through the real Keycloak pages. Finding during e2e: Keycloak's AMR references need
  `default.reference.maxAge`, otherwise `amr` is intermittently empty and MFA users look single-factor.
- The hosted demo can point the same code at another OIDC provider by configuration only.
