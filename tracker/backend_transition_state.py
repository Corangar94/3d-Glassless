"""Thread-local coordination between tracker backend switches and pose filters."""
from __future__ import annotations

import threading


_STATE = threading.local()


def current_backend_transition_generation() -> int:
    """Return the transition generation for the current tracking thread."""
    return int(getattr(_STATE, "generation", 0))


def mark_backend_transition() -> int:
    """Advance and return the current thread's backend transition generation."""
    generation = current_backend_transition_generation() + 1
    _STATE.generation = generation
    return generation


def reset_backend_transition_generation() -> None:
    """Clear thread-local state for a new process/test lifecycle."""
    _STATE.generation = 0
