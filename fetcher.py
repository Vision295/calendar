from ics import Calendar
import datetime

def load_ics_events(filename="original.ics"):
    events = []
    with open(filename, "r", encoding="utf-8") as f:
        c = Calendar(f.read())
        for e in c.events:
            events.append({
                "title": e.name,
                "start": e.begin.isoformat(),
                "end": e.end.isoformat(),
                "color": "#3788d8"
            })
    return events
