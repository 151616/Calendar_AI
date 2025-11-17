# server.py
from __future__ import print_function
from datetime import datetime, timedelta
import os
import json
from typing import Optional
from dateutil import parser
import tzlocal
from flask import Flask, request, jsonify
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import google.generativeai as genai

# ---- CONFIG ----
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
SCOPES = ["https://www.googleapis.com/auth/calendar"]

app = Flask(__name__)


# ---- HELPERS ----
def get_calendar_service():
    """
    Create a Google Calendar service using a service account JSON stored in env var.
    GOOGLE_SERVICE_ACCOUNT_JSON should contain the full JSON contents.
    """
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set in environment")

    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    service = build("calendar", "v3", credentials=creds)
    return service


def safe_parse(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return parser.parse(dt_str)
    except Exception:
        return None


def iso_format(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def human_readable(dt_str: str) -> str:
    """
    Convert an ISO-like datetime to a human-friendly string.
    Example: "Thursday, November 13 at 6:00 PM"
    """
    dt = safe_parse(dt_str)
    if not dt:
        return dt_str
    try:
        # Use platform-friendly format (avoid %-d on some platforms)
        return dt.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")
    except Exception:
        return dt_str


def format_range(start_iso: str, end_iso: str) -> str:
    s = safe_parse(start_iso)
    e = safe_parse(end_iso)
    if not s or not e:
        return f"{start_iso} to {end_iso}"

    date_part = s.strftime("%m/%d/%Y")

    # Cross-platform hour formatting
    start_t = s.strftime("%I:%M %p").lstrip("0")
    end_t = e.strftime("%I:%M %p").lstrip("0")

    return f"{date_part} {start_t} - {end_t}"

# ---- AI extraction wrapper ----
def extract_event_details_with_gemini(text: str) -> dict:
    """
    Uses Gemini to extract title/start/end/location as JSON.
    Returns a dict with those keys (may be empty strings).
    """
    model = genai.GenerativeModel("gemini-2.5-flash")
    now = datetime.now().isoformat()
    prompt = f"""
Today is {now}.
Extract calendar event details from this message.
Return only JSON in this exact format:
{{
  "title": "Event title",
  "start": "YYYY-MM-DDTHH:MM:SS",
  "end": "YYYY-MM-DDTHH:MM:SS",
  "location": "Location name"
}}

If any value is not present in the message, return an empty string for that field.
Message: {text}
"""
    resp = model.generate_content(prompt)
    cleaned = resp.text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").replace("json", "").strip()
    try:
        data = json.loads(cleaned)
        # Ensure keys exist
        return {
            "title": data.get("title", "") if isinstance(data, dict) else "",
            "start": data.get("start", "") if isinstance(data, dict) else "",
            "end": data.get("end", "") if isinstance(data, dict) else "",
            "location": data.get("location", "") if isinstance(data, dict) else "",
        }
    except Exception:
        # Best-effort fallback: return blanks
        return {"title": "", "start": "", "end": "", "location": ""}


# ---- CALENDAR FUNCTIONS ----
def check_conflicts(service, start_iso: str, end_iso: str):
    tz = tzlocal.get_localzone()
    start_dt = safe_parse(start_iso)
    end_dt = safe_parse(end_iso)
    if not start_dt or not end_dt:
        return []

    # Attach timezone if missing
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=tz)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=tz)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    items = events_result.get("items", [])
    conflicts = []
    for e in items:
        title = e.get("summary", "Untitled Event")
        s = e["start"].get("dateTime", e["start"].get("date", ""))
        e_t = e["end"].get("dateTime", e["end"].get("date", ""))
        conflicts.append({"title": title, "start": s, "end": e_t})
    return conflicts


def add_event_to_calendar(service, title: str, start_iso: str, end_iso: str, location: str = ""):
    tz_name = tzlocal.get_localzone_name()
    body = {
        "summary": title,
        "location": location,
        "start": {"dateTime": start_iso, "timeZone": tz_name},
        "end": {"dateTime": end_iso, "timeZone": tz_name},
    }
    created = service.events().insert(calendarId="primary", body=body).execute()
    return created


# ---- ROUTES (Option B) ----

@app.route("/extract", methods=["POST"])
def route_extract():
    """
    Input: { "text": "user spoken text here" }
    Output: { title, start, end, location, spoken_response }
    """
    payload = request.json or {}
    text = payload.get("text", "")
    if not text:
        return jsonify({"error": "No text provided", "spoken_response": "I didn't hear anything."}), 400

    extracted = extract_event_details_with_gemini(text)
    # Build a friendly spoken_response summarizing extraction
    title = extracted.get("title", "")
    start = extracted.get("start", "")
    end = extracted.get("end", "")
    location = extracted.get("location", "")

    parts = []
    if title:
        parts.append(f"Title: {title}")
    if start:
        parts.append(f"Start: {human_readable(start)}")
    if end:
        parts.append(f"End: {human_readable(end)}")
    if location:
        parts.append(f"Location: {location}")

    if parts:
        spoken = "I extracted the following — " + "; ".join(parts) + ". Is that correct?"
    else:
        spoken = "I couldn't find clear event details in that. What is the title, start, end, or location?"

    return jsonify({**extracted, "spoken_response": spoken})


@app.route("/check_conflicts", methods=["POST"])
def route_check_conflicts():
    """
    Input: { "start": "...", "end": "..." }
    Output: { conflicts: [...], spoken_response: "..." }
    """
    payload = request.json or {}
    start = payload.get("start")
    end = payload.get("end")
    if not start or not end:
        return jsonify({"error": "start and end required", "spoken_response": "I need both start and end times to check for conflicts."}), 400

    service = get_calendar_service()
    conflicts = check_conflicts(service, start, end)
    if not conflicts:
        spoken = f"No conflicts found for {human_readable(start)}."
    else:
        spoken_items = []
        for c in conflicts:
            spoken_items.append(f"{c['title']} on {format_range(c['start'], c['end'])}")
        spoken = "You already have the following event(s): " + "; ".join(spoken_items) + ". Would you like to reschedule the new event?"
    return jsonify({"conflicts": conflicts, "spoken_response": spoken})


@app.route("/add_event", methods=["POST"])
def route_add_event():
    """
    Input: { title, start, end, location (optional) }
    Output: { status, event_id, spoken_response }
    """
    payload = request.json or {}
    title = payload.get("title")
    start = payload.get("start")
    end = payload.get("end")
    location = payload.get("location", "")

    if not title or not start or not end:
        return jsonify({"error": "title, start, end required", "spoken_response": "I need title, start, and end to add the event."}), 400

    service = get_calendar_service()
    try:
        created = add_event_to_calendar(service, title, start, end, location)
        event_id = created.get("id")
        spoken = f"Added {title} on {format_range(start, end)}."
        return jsonify({"status": "added", "event_id": event_id, "spoken_response": spoken})
    except Exception as e:
        print("Error adding event:", e)
        return jsonify({"error": "failed_to_add", "spoken_response": "I couldn't add the event due to a server error."}), 500


# Health check
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
