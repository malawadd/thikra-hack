"""Offline conformance sweep for the provider catalog.

For EVERY (slot, vendor) entry: the factory constructs a real provider with no
network, and its curated `default_model` validates against the provider's model
family (when it declares families — Replicate/LMNT match any slug, so they're
exempt). This is the cheap guard that a default slug isn't a typo, run on every
CI pass without touching a vendor API.
"""

import os

import pytest

os.environ.setdefault("B2_BUCKET_NAME", "_")
os.environ.setdefault("B2_REGION", "us-west-004")
os.environ.setdefault("B2_KEY_ID", "_")
os.environ.setdefault("B2_APPLICATION_KEY", "_")

from genblaze_core.providers.base import BaseProvider

from app.repo import provider_catalog as pc

# (slot, vendor, entry) for every catalog cell — drives parametrization.
_ALL = [(slot, e.vendor, e) for slot, entries in pc.CATALOG.items() for e in entries.values()]
# Only the entries that actually construct a provider (everything but chat).
_PROVIDER_ENTRIES = [(s, v, e) for (s, v, e) in _ALL if e.make is not None]


def _ids(rows):
    return [f"{s}:{v}" for (s, v, _e) in rows]


def test_catalog_covers_every_slot() -> None:
    assert set(pc.CATALOG) == {pc.CHAT, pc.IMAGE, pc.VIDEO, pc.TTS, pc.MUSIC}
    # Every slot has at least one vendor.
    assert all(pc.CATALOG[slot] for slot in pc.CATALOG)


@pytest.mark.parametrize(("slot", "vendor", "entry"), _PROVIDER_ENTRIES, ids=_ids(_PROVIDER_ENTRIES))
def test_entry_constructs_a_provider(slot, vendor, entry) -> None:
    """`make()` builds a BaseProvider with no network (keys default to empty)."""
    prov = entry.make()
    assert isinstance(prov, BaseProvider)
    # The entry declares the genblaze Modality used for `.step()`.
    assert entry.modality is not None


@pytest.mark.parametrize(("slot", "vendor", "entry"), _PROVIDER_ENTRIES, ids=_ids(_PROVIDER_ENTRIES))
def test_default_model_is_valid_for_its_family(slot, vendor, entry) -> None:
    """For providers that declare model families, the curated `default_model`
    must match one (offline). Replicate/LMNT declare no families (they accept
    any slug), so there's nothing to assert for them."""
    reg = entry.make().models
    if not getattr(reg, "_provider_families", ()):
        pytest.skip(f"{vendor} declares no model families — any slug is accepted")
    assert reg.match_family(entry.default_model) is not None, (
        f"{slot}:{vendor} default_model {entry.default_model!r} matches no family"
    )


def test_only_external_inputs_or_image_kwarg_handoff() -> None:
    """Video entries declare a known handoff; non-video entries declare none."""
    for slot, entries in pc.CATALOG.items():
        for e in entries.values():
            if slot == pc.VIDEO:
                assert e.image_handoff in {"external_inputs", "image_kwarg"}
            else:
                assert e.image_handoff is None


def test_resolve_unknown_vendor_raises() -> None:
    with pytest.raises(ValueError, match="no provider"):
        pc.resolve(pc.IMAGE, "nope")


def test_matrix_shape_and_key_available_is_bool() -> None:
    m = pc.matrix()
    assert set(m) == set(pc.CATALOG)
    for _slot, rows in m.items():
        for row in rows:
            assert {"vendor", "default_model", "suggested_models", "modality",
                    "key_available"} <= row.keys()
            assert isinstance(row["key_available"], bool)
            assert isinstance(row["suggested_models"], list)
