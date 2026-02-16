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

                # Convert to python datetime
                start = e.begin.datetime
                end = e.end.datetime

                # Force remove timezone to avoid comparison issues
                if start.tzinfo is not None:
                    start = start.replace(tzinfo=None)
                if end.tzinfo is not None:
                    end = end.replace(tzinfo=None)

                events.append({
                    "title": e.name,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "color": "#3788d8"
                })

    except FileNotFoundError:
        pass

    return events


def generate_schedule(slots, existing_events=None):
    """
    Conflict-safe scheduling:
    - Working hours: 9:00–22:00
    - Exclude 12:00–13:00
    - Max 8h/day including ICS
    - No overlapping with ICS or generated events
    - numhoursperweek applies EVERY week until deadline
    """

    events = []
    today = datetime.date.today()
    start_hour = 9
    end_hour = 22
    lunch_start = 12
    lunch_end = 13
    daily_max = 8

    occupied_intervals = []

    # ---------------------------------
    # Load existing ICS events
    # ---------------------------------
    if existing_events:
        for e in existing_events:
            start = datetime.datetime.fromisoformat(e["start"])
            end = datetime.datetime.fromisoformat(e["end"])

            if start.tzinfo:
                start = start.replace(tzinfo=None)
            if end.tzinfo:
                end = end.replace(tzinfo=None)

            occupied_intervals.append((start, end))

    # ---------------------------------
    # Helpers
    # ---------------------------------
    def overlaps(start, end):
        for s, e in occupied_intervals:
            if start < e and s < end:
                return True
        return False

    def daily_hours(day):
        total = 0
        for s, e in occupied_intervals:
            if s.date() == day:
                total += (e - s).total_seconds() / 3600
        return total

    sorted_slots = sorted(slots, key=lambda s: int(s.get("prioritylevel", 5)))

    # ---------------------------------
    # Main scheduling loop
    # ---------------------------------
    for slot in sorted_slots:
        name = slot["name"]
        hours_per_week = int(slot.get("numhoursperweek", 1))
        max_per_day = int(slot.get("consecutivehours", 1))
        deadline = slot.get("deadline")

        deadline_date = (
            datetime.date.fromisoformat(deadline)
            if deadline else today + datetime.timedelta(days=7)
        )

        # ✅ Compute number of weeks until deadline
        total_days = (deadline_date - today).days + 1
        total_weeks = max(1, total_days // 7 + (1 if total_days % 7 else 0))

        total_hours = hours_per_week * total_weeks
        hours_remaining = total_hours

        current_day = today

        while hours_remaining > 0 and current_day <= deadline_date:

            already_today = daily_hours(current_day)

            if already_today >= daily_max:
                current_day += datetime.timedelta(days=1)
                continue

            hours_scheduled_today = 0

            for h in range(start_hour, end_hour):

                if lunch_start <= h < lunch_end:
                    continue

                if hours_scheduled_today >= max_per_day:
                    break

                if already_today >= daily_max:
                    break

                start_dt = datetime.datetime.combine(
                    current_day,
                    datetime.time(hour=h)
                )
                end_dt = start_dt + datetime.timedelta(hours=1)

                if overlaps(start_dt, end_dt):
                    continue

                events.append({
                    "title": name,
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "color": "#28a745"
                })

                occupied_intervals.append((start_dt, end_dt))

                hours_remaining -= 1
                hours_scheduled_today += 1
                already_today += 1

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

        for name, hour, priority, deadline, consec in zip(
            names, hours, priorities, deadlines, consecutive
        ):
            slots.append({
                "name": name,
                "numhoursperweek": hour,
                "prioritylevel": priority,
                "deadline": deadline,
                "consecutivehours": consec or 1
            })

        save_slots(slots)
        return redirect(url_for("home"))

    # ✅ THIS MUST EXIST
    slots = load_slots() or []
    return render_template("setup.html", slots=slots)


#     if request.method == "POST":
#         names = request.form.getlist("name")
#         hours = request.form.getlist("numhoursperweek")
#         priorities = request.form.getlist("prioritylevel")
#         deadlines = request.form.getlist("deadline")
#         consecutive = request.form.getlist("consecutivehours")

#         slots = []
#         for i in range(len(names)):
#             slots.append({
#                 "name": names[i],
#                 "numhoursperweek": hours[i],
#                 "prioritylevel": priorities[i],
#                 "deadline": deadlines[i],
#                 "consecutivehours": consecutive[i]
#             })

#         save_slots(slots)
#         return redirect(url_for("home"))

#     slots = load_slots() or []
#     return render_template("setup.html", slots=slots)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT
    app.run(host="0.0.0.0", port=port, debug=True)
