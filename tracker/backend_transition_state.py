"""Thread-local coordination between tracker backend switches and pose filters."""
from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class BackendTransitionState:
    generation: int
    preserve_position: bool


_STATE = threading.local()


def current_backend_transition_state() -> BackendTransitionState:
    """Return the transition descriptor for the current tracking thread."""
    return BackendTransitionState(
        generation=int(getattr(_STATE, "generation", 0)),
        preserve_position=bool(
            getattr(_STATE, "preserve_position", False)
        ),
    )


def current_backend_transition_generation() -> int:
    """Compatibility helper returning only the current generation."""
    return current_backend_transition_state().generation


def mark_backend_transition(*, preserve_position: bool = True) -> int:
    """Advance the transition generation and record reset semantics."""
    generation = current_backend_transition_generation() + 1
    _STATE.generation = generation
    _STATE.preserve_position = bool(preserve_position)
    return generation


def reset_backend_transition_generation() -> None:
    """Clear thread-local state for a new process/test lifecycle."""
    _STATE.generation = 0
    _STATE.preserve_position = False
