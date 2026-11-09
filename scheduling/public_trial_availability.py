"""Public trial date discovery using one shared inventory snapshot per request."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from .appointments import slot_data, validate_day
from .lessons import SlotCalendar, local_datetime, plan_dates


class TrialAvailability:
    def __init__(self, offer):
        self.offer = offer
        self.zone = ZoneInfo(offer.location.timezone_name)
        now = timezone.now()
        self.threshold = now + timedelta(hours=offer.notice_hours)
        self.first_day = self.threshold.astimezone(self.zone).date()
        self.last_day = now.astimezone(self.zone).date() + timedelta(days=offer.horizon_days)
        self.data = slot_data(offer)
        self.calendar = None
        self._rows = {}

    def slots(self, day):
        validate_day(self.offer, day)
        if day < self.first_day:
            return []
        if day in self._rows:
            return self._rows[day]
        if self.calendar is None:
            self.calendar = SlotCalendar(self.data,
                datetime.combine(self.first_day, time.min, tzinfo=self.zone),
                datetime.combine(self.last_day + timedelta(days=1), time.min, tzinfo=self.zone))
        result = []
        windows = [window for window in self.calendar.windows if window.weekday == day.weekday()]
        if windows and not any(c.first_date <= day <= c.last_date for c in self.calendar.closures):
            for minute in range(0, 1440, self.offer.interval_minutes):
                wall = datetime.combine(day, time.min) + timedelta(minutes=minute)
                if not any(w.start_time <= wall.time() < w.end_time for w in windows):
                    continue
                try:
                    if local_datetime(day, wall.time(), self.zone) < self.threshold:
                        continue
                    row = plan_dates(self.data, [wall], calendar=self.calendar)[0]
                except ValidationError:
                    continue
                if row["state"] == "scheduled":
                    result.append(row)
        self._rows[day] = result
        return result

    def available_dates(self, limit=7):
        dates = []
        day = self.first_day
        while day <= self.last_day and len(dates) < limit:
            if self.slots(day):
                dates.append(day)
            day += timedelta(days=1)
        return dates
