"""Fail-closed gate for the foreground paper runner.

This module only checks local runtime and persisted coordinator readiness.
It has no exchange adapter and cannot submit, cancel, or modify orders.
"""


class PaperRunnerGateError(RuntimeError):
    """The paper runner is not safe to start."""


def ensure_paper_runner_ready(runtime_guard, coordinator_session):
    if runtime_guard is None or not getattr(runtime_guard, "held", False):
        raise PaperRunnerGateError("exclusive app runtime lock is not held")
    if coordinator_session is None:
        raise PaperRunnerGateError("persistent coordinator session is unavailable")
    snapshot = coordinator_session.snapshot()
    if not isinstance(snapshot, dict) or type(snapshot.get("halted")) is not bool:
        raise PaperRunnerGateError("persisted coordinator state is invalid")
    if snapshot["halted"]:
        raise PaperRunnerGateError("persistent emergency stop is active")
    intents = snapshot.get("intents")
    if not isinstance(intents, dict):
        raise PaperRunnerGateError("persisted coordinator intents are invalid")
    return True
