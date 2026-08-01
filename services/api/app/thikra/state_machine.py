"""Authoritative generation-run transition policy."""

from app.thikra.models import GenerationRun

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"MANDATE_COMPILED", "CANCELLED"},
    "MANDATE_COMPILED": {"MANDATE_CONFIRMED", "CANCELLED"},
    "MANDATE_CONFIRMED": {"AUTHORIZATION_PENDING", "CANCELLED"},
    "AUTHORIZATION_PENDING": {"AUTHORIZED", "FAILED", "CANCELLED"},
    "AUTHORIZED": {"PROVIDER_SELECTED", "CANCELLED"},
    "PROVIDER_SELECTED": {"PAYMENT_INVOKED", "PLANNING", "CANCELLED"},
    "PAYMENT_INVOKED": {"PLANNING", "FAILED", "CANCELLED"},
    "PLANNING": {"GENERATING", "FAILED", "CANCELLED"},
    "GENERATING": {"COMPOSING", "VERIFYING", "FAILED", "CANCELLED"},
    "COMPOSING": {"VERIFYING", "FAILED", "CANCELLED"},
    "VERIFYING": {"HUMAN_REVIEW", "ACCEPTED", "RETRYING", "REJECTED", "FAILED"},
    "HUMAN_REVIEW": {"ACCEPTED", "RETRYING", "REJECTED", "REDRESS_OPEN"},
    "RETRYING": {"GENERATING", "VERIFYING", "FAILED", "CANCELLED"},
    "ACCEPTED": {"COMPLETED"},
    "REJECTED": {"REDRESS_OPEN", "COMPLETED"},
    "REDRESS_OPEN": {"COMPLETED"},
    "FAILED": {"RETRYING", "REDRESS_OPEN", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


class InvalidTransition(ValueError):
    pass


def transition(run: GenerationRun, target: str) -> None:
    allowed = TRANSITIONS.get(run.status, set())
    if target not in allowed:
        raise InvalidTransition(f"Cannot transition run from {run.status} to {target}")
    run.status = target
    run.current_stage = target.replace("_", " ").title()


def retry_allowed(run: GenerationRun, estimated_retry_minor: int) -> tuple[bool, str]:
    if run.retry_count >= run.maximum_retries:
        return False, "Maximum retry count reached"
    if run.spent_minor + estimated_retry_minor > run.authorized_minor:
        return False, "Retry would exceed the authorized amount"
    if run.spent_minor + estimated_retry_minor > run.budget_cap_minor:
        return False, "Retry would exceed the mandate budget"
    return True, "Retry is within policy"
