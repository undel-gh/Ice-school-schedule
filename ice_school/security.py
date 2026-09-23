from __future__ import annotations

from ipaddress import ip_address

from django.conf import settings


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    try:
        return str(ip_address(candidate))
    except ValueError:
        return None


def get_axes_client_ip(request) -> str | None:
    """Resolve client IP, trusting X-Real-IP only from configured proxies."""
    remote_addr = _valid_ip(request.META.get("REMOTE_ADDR"))
    trusted_proxies = set(getattr(settings, "TRUSTED_PROXY_IPS", ()))

    if remote_addr in trusted_proxies:
        forwarded = _valid_ip(request.META.get("HTTP_X_REAL_IP"))
        if forwarded is not None:
            return forwarded

    return remote_addr
