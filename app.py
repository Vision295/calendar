from flask import Flask, render_template, jsonify, redirect, url_for, request, send_file
from questions import load_slots, save_slots, slots_exist
from ics import Calendar, Event
import datetime
import io
import os

app = Flask(__name__)

# -----------------------------
# Load events from ICS file
# -----------------------------
def load_ics_events(filename="original.ics"):
    events = []
    try:
        with open(filename, "r", encoding="utf-8") as f:
            c = Calendar(f.read())
            for e in c.events:
                events.append({
                    "title": e.name,
                    "start": e.begin.isoformat(),
                    "end": e.end.isoformat(),
                    "color": "#3788d8"
                })
    except FileNotFoundError:
        pass
    return events

# -----------------------------
# Algorithm to generate schedule
# -----------------------------
def generate_schedule(slots, existing_events=None):
    """
    Schedule slots considering:
    - Working hours 9AM-10PM, exclude 12-13
    - Max 8 hours/day including existing events from ICS
    - Max per day = consecutivehours
    - Allow sacrifice
    """
    events = []
    today = datetime.date.today()
    start_hour = 9
    end_hour = 22
    lunch_hour = 12
    daily_max = 8

    # Build occupied map
    occupied = {}  # {date: set(hours)}
    if existing_events:
        for e in existing_events:
            start = datetime.datetime.fromisoformat(e["start"])
            end = datetime.datetime.fromisoformat(e["end"])
            day = start.date()
            occupied.setdefault(day, set())
            h = start.hour
            while h < end.hour:
                if h != lunch_hour:
                    occupied[day].add(h)
                h += 1

    # Sort slots by priority
    sorted_slots = sorted(slots, key=lambda s: int(s.get("prioritylevel", 5)))

    for slot in sorted_slots:
        name = slot["name"]
        total_hours = int(slot.get("numhoursperweek", 1))
        max_per_day = int(slot.get("consecutivehours", 1))
        deadline = slot.get("deadline")
        deadline_date = datetime.date.fromisoformat(deadline) if deadline else today + datetime.timedelta(days=7)

        hours_remaining = total_hours
        current_day = today

        while hours_remaining > 0 and current_day <= deadline_date:
            occupied.setdefault(current_day, set())
            # available hours for the day
            available_hours = [h for h in range(start_hour, end_hour) if h != lunch_hour and h not in occupied[current_day]]
            max_today = min(max_per_day, len(available_hours), daily_max - len(occupied[current_day]))
            if max_today <= 0:
                current_day += datetime.timedelta(days=1)
                continue

            for h in available_hours[:max_today]:
                start_dt = datetime.datetime.combine(current_day, datetime.time(hour=h))
                end_dt = start_dt + datetime.timedelta(hours=1)
                events.append({
                    "title": name,
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "color": "#28a745"
                })
                occupied[current_day].add(h)
                hours_remaining -= 1
                if hours_remaining <= 0:
                    break

            current_day += datetime.timedelta(days=1)

    return events

# -----------------------------
# Routes
# -----------------------------
@app.route("/events-json")
def events_json():
    existing_events = load_ics_events()
    fc_events = existing_events.copy()

    slots = load_slots()
    if slots:
        fc_events.extend(generate_schedule(slots, existing_events))

    return jsonify(fc_events)

@app.route("/")
def home():
    if not slots_exist():
        return redirect(url_for("setup"))
    slots = load_slots()
    return render_template("index.html", slots=slots)

@app.route("/download_calendar")
def download_calendar():
    slots = load_slots()
    if not slots:
        return "No slots to export", 400

    c = Calendar()
    existing_events = load_ics_events()
    scheduled_events = generate_schedule(slots, existing_events)

    # Add ICS original events
    for ev in existing_events:
        e = Event()
        e.name = ev["title"]
        e.begin = datetime.datetime.fromisoformat(ev["start"])
        e.end = datetime.datetime.fromisoformat(ev["end"])
        c.events.add(e)

    # Add generated slots
    for ev in scheduled_events:
        e = Event()
        e.name = ev["title"]
        e.begin = datetime.datetime.fromisoformat(ev["start"])
        e.end = datetime.datetime.fromisoformat(ev["end"])
        c.events.add(e)

    file_stream = io.StringIO(str(c))
    return send_file(
        io.BytesIO(file_stream.getvalue().encode("utf-8")),
        as_attachment=True,
        download_name="full_schedule.ics",
        mimetype="text/calendar"
    )

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        names = request.form.getlist("name")
        hours = request.form.getlist("numhoursperweek")
        priorities = request.form.getlist("prioritylevel")
        deadlines = request.form.getlist("deadline")
        consecutive = request.form.getlist("consecutivehours")

        slots = []
        for i in range(len(names)):
            slots.append({
                "name": names[i],
                "numhoursperweek": hours[i],
                "prioritylevel": priorities[i],
                "deadline": deadlines[i],
                "consecutivehours": consecutive[i]
            })

        save_slots(slots)
        return redirect(url_for("home"))

    slots = load_slots() or []
    return render_template("setup.html", slots=slots)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT
    app.run(host="0.0.0.0", port=port, debug=True)
