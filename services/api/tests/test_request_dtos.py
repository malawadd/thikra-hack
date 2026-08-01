"""Request-DTO bounds — the seed prompt must accept a detailed brief.

A 917-char art-directed prompt 422'd against the old 500-char cap before the
handler ran. Both DTOs share `_PROMPT_MAX`, so the storyboard endpoint and the
media stream (which re-sends the same seed) accept identical input.
"""

import pytest
from pydantic import ValidationError

from app.types.api import _PROMPT_MAX, MediaRequest, PromptRequest, Selection

# A realistic detailed brief (the case that regressed): comfortably over the
# old 500 cap, comfortably under the new one.
_LONG_PROMPT = "Create a clear, visually engaging explainer about " + ("token prediction " * 40)


def test_long_prompt_is_accepted_by_both_dtos() -> None:
    assert 500 < len(_LONG_PROMPT) <= _PROMPT_MAX
    assert PromptRequest(prompt=_LONG_PROMPT).prompt == _LONG_PROMPT
    assert MediaRequest(prompt=_LONG_PROMPT).prompt == _LONG_PROMPT


@pytest.mark.parametrize("dto", [PromptRequest, MediaRequest])
def test_prompt_bounds_are_enforced(dto) -> None:
    dto(prompt="abcd")  # exactly the 4-char minimum is fine
    with pytest.raises(ValidationError):
        dto(prompt="ab")  # too short
    with pytest.raises(ValidationError):
        dto(prompt="x" * (_PROMPT_MAX + 1))  # over the cap


def test_selection_defaults_to_simplest_path() -> None:
    """Omitting `selection` yields the fewest-keys default (Replicate + OpenAI),
    with `model=None` meaning 'use the catalog default for that vendor'."""
    sel = MediaRequest(prompt="seed prompt").selection
    assert sel == Selection()
    assert (
        sel.chat.vendor,
        sel.image.vendor,
        sel.video.vendor,
        sel.tts.vendor,
        sel.music.vendor,
    ) == ("openai", "replicate", "replicate", "openai", "replicate")
    assert all(c.model is None for c in (sel.chat, sel.image, sel.video, sel.tts, sel.music))


def test_selection_accepts_explicit_vendor_and_model() -> None:
    req = MediaRequest(
        prompt="seed prompt",
        selection={
            "video": {"vendor": "runway", "model": "gen4_turbo"},
        },
    )
    assert req.selection.video.vendor == "runway"
    assert req.selection.video.model == "gen4_turbo"
    # Unspecified slots still fall back to the simplest-path defaults.
    assert req.selection.image.vendor == "replicate"
