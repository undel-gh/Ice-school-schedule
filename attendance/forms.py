from django import forms


class ManagerMedicalVerifyForm(forms.Form):
    valid_until = forms.DateField(
        label="Отработка действительна по",
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class ManagerAttendanceCoverageRebindForm(forms.Form):
    source = forms.ChoiceField(label="Новое покрытие")

    def __init__(self, *args, targets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._targets = {target.key: target for target in targets}
        self.fields["source"].choices = [
            (target.key, target.label)
            for target in targets
        ]

    @property
    def has_choices(self) -> bool:
        return bool(self._targets)

    def target_kwargs(self) -> dict:
        target = self._targets[self.cleaned_data["source"]]
        return {
            "one_time_entitlement_id": target.one_time_entitlement_id,
            "subscription_allowance_id": target.subscription_allowance_id,
            "makeup_entitlement_id": target.makeup_entitlement_id,
        }



class ManagerAttendanceCoverageRecoveryForm(
    ManagerAttendanceCoverageRebindForm
):
    source = forms.ChoiceField(
        label="Источник покрытия",
        required=False,
    )

    def target_kwargs(self) -> dict:
        if not self.cleaned_data["source"]:
            return {
                "one_time_entitlement_id": None,
                "subscription_allowance_id": None,
                "makeup_entitlement_id": None,
            }
        return super().target_kwargs()
