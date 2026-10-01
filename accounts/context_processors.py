from .external_auth import configured_providers


def external_auth(request):
    providers = configured_providers()
    return {
        "external_auth_providers": providers,
        "yandex_external_auth_available": "yandex" in providers,
        "vk_external_auth_available": "vk" in providers,
    }
