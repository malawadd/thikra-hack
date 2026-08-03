"""Studio provider-result classification tests."""

from types import SimpleNamespace

import pytest

from app.repo.studio_runtime import result_assets


def test_empty_failed_provider_result_is_not_reported_as_success() -> None:
    result = SimpleNamespace(
        run=SimpleNamespace(
            status="failed",
            steps=[
                SimpleNamespace(
                    assets=[],
                    error="Invalid seconds=5. Must be one of {8, 4, 12}",
                )
            ],
        )
    )
    with pytest.raises(RuntimeError, match="Invalid seconds=5"):
        result_assets(result)
