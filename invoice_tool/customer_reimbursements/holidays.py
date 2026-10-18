"""Legacy-compatible holidays helpers; explicit runtime dependencies."""

def observed_us_holidays(year, *, date, timedelta):
    def observed(day):
        if day.weekday() == 5:
            return day - timedelta(days=1)
        if day.weekday() == 6:
            return day + timedelta(days=1)
        return day

    def nth_weekday(month, weekday, nth):
        current = date(year, month, 1)
        while current.weekday() != weekday:
            current += timedelta(days=1)
        return current + timedelta(days=7 * (nth - 1))

    def last_weekday(month, weekday):
        current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        while current.weekday() != weekday:
            current -= timedelta(days=1)
        return current

    return {
        observed(date(year, 1, 1)),
        nth_weekday(1, 0, 3),
        nth_weekday(2, 0, 3),
        last_weekday(5, 0),
        observed(date(year, 6, 19)),
        observed(date(year, 7, 4)),
        nth_weekday(9, 0, 1),
        nth_weekday(10, 0, 2),
        observed(date(year, 11, 11)),
        nth_weekday(11, 3, 4),
        observed(date(year, 12, 25)),
    }
