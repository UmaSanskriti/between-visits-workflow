"""Calendar stand-in. Playbook section 4: four 30-minute slots in the next 10 business days, Boston time,
from Anirudh's calendar. The real calendar (HBS Outlook or Google, open item) replaces this module."""

from __future__ import annotations

from datetime import datetime

from .clock import add_business_days, to_local

SLOT_HOURS = [14, 10, 15, 11]


def stub_slots(now_utc: datetime, tz_name: str = "America/New_York", count: int = 4) -> list[dict]:
    local = to_local(now_utc, tz_name).replace(minute=0, second=0, microsecond=0)
    slots = []
    for i in range(count):
        day = add_business_days(local, 2 + 2 * i)  # every other business day, starting two days out
        slot = day.replace(hour=SLOT_HOURS[i % len(SLOT_HOURS)])
        label = slot.strftime("%a %-m/%-d %-I:%M%p").replace("AM", "am").replace("PM", "pm")
        slots.append({"iso": slot.isoformat(), "label": label})
    return slots
