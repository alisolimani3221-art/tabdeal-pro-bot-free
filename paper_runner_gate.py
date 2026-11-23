"""Fail-closed gate for the foreground paper-only runner.

The paper simulator has no exchange-order capability. A coordinator emergency
halt continues blocking prepared intents, but it must not disable an independent
read-only paper simulation. Runtime exclusivity and persisted-state integrity
remain mandatory.
"""


class PaperRunnerGateError(RuntimeError):
    """The paper runner is not safe to start."""


def ensure_paper_runner_ready(runtime_guard, coordinator_session):
    if runtime_guard is None or not getattr(runtime_guard, "held", False):
        raise PaperRunnerGateError("exclusive app runtime lock is not held")
    if coordinator_session is None:
        raise PaperRunnerGateError("persistent coordinator session is unavailable")

    # Require a readable, structurally valid coordinator snapshot. Its halted
    # flag protects prepared-intent workflows, not this local paper-only engine.
    # Do not clear or modify the coordinator halt here.
    snapshot = coordinator_session.snapshot()
    if not isinstance(snapshot, dict) or type(snapshot.get("halted")) is not bool:
        raise PaperRunnerGateError("persisted coordinator state is invalid")
    intents = snapshot.get("intents")
    if not isinstance(intents, dict):
        raise PaperRunnerGateError("persisted coordinator intents are invalid")
    return True
