import pytest

from work_hunter.hh_autopilot.state_machine import assert_transition, recovery_target
from work_hunter.hh_autopilot.types import (
    AutopilotState,
    DeliveryCertainty,
    STABLE_OUTCOME_CODES,
)


def test_possibly_sent_recovery_enters_reconciliation() -> None:
    assert (
        recovery_target(DeliveryCertainty.POSSIBLY_SENT)
        is AutopilotState.RECONCILING
    )


def test_ready_cannot_skip_directly_to_applied() -> None:
    with pytest.raises(ValueError, match="ready -> applied"):
        assert_transition(AutopilotState.READY, AutopilotState.APPLIED)


def test_late_ambiguity_can_be_classified() -> None:
    assert_transition(AutopilotState.MANUAL_CHALLENGE, AutopilotState.APPLIED)


def test_complete_stable_outcome_vocabulary_is_declared() -> None:
    assert {
        "ambiguous_remote_result",
        "challenge_expired",
        "challenge_dismissed",
        "pre_dispatch_network_error",
        "invalid_request",
        "authorization_state_mismatch",
    } <= STABLE_OUTCOME_CODES
