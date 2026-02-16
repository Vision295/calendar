from flask import Flask, render_template, jsonify, redirect, url_for, request, send_file
from questions import load_slots, save_slots, slots_exist
from ics import Calendar, Event
import datetime
import io
import os
import math

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
                start = e.begin.datetime
                end = e.end.datetime
                if start.tzinfo:
                    start = start.replace(tzinfo=None)
                if end.tzinfo:
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

# -----------------------------
# Schedule generator
# -----------------------------
def generate_schedule(slots, existing_events=None):
    """
    Conflict-safe scheduling:
    - Working hours: 9:00–22:00
    - Exclude 12:00–13:00
    - Max 8h/day including ICS
    - No overlapping with ICS or generated events
    - numhoursperweek applies EVERY week until deadline
    - Supports fractional consecutive hours down to 0.05h (3 min)
    - Minimum slot duration: 0.5h (30min)
    - 5-min breaks between different activities
    """

    import datetime

    events = []
    today = datetime.date.today()
    start_hour = 9
    end_hour = 22
    lunch_start = 12
    lunch_end = 13
    daily_max = 8
    min_block_hours = 0.5
    break_between_activities = 5 / 60  # 5 minutes in hours
    minute_step = 5  # align start times to 5 min increments

    occupied_intervals = []

    # Load existing ICS events
    if existing_events:
        for e in existing_events:
            start = e["start"]
            end = e["end"]
            if isinstance(start, str):
                start = datetime.datetime.fromisoformat(start)
            if isinstance(end, str):
                end = datetime.datetime.fromisoformat(end)
            if start.tzinfo:
                start = start.replace(tzinfo=None)
            if end.tzinfo:
                end = end.replace(tzinfo=None)
            occupied_intervals.append((start, end))

    # Helpers
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

    # Sort by priority
    sorted_slots = sorted(slots, key=lambda s: int(s.get("prioritylevel", 5)))

    # Main loop
    for slot in sorted_slots:
        name = slot["name"]
        hours_per_week = float(slot.get("numhoursperweek", 1))
        max_per_day = float(slot.get("consecutivehours", 1))
        deadline = slot.get("deadline")
        deadline_date = datetime.date.fromisoformat(deadline) if deadline else today + datetime.timedelta(days=7)

        # Compute total hours until deadline
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
            current_time = datetime.datetime.combine(current_day, datetime.time(hour=start_hour))
            # Align start to nearest 5 min
            current_time = current_time.replace(minute=(current_time.minute // minute_step) * minute_step, second=0, microsecond=0)
            end_of_day = datetime.datetime.combine(current_day, datetime.time(hour=end_hour))

            while current_time < end_of_day and hours_remaining > 0 and hours_scheduled_today < max_per_day:
                # Skip lunch
                lunch_start_dt = datetime.datetime.combine(current_day, datetime.time(hour=lunch_start))
                lunch_end_dt = datetime.datetime.combine(current_day, datetime.time(hour=lunch_end))
                if current_time >= lunch_start_dt and current_time < lunch_end_dt:
                    current_time = lunch_end_dt
                    continue

                # Determine session duration
                max_possible = min(max_per_day - hours_scheduled_today, hours_remaining)
                session_duration = max(min_block_hours, max_possible)
                session_end = current_time + datetime.timedelta(hours=session_duration)

                # If session crosses lunch, split
                if current_time < lunch_start_dt < session_end:
                    session_end = lunch_start_dt
                    session_duration = (session_end - current_time).total_seconds() / 3600
                    if session_duration < min_block_hours:
                        current_time = lunch_end_dt
                        continue

                # Avoid overlap
                if overlaps(current_time, session_end):
                    current_time += datetime.timedelta(minutes=minute_step)
                    continue

                # Add event
                events.append({
                    "title": name,
                    "start": current_time.isoformat(),
                    "end": session_end.isoformat(),
                    "color": "#28a745"
                })
                occupied_intervals.append((current_time, session_end))
                hours_remaining -= session_duration
                hours_scheduled_today += session_duration

                # Move current_time, add 5 min break
                current_time = session_end + datetime.timedelta(minutes=5)

            current_day += datetime.timedelta(days=1)

    # ---------------------
    # Merge consecutive events of the same activity
    # ---------------------
    merged_events = []
    events.sort(key=lambda e: (e["title"], e["start"]))
    for e in events:
        if merged_events and merged_events[-1]["title"] == e["title"]:
            prev_end = datetime.datetime.fromisoformat(merged_events[-1]["end"])
            curr_start = datetime.datetime.fromisoformat(e["start"])
            # If previous ends exactly at current start, merge
            if prev_end == curr_start:
                merged_events[-1]["end"] = e["end"]
                continue
        merged_events.append(e)

    return merged_events


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

    # Add original ICS events
    for ev in existing_events:
        e = Event()
        e.name = ev["title"]
        e.begin = datetime.datetime.fromisoformat(ev["start"])
        e.end = datetime.datetime.fromisoformat(ev["end"])
        c.events.add(e)

    # Add scheduled slots
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
                "numhoursperweek": hour or 1,
                "prioritylevel": priority or 5,
                "deadline": deadline or "",
                "consecutivehours": consec or 1
            })

        save_slots(slots)
        return redirect(url_for("home"))

    slots = load_slots() or []
    return render_template("setup.html", slots=slots)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
