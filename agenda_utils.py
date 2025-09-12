from datetime import datetime, date, time, timedelta
import logging
import time as time_module
import requests
from icalendar import Calendar
from tzlocal import get_localzone

LOCAL_TZ = get_localzone()

def _ensure_aware(dt):
    """Force datetime to local timezone and convert date -> datetime."""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return datetime.combine(dt, time(0, 0), tzinfo=LOCAL_TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)

def get_first_and_last_class(ics_url: str, target_date: date = None, retries: int = 3):
    """
    Fetch ICS calendar (with retries) and return (first_start, last_end) for the target date
    in local timezone.

    If no events on that day, returns (False, False).
    """
    if target_date is None:
        target_date = datetime.now(LOCAL_TZ).date()

    resp = None
    for attempt in range(retries):
        try:
            logging.info(f"[agenda] ICS fetch attempt {attempt + 1}/{retries}: {ics_url}")
            resp = requests.get(ics_url, timeout=30)
            resp.raise_for_status()
            break  # Success, exit retry loop
        except requests.RequestException as e:
            logging.warning(f"[agenda] ICS fetch failed ({attempt + 1}/{retries}): {e}")
            if attempt == retries - 1:
                raise
            time_module.sleep(1)

    cal = Calendar.from_ical(resp.content)

    day_start = datetime.combine(target_date, time.min).replace(tzinfo=LOCAL_TZ)
    day_end   = datetime.combine(target_date, time.max).replace(tzinfo=LOCAL_TZ)

    starts, ends = [], []
    for comp in cal.walk('VEVENT'):
        if comp.get('STATUS', '').upper() == 'CANCELLED':
            continue

        dtstart = comp.decoded('DTSTART')
        dtend   = comp.decoded('DTEND') or comp.decoded('DTSTART')

        dtstart = _ensure_aware(dtstart)
        dtend   = _ensure_aware(dtend)

        # Keep events overlapping the target day
        if dtstart <= day_end and dtend >= day_start:
            starts.append(max(dtstart, day_start))
            ends.append(min(dtend, day_end))

    if not starts or not ends:
        return False, False

    return min(starts), max(ends)
