import json
import os

DATA_FILE = "answers.json"


def save_slots(slots):
      """
      slots: list of dicts, each dict has keys:
          name, numhoursperweek, prioritylevel, deadline, consecutivehours
      """
      data = {
            "slots": slots
      }
      with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)


def load_slots():
      if not os.path.exists(DATA_FILE):
            return []
      with open(DATA_FILE, "r") as f:
            data = json.load(f)
      return data.get("slots", [])


def slots_exist():
      """
      True if file exists and at least one slot is saved
      """
      slots = load_slots()
      return len(slots) > 0
