from __future__ import annotations

from ipaddress import ip_network

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security)
def trusted_proxy_configuration_check(app_configs, **kwargs):
    warnings = []
    values = tuple(getattr(settings, "TRUSTED_PROXY_IPS", ()))

    invalid = []
    for value in values:
        try:
            ip_network(value, strict=False)
        except ValueError:
            invalid.append(value)

    if invalid:
        warnings.append(
            Warning(
                "TRUSTED_PROXY_IPS contains invalid IP/CIDR entries.",
                hint="Fix or remove: " + ", ".join(invalid),
                id="ice_school.W001",
            )
        )

    if not settings.DEBUG and not values:
        warnings.append(
            Warning(
                "TRUSTED_PROXY_IPS is empty in production settings.",
                hint=(
                    "If the application is behind a reverse proxy, configure "
                    "its IP/CIDR so Axes can trust X-Real-IP. For direct "
                    "deployments this warning can be accepted."
                ),
                id="ice_school.W002",
            )
        )

    return warnings
