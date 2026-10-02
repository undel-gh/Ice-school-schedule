from .external_auth import configured_providers
from .mfa import mfa_required_for_user


def external_auth(request):
    providers = configured_providers()
    return {
        "external_auth_providers": providers,
        "yandex_external_auth_available": "yandex" in providers,
        "vk_external_auth_available": "vk" in providers,
        "privileged_mfa_available": (
            request.user.is_authenticated
            and mfa_required_for_user(
                request.user,
                use_request_cache=True,
            )
        ),
    }
