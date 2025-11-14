from __future__ import print_function
from datetime import datetime, timedelta
import os, json
import speech_recognition as sr
import pyttsx3
from dateutil import parser
import tzlocal

# Google Calendar API
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# AI
import google.generativeai as genai

# -------- CONFIGURATION --------
SCOPES = ['https://www.googleapis.com/auth/calendar']
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# -------- SPEECH UTILITIES --------
engine = pyttsx3.init('sapi5')  # Windows Text-to-Speech
engine.setProperty('rate', 170)  # Speech rate

import time

def speak(text):
    """Speak text aloud and print it to console."""
    print("🗨️", text)
    try:
        engine = pyttsx3.init()  # Re-initialize engine each call to avoid Windows TTS issues
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"⚠️ Speech engine error: {e}")

def listen():
    """Listen from microphone and return text."""
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("\n🎤 Listening...")
        r.adjust_for_ambient_noise(source)
        audio = r.listen(source)
    try:
        text = r.recognize_google(audio)
        print(f"🗣️ You said: {text}")
        return text
    except sr.UnknownValueError:
        speak("Sorry, I didn't catch that. Could you repeat?")
        return listen()
    except sr.RequestError:
        speak("Speech recognition service is unavailable.")
        return ""

# -------- GOOGLE CALENDAR LOGIN --------
def get_calendar_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    service = build('calendar', 'v3', credentials=creds)
    return service

# -------- AI EVENT EXTRACTION --------
def extract_event_details(text):
    model = genai.GenerativeModel("gemini-2.5-flash")
    now = datetime.now().isoformat()
    prompt = f"""
    Today is {now}.
    Extract calendar event details from this message.
    Return only JSON in this format:
    {{
      "title": "Event title",
      "start": "YYYY-MM-DDTHH:MM:SS",
      "end": "YYYY-MM-DDTHH:MM:SS",
      "location": "Location name"
    }}
    Message: {text}
    """
    response = model.generate_content(prompt)
    cleaned = response.text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").replace("json", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        speak("⚠️ Couldn't parse AI output.")
        print("Raw output:", response.text)
        return {}

# -------- FILL MISSING FIELDS --------
def fill_missing_fields(event, service):
    """Ask for missing fields and handle cancellations."""
    now = datetime.now()

    # Ask for missing title
    if not event.get("title"):
        speak("What should I call this event?")
        title = listen()
        if title.lower() in ["cancel", "nevermind"]:
            speak("Event creation has been cancelled.")
            return None
        event["title"] = title

    # Ask for missing start time
    if not event.get("start"):
        speak("When does it start?")
        start = listen()
        if start.lower() in ["cancel", "nevermind"]:
            speak("Event creation has been cancelled.")
            return None
        event["start"] = parse_natural_time(start, now)

    # Ask for missing end time
    if not event.get("end"):
        speak("When does it end?")
        end = listen()
        if end.lower() in ["cancel", "nevermind"]:
            speak("Event creation has been cancelled.")
            return None
        event["end"] = parse_natural_time(end, now)

    # Ask for missing location
    if not event.get("location"):
        speak("Where is it happening?")
        location = listen()
        if location.lower() in ["cancel", "nevermind"]:
            speak("Event creation has been cancelled.")
            return None
        event["location"] = location

    # Check for conflicts immediately after getting times
    new_start, new_end, _ = check_for_conflicts(
        service, event["start"], event["end"]
    )
    event["start"] = new_start
    event["end"] = new_end

    return event

# -------- PARSE TIME --------
def parse_natural_time(text, base_time=None):
    if not text:
        return None
    if base_time is None:
        base_time = datetime.now()
    try:
        dt = parser.parse(text, default=base_time)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception as e:
        speak(f"⚠️ Couldn't parse time '{text}'")
        print(f"Error: {e}")
        return base_time.strftime("%Y-%m-%dT%H:%M:%S")

# -------- FORMAT DATETIME FOR SPEECH --------
def format_datetime_for_speech(dt_str):
    try:
        dt = parser.parse(dt_str)
        return dt.strftime("%A, %B %d at %-I:%M %p")
    except Exception:
        return dt_str

# -------- CHECK CONFLICTS --------
def check_for_conflicts(service, start_datetime, end_datetime):
    """
    Checks for calendar event conflicts and asks the user if they want to reschedule.
    Speaks events in a legible format like 'Dinner on 11/13/2025 6-7 PM'.
    """
    from datetime import timedelta
    from dateutil import parser
    import tzlocal

    def format_event_time_range(start_str, end_str):
        """Convert ISO datetimes into legible range like '11/13/2025 6-7 PM'."""
        try:
            start_dt = parser.parse(start_str)
            end_dt = parser.parse(end_str)
            date_part = start_dt.strftime("%m/%d/%Y")
            start_hour = start_dt.strftime("%-I")
            end_hour = end_dt.strftime("%-I %p")
            # If start and end are in different AM/PM, include AM/PM on start
            if start_dt.strftime("%p") != end_dt.strftime("%p"):
                start_hour += f" {start_dt.strftime('%p')}"
            return f"{date_part} {start_hour}-{end_hour}"
        except Exception:
            return f"{start_str} to {end_str}"

    try:
        tz = tzlocal.get_localzone()
        start = parser.parse(start_datetime)
        end = parser.parse(end_datetime)

        # Add timezone if missing
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)

        # Ensure valid time range
        if end <= start:
            end = start + timedelta(hours=1)

        time_min = start.isoformat()
        time_max = end.isoformat()

    except Exception as e:
        print(f"⚠️ Couldn't parse datetimes: {e}")
        speak("Sorry, I couldn’t understand the time you gave me.")
        return start_datetime, end_datetime, False

    try:
        events_result = (
            service.events()
            .list(
                calendarId='primary',
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            )
            .execute()
        )

        events = events_result.get('items', [])

        if events:
            speak("You already have the following event scheduled:")
            conflict_messages = []
            for event in events:
                event_title = event.get('summary', 'Untitled Event')
                event_start = event['start'].get('dateTime', event['start'].get('date', ''))
                event_end = event['end'].get('dateTime', event['end'].get('date', ''))
                msg = f"{event_title} on {format_event_time_range(event_start, event_end)}"
                conflict_messages.append(msg)
                speak(msg)  # speak each conflict individually

            # Ask if user wants to reschedule
            speak("Would you like to reschedule your new event?")
            response = listen().lower()

            if "reschedule" in response or "change" in response or "yes" in response:
                speak("What time would you like to move it to?")
                new_time_response = listen()
                try:
                    new_start = parser.parse(new_time_response, default=start)
                except Exception:
                    speak("I didn't understand that time. Keeping original time.")
                    return start_datetime, end_datetime, True

                new_end = new_start + timedelta(hours=1)
                return new_start.strftime("%Y-%m-%dT%H:%M:%S"), new_end.strftime("%Y-%m-%dT%H:%M:%S"), True

            else:
                speak("Okay, keeping the original time.")
                return start_datetime, end_datetime, True

        # No conflicts
        return start_datetime, end_datetime, False

    except Exception as e:
        print(f"⚠️ Error while checking for conflicts: {e}")
        speak("Sorry, there was a problem checking your calendar.")
        return start_datetime, end_datetime, False

# -------- ADD EVENT --------
def add_event_to_calendar(service, title, start_datetime, end_datetime, location=""):
    if title is None or start_datetime is None or end_datetime is None:
        speak("Event creation cancelled.")
        return

    start_datetime, end_datetime, _ = check_for_conflicts(service, start_datetime, end_datetime)
    try:
        tz = tzlocal.get_localzone_name()
    except Exception:
        tz = "UTC"

    event = {
        'summary': title,
        'location': location,
        'start': {'dateTime': start_datetime, 'timeZone': tz},
        'end': {'dateTime': end_datetime, 'timeZone': tz},
    }

    try:
        service.events().insert(calendarId='primary', body=event).execute()
        speak(f"Your event '{title}' has been added to the calendar.")
    except Exception as e:
        speak("There was a problem adding your event.")
        print(f"⚠️ Failed to add event: {e}")

# -------- RUN LOCAL VOICE-ONLY MODE --------
if __name__ == '__main__':
    service = get_calendar_service()
    speak("Tell me about your event.")
    user_input = listen()
    extracted = extract_event_details(user_input)
    final_event = fill_missing_fields(extracted, service)

    if final_event is None:
        speak("Event creation has been cancelled. Goodbye!")
    else:
        add_event_to_calendar(
            service,
            title=final_event["title"],
            start_datetime=final_event["start"],
            end_datetime=final_event["end"],
            location=final_event["location"]
        )
