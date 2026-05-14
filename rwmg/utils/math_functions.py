"""Reusable mathematical helpers used across the project.

Only two functions are required for the current codebase but they encapsulate
logic that is referenced from multiple modules.  The functions are intentionally
independent from the rest of the system to keep them easily testable.
"""

from __future__ import annotations

import math


# utils/math_functions.py
def nonlinear_trait_shift(current_value: float, delta: float) -> float:
    """Apply a bounded non-linear shift to a trait value.

    The raw trait values in the project are expected to fall within the
    ``[0, 1]`` interval.  When applying a shift we want large deltas to taper off
    near the extremes rather than clipping abruptly.  To achieve this we first
    add ``delta`` to ``current_value`` and then squash the result through a
    smooth ``tanh`` curve which asymptotically approaches ``0`` and ``1``.

    Parameters
    ----------
    current_value:
        The original trait value in the ``[0, 1]`` range.
    delta:
        The proposed change which may be positive or negative.

    Returns
    -------
    float
        The adjusted value constrained to ``[0, 1]`` with diminishing returns
        close to the boundaries.
    """

    # Shift the value and map it to the ``[-1, 1]`` domain for ``tanh``.
    shifted = (current_value + delta) * 2.0 - 1.0
    squashed = math.tanh(shifted)
    # Map back to ``[0, 1]`` and clamp for numerical safety.
    result = (squashed + 1.0) / 2.0
    return max(0.0, min(1.0, result))


def exponential_decay(value: float, time: int, half_life: int) -> float:
    """Return ``value`` after exponential decay over ``time`` units.

    ``half_life`` represents the number of time steps after which the value is
    expected to have halved.  The implementation follows the classic exponential
    decay formula ``value * 0.5 ** (time / half_life)``.  ``time`` and
    ``half_life`` are treated as non-negative; if ``half_life`` is zero the
    function degrades gracefully by returning ``0.0`` for any positive
    ``time``.

    Parameters
    ----------
    value:
        Initial value before decay.
    time:
        Number of time steps elapsed.  Negative values are treated as zero.
    half_life:
        The half-life period of the decay function.

    Returns
    -------
    float
        The decayed value.
    """

    if half_life <= 0:
        # With no half-life the value effectively drops to zero immediately
        # (except when no time has passed).
        return 0.0 if time > 0 else float(value)

    time = max(0, time)
    decay_factor = 0.5 ** (time / float(half_life))
    return float(value) * decay_factor

