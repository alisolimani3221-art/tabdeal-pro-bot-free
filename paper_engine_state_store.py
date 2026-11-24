"""Durable atomic JSON persistence for the local paper-trading snapshot."""
import json
import os
import tempfile


class PaperStateStoreError(RuntimeError):
    """The paper engine snapshot could not be durably persisted."""


def atomic_write_json(path, state):
    """Write JSON to a private temp file, fsync it, replace, then fsync its directory.

    A directory-fsync failure is reported even though os.replace may already
    have happened: callers must halt rather than assume the rename is durable.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("paper state path is required")
    try:
        payload = json.dumps(
            state, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PaperStateStoreError("paper state is not valid finite JSON") from exc

    destination = os.path.abspath(path)
    directory = os.path.dirname(destination)
    temporary_path = None
    fd = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=".paper-engine-", suffix=".tmp", dir=directory
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(directory, flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception as exc:
        raise PaperStateStoreError("could not durably persist paper state") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
