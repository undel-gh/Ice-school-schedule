from __future__ import annotations

from django.core.exceptions import ValidationError


def validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return " ".join(
            message
            for messages_ in exc.message_dict.values()
            for message in messages_
        )
    return " ".join(exc.messages)
