from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError

from .models import ExternalIdentity


YANDEX_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_USERINFO_URL = "https://login.yandex.ru/info"
VKID_AUTHORIZE_URL = "https://id.vk.ru/authorize"
VKID_AUTH_URL = "https://id.vk.ru/oauth2/auth"
VKID_USERINFO_URL = "https://id.vk.ru/oauth2/user_info"


@dataclass(frozen=True, slots=True)
class ExternalProfile:
    provider: str
    subject: str


def configured_providers() -> tuple[str, ...]:
    providers: list[str] = []
    if getattr(settings, "YANDEX_OAUTH_CLIENT_ID", ""):
        providers.append(ExternalIdentity.Provider.YANDEX)
    if getattr(settings, "VKID_CLIENT_ID", ""):
        providers.append(ExternalIdentity.Provider.VK)
    return tuple(providers)


def provider_is_configured(provider: str) -> bool:
    return provider in configured_providers()


def generate_pkce_verifier() -> str:
    return secrets.token_urlsafe(48)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorization_url(
    *,
    provider: str,
    redirect_uri: str,
    state: str,
    code_verifier: str,
) -> str:
    challenge = pkce_challenge(code_verifier)

    if provider == ExternalIdentity.Provider.YANDEX:
        client_id = getattr(settings, "YANDEX_OAUTH_CLIENT_ID", "")
        if not client_id:
            raise ValidationError({"provider": "Yandex login is not configured."})
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": getattr(settings, "YANDEX_OAUTH_SCOPE", "login:info"),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{YANDEX_AUTHORIZE_URL}?{urlencode(params)}"

    if provider == ExternalIdentity.Provider.VK:
        client_id = getattr(settings, "VKID_CLIENT_ID", "")
        if not client_id:
            raise ValidationError({"provider": "VK ID login is not configured."})
        params = {
            "response_type": "code",
            "client_id": client_id,
            "app_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            # VK ID's current official Web SDK uses lowercase s256.
            "code_challenge_method": "s256",
        }
        scope = getattr(settings, "VKID_SCOPE", "").strip()
        if scope:
            params["scope"] = scope
        return f"{VKID_AUTHORIZE_URL}?{urlencode(params)}"

    raise ValidationError({"provider": "Unsupported external identity provider."})


def _request_json(
    *,
    url: str,
    method: str,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    encoded = None
    effective_headers = {"Accept": "application/json"}
    if headers:
        effective_headers.update(headers)
    if data is not None:
        encoded = urlencode(data).encode("utf-8")
        effective_headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(
        url,
        data=encoded,
        headers=effective_headers,
        method=method,
    )
    timeout = getattr(settings, "EXTERNAL_AUTH_HTTP_TIMEOUT_SECONDS", 10)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise ValidationError(
            {"provider": f"External identity provider rejected the request ({exc.code})."}
        ) from exc
    except URLError as exc:
        raise ValidationError(
            {"provider": "External identity provider is unavailable."}
        ) from exc

    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            {"provider": "External identity provider returned an invalid response."}
        ) from exc
    if not isinstance(result, dict):
        raise ValidationError(
            {"provider": "External identity provider returned an invalid response."}
        )
    if result.get("error"):
        raise ValidationError(
            {
                "provider": (
                    result.get("error_description")
                    or str(result.get("error"))
                )
            }
        )
    return result


def exchange_authorization_code(
    *,
    provider: str,
    code: str,
    redirect_uri: str,
    state: str,
    code_verifier: str,
    device_id: str | None = None,
) -> ExternalProfile:
    if provider == ExternalIdentity.Provider.YANDEX:
        client_id = getattr(settings, "YANDEX_OAUTH_CLIENT_ID", "")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        client_secret = getattr(settings, "YANDEX_OAUTH_CLIENT_SECRET", "")
        if client_secret:
            data["client_secret"] = client_secret
        token = _request_json(
            url=YANDEX_TOKEN_URL,
            method="POST",
            data=data,
        )
        access_token = token.get("access_token")
        if not access_token:
            raise ValidationError({"provider": "Yandex did not return an access token."})
        profile = _request_json(
            url=f"{YANDEX_USERINFO_URL}?format=json",
            method="GET",
            headers={"Authorization": f"OAuth {access_token}"},
        )
        subject = profile.get("psuid") or profile.get("id")
        if not subject:
            raise ValidationError({"provider": "Yandex user identifier is missing."})
        return ExternalProfile(
            provider=ExternalIdentity.Provider.YANDEX,
            subject=str(subject),
        )

    if provider == ExternalIdentity.Provider.VK:
        client_id = getattr(settings, "VKID_CLIENT_ID", "")
        if not device_id:
            raise ValidationError({"provider": "VK ID device identifier is missing."})
        query = urlencode(
            {
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
                "state": state,
                "device_id": device_id,
            }
        )
        token = _request_json(
            url=f"{VKID_AUTH_URL}?{query}",
            method="POST",
            data={"code": code},
        )
        returned_state = token.get("state")
        if returned_state != state:
            raise ValidationError({"provider": "VK ID state validation failed."})
        access_token = token.get("access_token")
        if not access_token:
            raise ValidationError({"provider": "VK ID did not return an access token."})
        profile = _request_json(
            url=f"{VKID_USERINFO_URL}?{urlencode({'client_id': client_id})}",
            method="POST",
            data={"access_token": access_token},
        )
        user_data = profile.get("user")
        subject = user_data.get("user_id") if isinstance(user_data, dict) else None
        if not subject:
            raise ValidationError({"provider": "VK ID user identifier is missing."})
        return ExternalProfile(
            provider=ExternalIdentity.Provider.VK,
            subject=str(subject),
        )

    raise ValidationError({"provider": "Unsupported external identity provider."})
