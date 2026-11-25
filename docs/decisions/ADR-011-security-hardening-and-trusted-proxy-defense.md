# ADR-011: Security Hardening, Anti-Spoofing Trusted Proxy Verification, and HTTP Defenses

- **Status**: Accepted
- **Date**: 2026-11-20
- **Deciders**: Architecture Team, Security Engineering
- **Phase**: Phase 10: Security Hardening
- **Nature of Rationale**: Original

---

## 1. Context and Problem

Following the Phase 9 deployment and pilot release, a thorough security audit of IssueLens identified several critical vulnerabilities and latent attack vectors:
1. **X-Forwarded-For Rate Limit Spoofing**: The in-memory sliding-window rate limiter extracted client identifiers by naively trusting the `X-Forwarded-For` HTTP header from any incoming request. An adversary could rotate arbitrary fake IP addresses in this header on every request to completely bypass rate limits and exhaust API quotas ("Denial of Wallet").
2. **Missing HTTP Security Headers**: FastAPI was serving responses without foundational defense-in-depth headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and `Permissions-Policy`). In production, modern browsers require these headers to prevent MIME confusion attacks and clickjacking.
3. **Missing HSTS Protection**: Production traffic over HTTPS lacked `Strict-Transport-Security`, leaving clients vulnerable to SSL-stripping man-in-the-middle attacks.
4. **Public Database Port Exposure**: `docker-compose.yml` bound PostgreSQL port `5432:5432` to `0.0.0.0`, potentially exposing the database to the public internet on unfirewalled cloud hosts.
5. **Prompt Injection Tag Escaping**: Raw untrusted issue titles, bodies, and repository source code placed inside `<untrusted_issue_data>` and `<untrusted_repository_evidence>` XML boundary tags could contain literal closing tags, breaking the enclave.

---

## 2. Decision and Rationale

We commit to the following architectural and security controls:

1. **Anti-Spoofing Trusted Proxy Verification (`issuelens.api.security.is_trusted_proxy`)**:
   - `get_client_identifier()` inspects the direct connection peer IP (`request.client.host`).
   - The `X-Forwarded-For` header is **only** parsed if the direct connection originates from an explicitly configured trusted reverse proxy (matching an IP or CIDR network block in `Settings.trusted_proxies`).
   - Untrusted direct callers have their actual socket IP enforced, preventing header spoofing.
   - Defaults to loopback addresses `["127.0.0.1", "::1"]`.

2. **HTTP Security Headers Middleware (`issuelens.main.create_app`)**:
   - Standard security headers attached to every response:
     - `X-Content-Type-Options: nosniff` (mitigates MIME-sniffing exploits).
     - `X-Frame-Options: DENY` (mitigates clickjacking).
     - `Referrer-Policy: strict-origin-when-cross-origin` (prevents credential leakage in referrer strings).
     - `Permissions-Policy: geolocation=(), camera=(), microphone=()` (disables unused browser capabilities).
     - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (enforced when request is HTTPS or `app_env == "production"`).

3. **CORS Allowlist Protection (`fastapi.middleware.cors.CORSMiddleware`)**:
   - Configurable `Settings.cors_allowed_origins` restrict cross-origin browser interactions to trusted origins (defaulting to local development ports `http://localhost:3000` and `http://localhost:8000`).

4. **Production Authentication Enforcement (`issuelens.config.Settings`)**:
   - A Pydantic model validator strictly prevents the application from starting in `app_env == "production"` unless `auth_required=True` and a non-empty `api_key` are provided.

5. **Prompt Injection Boundary Escaping (`issuelens.agent.prompts.sanitize_boundary_tags`)**:
   - All untrusted issue strings and evidence blocks escape literal XML boundary closing tags (`</untrusted_issue_data>`, `</untrusted_repository_evidence>`) before prompt interpolation.

6. **Database Host Binding Restriction (`docker-compose.yml`)**:
   - PostgreSQL port mapping is restricted strictly to `127.0.0.1:5432:5432` so the database port is never accessible from public network interfaces.

---

## 3. Alternatives Considered

### Alternative A: Relying Exclusively on an Upstream Cloud Reverse Proxy (e.g., Cloudflare / AWS WAF)
- **Description**: Assume all security headers, rate limiting, and SSL enforcement are handled by an external CDN or WAF layer.
- **Pros**: Reduces Python runtime processing overhead.
- **Cons**: Violates defense-in-depth principles. Single-container deployments, Docker Compose stacks, and direct VPS deployments would be left completely exposed without an upstream gateway.

### Alternative B: Using Client IP Direct Sockets Only (Disallowing X-Forwarded-For Entirely)
- **Description**: Completely ignore `X-Forwarded-For` and rate limit exclusively on `request.client.host`.
- **Pros**: Zero risk of header spoofing.
- **Cons**: Completely breaks rate limiting behind valid reverse proxies (such as NGINX, Cloudflare, or AWS ALB), because all incoming requests would share the single proxy IP and throttle each other.

---

## 4. Why the Alternatives Were Not Selected

1. **Alternative A**: Defense-in-depth requires application services to be secure by default even when exposed without a specialized external gateway.
2. **Alternative B**: Legitimate multi-tier deployments require proper client IP resolution; configuring trusted proxy CIDRs provides security without sacrificing reverse proxy compatibility.

---

## 5. Trade-offs and Consequences

### Benefits
- **Zero-Bypass Rate Limiting**: Adversaries cannot evade sliding-window quotas by forging HTTP headers.
- **MIME & Clickjacking Defense**: Modern browsers enforce strict isolation based on security headers.
- **Production Guardrails**: Misconfiguration (deploying to production without an API key) is caught fail-fast at boot time.
- **Safe Docker Exposure**: Internal databases cannot be brute-forced from the open internet.

### Drawbacks & Limitations
- When deploying behind a new reverse proxy or Kubernetes ingress controller, operators must ensure the proxy's IP/subnet is added to `TRUSTED_PROXIES` in `.env` so that legitimate client IPs are correctly identified.

---

## 6. Implementation References

- **Anti-Spoofing & Client Identification**: [`src/issuelens/api/security.py`](../../src/issuelens/api/security.py)
- **Security Headers & CORS Middleware**: [`src/issuelens/main.py`](../../src/issuelens/main.py)
- **Configuration & Validation**: [`src/issuelens/config.py`](../../src/issuelens/config.py)
- **XML Enclave Escaping**: [`src/issuelens/agent/prompts.py`](../../src/issuelens/agent/prompts.py)
- **Unit & Integration Tests**:
  - [`tests/unit/test_trusted_proxies.py`](../../tests/unit/test_trusted_proxies.py)
  - [`tests/unit/test_security_headers.py`](../../tests/unit/test_security_headers.py)
  - [`tests/unit/agent/test_prompt_injection_defense.py`](../../tests/unit/agent/test_prompt_injection_defense.py)
  - [`tests/unit/test_config.py`](../../tests/unit/test_config.py)

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
- Advanced multi-tenant authentication requirements necessitate distributed token buckets (e.g., Redis-backed limiting).
- Web application needs mandate complex Content Security Policy (CSP) nonces for inline JavaScript rendering.

---

## 8. Related ADRs

- [ADR-010: Production Deployment Containerization, API Authentication, and Rate Limiting](ADR-010-production-deployment-and-api-hardening.md): Precedes and is complemented by ADR-011.
