import pytest
from pydantic import ValidationError

from app.models.schemas import ModelAction


@pytest.mark.parametrize('kind,expected', [
    ('click', {'kind', 'x', 'y', 'hold_ms'}),
    ('double_click', {'kind', 'x', 'y', 'hold_ms'}),
    ('replace_text', {'kind', 'text', 'x', 'y', 'text_targets'}),
    ('press_key', {'kind', 'keys'}),
    ('scroll', {'kind', 'direction', 'amount'}),
])
def test_flat_provider_slots_cannot_send_another_action_kinds_payload(kind, expected):
    proposed = ModelAction(kind=kind, x=10, y=20, hold_ms=180, keys=('Return',),
                           text='SYNTHETIC_TEXT_CANARY', direction='down', amount=3)
    action = proposed.to_action()
    assert set(action.model_dump()) == expected
    if kind != 'replace_text':
        assert 'SYNTHETIC_TEXT_CANARY' not in str(action.model_dump())


def test_missing_selected_kind_fields_are_still_rejected():
    proposed = ModelAction(kind='click', x=None, y=20, hold_ms=180, keys=None,
                           text='Not a substitute for coordinates', direction=None, amount=None)
    with pytest.raises(ValidationError):
        proposed.to_action()


def test_targeted_text_requires_complete_coordinate_pair():
    proposed = ModelAction(kind='replace_text', x=10, y=None, hold_ms=None, keys=None,
                           text='Synthetic', direction=None, amount=None)
    with pytest.raises(ValidationError):
        proposed.to_action()


def test_text_group_rejects_mixed_target_modes_and_duplicate_inputs():
    from app.domain.contracts import TextArgs, TextTarget

    target = TextTarget(x=10, y=20, text='Synthetic')
    for values in ({'text': 'Other', 'text_targets': (target,)},
                   {'text': '', 'x': 10, 'y': 20, 'text_targets': (target,)},
                   {'text': '', 'text_targets': (target, target)}):
        with pytest.raises(ValidationError):
            TextArgs(**values)
