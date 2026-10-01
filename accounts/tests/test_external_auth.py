from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from django.core.exceptions import ValidationError

from accounts import external_auth
from accounts.models import ExternalIdentity


@pytest.mark.django_db
def test_yandex_authorization_url_uses_state_and_pkce(settings):
    settings.YANDEX_OAUTH_CLIENT_ID = "ya-client"
    settings.YANDEX_OAUTH_SCOPE = "login:info"

    url = external_auth.build_authorization_url(
        provider=ExternalIdentity.Provider.YANDEX,
        redirect_uri="https://school.example/accounts/external/yandex/callback/",
        state="state-value",
        code_verifier="v" * 64,
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "oauth.yandex.ru"
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["ya-client"]
    assert params["state"] == ["state-value"]
    assert params["code_challenge_method"] == ["s256"]
    assert params["code_challenge"][0] != "v" * 64


@pytest.mark.django_db
def test_vk_authorization_url_uses_oauth21_pkce(settings):
    settings.VKID_CLIENT_ID = "12345"
    settings.VKID_SCOPE = ""

    url = external_auth.build_authorization_url(
        provider=ExternalIdentity.Provider.VK,
        redirect_uri="https://school.example/accounts/external/vk/callback/",
        state="vk-state",
        code_verifier="x" * 64,
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.netloc == "id.vk.ru"
    assert params["client_id"] == ["12345"]
    assert params["app_id"] == ["12345"]
    assert params["state"] == ["vk-state"]
    assert params["redirect_uri"] == [
        "https://school.example/accounts/external/vk/callback/"
    ]
    assert params["code_challenge_method"] == ["S256"]


@pytest.mark.django_db
def test_yandex_code_exchange_uses_pairwise_subject(settings, monkeypatch):
    settings.YANDEX_OAUTH_CLIENT_ID = "ya-client"
    settings.YANDEX_OAUTH_CLIENT_SECRET = ""
    calls = []

    def fake_request_json(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"access_token": "ya-token"}
        return {
            "id": "global-id",
            "psuid": "pairwise-id",
        }

    monkeypatch.setattr(external_auth, "_request_json", fake_request_json)
    profile = external_auth.exchange_authorization_code(
        provider=ExternalIdentity.Provider.YANDEX,
        code="code",
        redirect_uri="https://school.example/callback",
        state="state",
        code_verifier="verifier",
    )

    assert profile.subject == "pairwise-id"
    assert calls[0]["data"]["code_verifier"] == "verifier"
    assert calls[1]["headers"]["Authorization"] == "OAuth ya-token"


@pytest.mark.django_db
def test_vk_code_exchange_requires_device_and_fetches_user_info(
    settings,
    monkeypatch,
):
    settings.VKID_CLIENT_ID = "12345"
    calls = []

    def fake_request_json(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "access_token": "vk-token",
                "state": "state",
            }
        return {"user": {"user_id": "777"}}

    monkeypatch.setattr(external_auth, "_request_json", fake_request_json)

    profile = external_auth.exchange_authorization_code(
        provider=ExternalIdentity.Provider.VK,
        code="code",
        redirect_uri="https://school.example/callback",
        state="state",
        code_verifier="verifier",
        device_id="device-1",
    )

    assert profile.subject == "777"
    assert "device_id=device-1" in calls[0]["url"]
    assert calls[0]["data"] == {"code": "code"}
    assert calls[1]["data"] == {"access_token": "vk-token"}

    with pytest.raises(ValidationError):
        external_auth.exchange_authorization_code(
            provider=ExternalIdentity.Provider.VK,
            code="code",
            redirect_uri="https://school.example/callback",
            state="state",
            code_verifier="verifier",
            device_id=None,
        )



@pytest.mark.django_db
def test_vk_code_exchange_rejects_missing_token_state(settings, monkeypatch):
    settings.VKID_CLIENT_ID = "12345"

    monkeypatch.setattr(
        external_auth,
        "_request_json",
        lambda **kwargs: {"access_token": "vk-token"},
    )

    with pytest.raises(ValidationError) as exc:
        external_auth.exchange_authorization_code(
            provider=ExternalIdentity.Provider.VK,
            code="code",
            redirect_uri="https://school.example/callback",
            state="expected-state",
            code_verifier="verifier",
            device_id="device-1",
        )

    assert "state validation failed" in exc.value.message_dict["provider"][0]
