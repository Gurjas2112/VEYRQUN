# OWASP ASVS 4.0.3 — Level 2 status (control plane)

An engineering self-assessment, not a certification. **Status:** ✅ implemented and tested · 🟡 partial ·
⏳ planned (phase) · n/a. Evidence points at code or tests in this repo.

| ASVS | Requirement (abridged) | Status | Evidence |
|---|---|---|---|
| V1.4 | Access control enforced at a trusted service layer | ✅ | `auth/deps.py` `require()`; matrix in `auth/roles.py` |
| V1.6 | Key management policy; keys rotatable | ✅ | `keys.py` (`kid`, rotate, 24 h overlap), `crypto.py` key ring |
| V2.1 | Password length ≥ 12, no composition rules, no forced rotation | ✅ | Keycloak realm `passwordPolicy` (`deploy/keycloak`) |
| V2.1.7 | Breached-password check | 🟡 | Keycloak needs a blocklist file or IdP feature; documented, not bundled |
| V2.2 | Anti-automation / brute force | ✅ | Keycloak brute-force lockout with backoff; sandbox rate limits |
| V2.4 | Password hashing with a slow KDF | ✅ | Keycloak `hashAlgorithm(argon2)` |
| V2.7/2.8 | MFA (TOTP / WebAuthn) for privileged users | ✅ | `MFA_REQUIRED` roles; `test_privileged_roles_require_mfa`; e2e `test_privileged_role_without_mfa_is_refused` |
| V2.10 | Service credentials not hard-coded; rotatable | ✅ | agent client secrets hashed, shown once, `rotate-secret`; `.env` only |
| V3.2 | Session id from CSPRNG, new on login | ✅ | `sessions.create` (`secrets.token_urlsafe(32)`), old session destroyed on re-login |
| V3.3 | Logout and timeouts invalidate sessions | ✅ | idle/absolute per role, `/auth/logout`, back-channel logout; `test_idle_timeout_*` |
| V3.4 | Cookie attributes (`Secure`, `HttpOnly`, `SameSite`, `__Host-`) | ✅ | `routes/auth.py`; `test_successful_login_sets_hardened_cookies*`, e2e cookie check |
| V3.5 | Token-based sessions validated (signature, exp, aud, iss) | ✅ | `oidc.verify_id_token`, `deps._agent_principal`; `test_bad_id_tokens_are_rejected` (7 cases) |
| V3.7 | Re-authentication before sensitive operations | ✅ | `STEP_UP` permissions, approval kinds; `test_step_up_needed_for_kill_switch` |
| V4.1 | Least privilege, deny by default | ✅ | default-deny matrix; 358 generated authorization tests |
| V4.2 | Protection against IDOR | ✅ | RLS + scoped queries; `test_tenancy.py` IDOR cases |
| V4.3 | Admin interfaces need MFA; separation of duties | ✅ | `iam:manage` + MFA + step-up; SoD conflicts; maker-checker tests |
| V5.1 | Input validation (allowlists, types, lengths) | ✅ | pydantic `extra="forbid"`, patterns, limits; `test_input_validation` |
| V5.3 | Output encoding / injection | ✅ | parameterised SQL only (Semgrep clean); JSON API |
| V6.2 | Approved algorithms (AES-GCM, SHA-256, Ed25519) | ✅ | `crypto.py`, `audit.py`, `keys.py` |
| V6.4 | Secret management, keys separate from data | ✅ | envelope encryption; master key from env/KMS; gitleaks in CI |
| V7.1 | No sensitive data in logs | 🟡 | logs carry pseudonymous ids only; structured log redaction in Phase 7 |
| V7.2 | Security events logged | ✅ | auth, role, invite, key and operator events in the hash chain |
| V7.3 | Log integrity protection | ✅ | SHA-256 chain, append-only grants + trigger, signed export; HMAC + anchoring in Phase 3 |
| V8.1 | Sensitive data protected in storage | ✅ | encrypted identities, crypto-shredding (ADR 0009) |
| V8.3 | Data retention / deletion | 🟡 | sandbox TTL purge, erasure; partition archiving job in Phase 6 |
| V9.1 | TLS for all connections | 🟡 | HSTS set on https; TLS terminated by platform; mTLS in Phase 5 |
| V10.3 | Deployed code integrity | ✅ | deterministic, CI-verified `policy.wasm`; signed bundles |
| V11.1 | Business logic limits (rate, amounts) | ✅ | Redis Lua caps, rate limits; parity tests |
| V12.1 | Request size limits | ✅ | `BodySizeLimit` (1 MB); `test_oversized_bodies_are_rejected` |
| V13.1 | API authentication, CSRF for cookie auth | ✅ | double-submit CSRF + Origin; `test_csrf_and_origin_are_required*` |
| V13.2 | RESTful methods, content types | ✅ | JSON only; explicit methods in CORS |
| V14.2 | Dependencies scanned | 🟡 | pip/npm audit + Trivy SBOM in Phase 7 |
| V14.4 | Security headers (CSP, nosniff, frame-ancestors, HSTS, Referrer-Policy) | ✅ | `security.py`; `test_security_headers_present` |
| V14.5 | Origin / CORS restricted | ✅ | allowlist, never `*` in production (config validator) |

**Automated checks in CI:** Semgrep (`p/python`, `p/security-audit`, `p/secrets`), gitleaks, OWASP ZAP API scan
against the running control plane, the authorization matrix, and Keycloak e2e.
