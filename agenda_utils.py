# agenda_utils.py
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
import requests
from icalendar import Calendar

BRUSSELS = ZoneInfo("Europe/Brussels")

def _ensure_aware(dt):
    """Force datetime to Europe/Brussels timezone and convert date -> datetime."""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return datetime.combine(dt, time(0, 0), tzinfo=BRUSSELS)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BRUSSELS)
    return dt.astimezone(BRUSSELS)

def get_first_and_last_class(ics_url: str, target_date: date = None):
    """
    Fetch ICS calendar and return (first_start, last_end) for the target date
    in Europe/Brussels timezone.

    If no events on that day, returns (False, False).
    """
    if target_date is None:
        target_date = datetime.now(BRUSSELS).date()

    try:
        resp = requests.get(ics_url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch ICS: {e}")

    cal = Calendar.from_ical(resp.content)

    day_start = datetime.combine(target_date, time.min).replace(tzinfo=BRUSSELS)
    day_end   = datetime.combine(target_date, time.max).replace(tzinfo=BRUSSELS)

    starts, ends = [], []
    for comp in cal.walk('VEVENT'):
        if comp.get('STATUS', '').upper() == 'CANCELLED':
            continue

        dtstart = comp.decoded('DTSTART')
        dtend = comp.decoded('DTEND') or comp.decoded('DTSTART')

        dtstart = _ensure_aware(dtstart)
        dtend   = _ensure_aware(dtend)

        # Keep events overlapping the target day
        if dtstart <= day_end and dtend >= day_start:
            starts.append(max(dtstart, day_start))
            ends.append(min(dtend, day_end))

    if not starts or not ends:
        return False, False

    return min(starts), max(ends)
