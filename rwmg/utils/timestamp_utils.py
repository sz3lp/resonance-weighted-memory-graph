"""Timestamp utilities for the RWMG project.

This module centralises lightweight helpers for working with timestamps and
dates.  Only a small subset of functionality is required by the simulation,
so the implementations intentionally avoid additional dependencies in favour
of the standard library ``datetime`` module.
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

# utils/timestamp_utils.py
def get_current_iso_time() -> str:
    """Returns the current timestamp in ISO 8601 format.

    The timestamp is expressed in UTC and excludes microseconds to keep the
    output stable for logging and file naming purposes.
    """

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def calculate_age_in_days(birth_date: str) -> int:
    """Calculates the number of days since a given birth date.

    Parameters
    ----------
    birth_date:
        An ISO 8601 formatted date string. If no timezone information is
        provided, the date is assumed to be in UTC.

    Returns
    -------
    int
        The number of full days that have elapsed between ``birth_date`` and
        the current moment.  If ``birth_date`` cannot be parsed or lies in the
        future, ``0`` is returned.
    """

    try:
        birth_dt = datetime.fromisoformat(birth_date)
    except (TypeError, ValueError):
        return 0

    if birth_dt.tzinfo is None:
        birth_dt = birth_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = now - birth_dt
    return max(delta.days, 0)
