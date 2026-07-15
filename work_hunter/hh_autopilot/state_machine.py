from .types import AutopilotState, DeliveryCertainty


ALLOWED_TRANSITIONS = {
    AutopilotState.DISCOVERED: {
        AutopilotState.ELIGIBLE,
        AutopilotState.SKIPPED,
    },
    AutopilotState.ELIGIBLE: {
        AutopilotState.RANKED,
        AutopilotState.RETRY_WAIT,
    },
    AutopilotState.RANKED: {
        AutopilotState.READY,
        AutopilotState.SKIPPED,
        AutopilotState.RETRY_WAIT,
    },
    AutopilotState.READY: {AutopilotState.APPLYING},
    AutopilotState.APPLYING: {
        AutopilotState.APPLIED,
        AutopilotState.SKIPPED,
        AutopilotState.RETRY_WAIT,
        AutopilotState.MANUAL_CHALLENGE,
        AutopilotState.RECONCILING,
        AutopilotState.DEAD,
    },
    AutopilotState.RECONCILING: {
        AutopilotState.APPLIED,
        AutopilotState.SKIPPED,
        AutopilotState.RETRY_WAIT,
        AutopilotState.MANUAL_CHALLENGE,
        AutopilotState.DEAD,
    },
    AutopilotState.RETRY_WAIT: {
        AutopilotState.ELIGIBLE,
        AutopilotState.READY,
        AutopilotState.RECONCILING,
        AutopilotState.DEAD,
    },
    AutopilotState.MANUAL_CHALLENGE: {
        AutopilotState.READY,
        AutopilotState.RECONCILING,
        AutopilotState.APPLIED,
        AutopilotState.SKIPPED,
        AutopilotState.DEAD,
    },
    AutopilotState.APPLIED: set(),
    AutopilotState.SKIPPED: set(),
    AutopilotState.DEAD: set(),
}


def assert_transition(current: AutopilotState, target: AutopilotState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(
            f"Illegal HH autopilot transition: {current.value} -> {target.value}"
        )


def recovery_target(certainty: DeliveryCertainty) -> AutopilotState:
    if certainty is DeliveryCertainty.POSSIBLY_SENT:
        return AutopilotState.RECONCILING
    return AutopilotState.RETRY_WAIT
