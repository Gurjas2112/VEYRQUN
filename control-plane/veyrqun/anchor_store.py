"""Off-database copies of signed audit anchors, on any S3-compatible store (AWS S3, Supabase Storage, MinIO).

A minimal AWS Signature V4 PUT (path-style), so the control plane needs no AWS SDK. An attacker who controls the
database can't reach this copy, so a fully rewritten chain (hashes and HMACs recomputed) still disagrees with it.
Supabase Storage has no Object Lock, so the copy is tamper-evident (signed), not WORM. See ADR 0012.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx

from .config import get_settings


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k_date = _hmac(("AWS4" + secret).encode(), date)
    return _hmac(_hmac(_hmac(k_date, region), service), "aws4_request")


def sigv4_headers(method: str, url: str, body: bytes, *, access_key: str, secret: str, region: str,
                  headers: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, str]:
    """Headers for an AWS Signature V4 (service s3) request: every given header is signed, plus host,
    x-amz-content-sha256 and x-amz-date."""
    now = now or datetime.now(UTC)
    amz_date, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    parts = urlsplit(url)
    payload_hash = hashlib.sha256(body).hexdigest()
    signed_headers = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
    signed_headers.update({"host": parts.netloc, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date})
    names = sorted(signed_headers)
    canonical_request = "\n".join([
        method, quote(parts.path, safe="/-_.~"), parts.query,
        "".join(f"{k}:{signed_headers[k]}\n" for k in names), ";".join(names), payload_hash,
    ])
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    signature = hmac.new(signing_key(secret, date, region, "s3"), to_sign.encode(), hashlib.sha256).hexdigest()
    signed_headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                                       f"SignedHeaders={';'.join(names)}, Signature={signature}")
    signed_headers.pop("host")
    return signed_headers


def sigv4_put_headers(url: str, body: bytes, *, access_key: str, secret: str, region: str,
                      content_type: str = "application/json", now: datetime | None = None) -> dict[str, str]:
    return sigv4_headers("PUT", url, body, access_key=access_key, secret=secret, region=region,
                         headers={"content-type": content_type}, now=now)


def s3_put() -> Callable[[str, bytes], str] | None:
    """A `put(name, body) -> ref` for the configured bucket, or None when off-database anchoring is off."""
    s = get_settings()
    if not (s.anchor_s3_endpoint and s.anchor_s3_bucket and s.anchor_s3_access_key_id and s.anchor_s3_secret_access_key):
        return None
    if not s.anchor_s3_endpoint.startswith("https://") and s.veyrqun_env == "production":
        raise ValueError("ANCHOR_S3_ENDPOINT must be https in production")

    def put(name: str, body: bytes) -> str:
        url = f"{s.anchor_s3_endpoint.rstrip('/')}/{s.anchor_s3_bucket}/{name}"
        headers = sigv4_put_headers(url, body, access_key=s.anchor_s3_access_key_id, secret=s.anchor_s3_secret_access_key,
                                    region=s.anchor_s3_region)
        r = httpx.put(url, content=body, headers=headers, timeout=10)
        r.raise_for_status()
        return f"s3://{s.anchor_s3_bucket}/{name}"

    return put
