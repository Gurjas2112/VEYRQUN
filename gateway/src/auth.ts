/** Agent authentication: short-lived Ed25519 JWTs issued by the control plane, verified via its JWKS. */
import { createRemoteJWKSet, errors, jwtVerify, type JWTPayload } from "jose";

export interface AgentIdentity {
  agentId: string;
  workspaceId: string;
  scopes: string[];
  claims: JWTPayload;
}

export class AuthError extends Error {}

export class AgentAuth {
  private readonly jwks: ReturnType<typeof createRemoteJWKSet>;

  constructor(jwksUrl: string, private readonly issuer: string, private readonly audience: string) {
    // jose caches keys, refreshes on an unknown kid, and rate-limits refetches (cooldown).
    this.jwks = createRemoteJWKSet(new URL(jwksUrl), { cooldownDuration: 30_000, cacheMaxAge: 600_000, timeoutDuration: 3_000 });
  }

  async verify(authorization: string | undefined): Promise<AgentIdentity> {
    if (!authorization?.toLowerCase().startsWith("bearer ")) throw new AuthError("missing bearer token");
    const token = authorization.slice(7).trim();
    try {
      const { payload } = await jwtVerify(token, this.jwks, {
        issuer: this.issuer,
        audience: this.audience,
        algorithms: ["EdDSA"],           // never `none`, never HS* from an external issuer
        requiredClaims: ["sub", "exp", "iat", "ws"],
        clockTolerance: 30,
      });
      return {
        agentId: String(payload.sub),
        workspaceId: String(payload.ws),
        scopes: String(payload.scope ?? "").split(" ").filter(Boolean),
        claims: payload,
      };
    } catch (err) {
      if (err instanceof errors.JOSEError) throw new AuthError(err.code);
      throw new AuthError("token verification failed");
    }
  }
}
