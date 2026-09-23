from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address, ip_network

from django.conf import settings


def _parse_ip(value: str | None):
    if not value:
        return None
    try:
        return ip_address(value.strip())
    except ValueError:
        return None


@lru_cache(maxsize=32)
def _trusted_proxy_networks(values: tuple[str, ...]):
    networks = []
    for value in values:
        try:
            networks.append(ip_network(value.strip(), strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted_proxy(remote_addr) -> bool:
    if remote_addr is None:
        return False
    if getattr(remote_addr, "ipv4_mapped", None) is not None:
        remote_addr = remote_addr.ipv4_mapped
    values = tuple(getattr(settings, "TRUSTED_PROXY_IPS", ()))
    return any(
        remote_addr.version == network.version
        and remote_addr in network
        for network in _trusted_proxy_networks(values)
    )


def get_axes_client_ip(request) -> str | None:
    """Trust X-Real-IP only from an explicitly trusted proxy network."""
    remote_addr = _parse_ip(request.META.get("REMOTE_ADDR"))

    if _is_trusted_proxy(remote_addr):
        forwarded = _parse_ip(request.META.get("HTTP_X_REAL_IP"))
        if forwarded is not None:
            return str(forwarded)

    return str(remote_addr) if remote_addr is not None else None
