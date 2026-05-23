"""
Weekly Work Schedule Sender — Cloud version

Flow (every Saturday, India time):
  12:00 → send two WhatsApp polls (weekday hours 1-8, Friday hours 1-8)
  15:00 → read poll results → send schedule to group

Timezone: Asia/Kolkata (IST, UTC+5:30, no DST)
Transport: Green API (no browser required)
"""

import json
import os
import random
import threading
import time
import logging
import requests
from datetime import date, datetime, timedelta, timezone

# ── Simple file-based state (replaces job_store DB dependency) ─────────────────
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule_state.json")

def get_state(key: str, default=None):
    try:
        return json.loads(open(_STATE_FILE).read()).get(key, default)
    except Exception:
        return default

def set_state(key: str, value) -> None:
    try:
        try:
            data = json.loads(open(_STATE_FILE).read())
        except Exception:
            data = {}
        data[key] = value
        with open(_STATE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logging.getLogger(__name__).warning(f"set_state failed: {e}")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    INDIA_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    INDIA_TZ = timezone(timedelta(hours=5, minutes=30))

POLL_HOUR     = 12   # send polls at noon IST
SEND_HOUR     = 15   # send schedule at 15:00 IST (3h after polls)
SEND_WEEKDAY  = 5    # Saturday (Monday=0 in Python)

DEFAULT_WEEKDAY_HOURS = float(os.environ.get("SCHEDULE_WEEKDAY_HOURS", "8.5"))
DEFAULT_FRIDAY_HOURS  = float(os.environ.get("SCHEDULE_FRIDAY_HOURS",  "6"))

# Allowed start times (08:00 / 08:15 / 08:30 only)
START_OPTIONS = ["08:00", "08:15", "08:30"]

# Poll options: 1–7 then 8.5 (a full day is 8.5h, not 8h)
POLL_OPTIONS = [{"optionName": str(i)} for i in range(1, 8)] + [{"optionName": "8.5"}]


# ── Holidays 2026 ──────────────────────────────────────────────────────────────
# type: "closed" | "short" | "local_off"
# url: Wikipedia link shown inline in the WhatsApp message (omit if none)
HOLIDAYS: dict[date, dict] = {
    date(2026,  1,  1): {"name": "New Year",                "type": "closed",   "url": "https://en.wikipedia.org/wiki/New_Year%27s_Day"},
    date(2026,  1, 15): {"name": "Makara Sankranti",         "type": "closed",   "url": "https://en.wikipedia.org/wiki/Makar_Sankranti"},
    date(2026,  1, 26): {"name": "Republic Day",             "type": "closed",   "url": "https://en.wikipedia.org/wiki/Republic_Day_(India)"},
    date(2026,  3,  3): {"name": "Purim",                    "type": "closed",   "url": "https://en.wikipedia.org/wiki/Purim"},
    date(2026,  3, 19): {"name": "Chandramana Ugadi",        "type": "closed",   "url": "https://en.wikipedia.org/wiki/Ugadi"},
    date(2026,  3, 31): {"name": "Pre Passover Eve",         "type": "closed",   "url": "https://en.wikipedia.org/wiki/Passover"},
    date(2026,  4,  1): {"name": "Passover Eve",             "type": "short",    "hours": 4.5, "url": "https://en.wikipedia.org/wiki/Passover"},
    date(2026,  4,  2): {"name": "Passover",                 "type": "closed",   "url": "https://en.wikipedia.org/wiki/Passover"},
    date(2026,  4,  3): {"name": "Passover / Good Friday",   "type": "closed",   "url": "https://en.wikipedia.org/wiki/Good_Friday"},
    date(2026,  4,  6): {"name": "Hol-Amoed Pesach",         "type": "short",    "hours": 7.0, "url": "https://en.wikipedia.org/wiki/Chol_HaMoed"},
    date(2026,  4,  7): {"name": "Passover Eve",             "type": "short",    "hours": 4.5, "url": "https://en.wikipedia.org/wiki/Passover"},
    date(2026,  4,  8): {"name": "Passover - Hag Sheni",     "type": "closed",   "url": "https://en.wikipedia.org/wiki/Passover"},
    date(2026,  4,  9): {"name": "Passover - Hag Sheni",     "type": "closed",   "url": "https://en.wikipedia.org/wiki/Passover"},
    date(2026,  4, 21): {"name": "Yom Hazikaron",            "type": "short",    "hours": 5.5, "url": "https://en.wikipedia.org/wiki/Yom_Hazikaron"},
    date(2026,  4, 22): {"name": "Independence Day",         "type": "closed",   "url": "https://en.wikipedia.org/wiki/Israeli_Independence_Day"},
    date(2026,  5,  1): {"name": "May Day",                  "type": "local_off","url": "https://en.wikipedia.org/wiki/International_Workers%27_Day"},
    date(2026,  5, 21): {"name": "Erev Shavuot",             "type": "short",    "hours": 5.5, "url": "https://en.wikipedia.org/wiki/Shavuot"},
    date(2026,  5, 22): {"name": "Shavuot",                  "type": "closed",   "url": "https://en.wikipedia.org/wiki/Shavuot"},
    date(2026,  7, 30): {"name": "School Year End",          "type": "closed"},
    date(2026,  8, 21): {"name": "Varamahalakshmi Vrata",    "type": "local_off","url": "https://en.wikipedia.org/wiki/Varalakshmi_Vratam"},
    date(2026,  9, 11): {"name": "Erev Rosh Hashana",        "type": "short",    "hours": 5.5, "url": "https://en.wikipedia.org/wiki/Rosh_Hashanah"},
    date(2026,  9, 14): {"name": "Varasiddhi Vinayaka Vrata","type": "closed",   "url": "https://en.wikipedia.org/wiki/Ganesha_Chaturthi"},
    date(2026,  9, 21): {"name": "Yom Kippur",               "type": "closed",   "url": "https://en.wikipedia.org/wiki/Yom_Kippur"},
    date(2026,  9, 25): {"name": "Erev Sukkot",              "type": "short",    "hours": 5.0, "url": "https://en.wikipedia.org/wiki/Sukkot"},
    date(2026,  9, 28): {"name": "Hol-Amoed Succoth",        "type": "short",    "hours": 7.0, "url": "https://en.wikipedia.org/wiki/Chol_HaMoed"},
    date(2026,  9, 29): {"name": "Hol-Amoed Succoth",        "type": "short",    "hours": 7.0, "url": "https://en.wikipedia.org/wiki/Chol_HaMoed"},
    date(2026,  9, 30): {"name": "Hol-Amoed Succoth",        "type": "short",    "hours": 7.0, "url": "https://en.wikipedia.org/wiki/Chol_HaMoed"},
    date(2026, 10,  1): {"name": "Succoth Eve",              "type": "short",    "hours": 5.5, "url": "https://en.wikipedia.org/wiki/Sukkot"},
    date(2026, 10,  2): {"name": "Succoth / Gandhi Jayanti", "type": "closed",   "url": "https://en.wikipedia.org/wiki/Gandhi_Jayanti"},
    date(2026, 10, 20): {"name": "Mahanavami & Ayudha Puja", "type": "closed",   "url": "https://en.wikipedia.org/wiki/Ayudha_Puja"},
    date(2026, 10, 21): {"name": "Vijayadasami",             "type": "closed",   "url": "https://en.wikipedia.org/wiki/Vijayadashami"},
    date(2026, 11, 10): {"name": "Deepavali",                "type": "closed",   "url": "https://en.wikipedia.org/wiki/Diwali"},
    date(2026, 11, 27): {"name": "Kanakadas Jayanthi",       "type": "local_off","url": "https://en.wikipedia.org/wiki/Kanakadasa"},
    date(2026, 12, 25): {"name": "Christmas",                "type": "closed",   "url": "https://en.wikipedia.org/wiki/Christmas"},
}


# ── Poll state ─────────────────────────────────────────────────────────────────

_poll_lock  = threading.Lock()
_poll_state: dict = {
    "weekday_id":    None,   # idMessage of the weekday-hours poll
    "friday_id":     None,   # idMessage of the friday-hours poll
    "sent_date":     None,   # date the polls were sent (date object)
    "schedule_sent": False,  # True once the schedule was sent for this week
}
_STATE_KEY = "schedule_sender"


def _load_persisted_state() -> None:
    saved = get_state(_STATE_KEY, {}) or {}
    with _poll_lock:
        _poll_state["weekday_id"] = saved.get("weekday_id")
        _poll_state["friday_id"] = saved.get("friday_id")
        sent_date = saved.get("sent_date")
        _poll_state["sent_date"] = date.fromisoformat(sent_date) if sent_date else None
        _poll_state["schedule_sent"] = bool(saved.get("schedule_sent", False))


def _persist_state() -> None:
    with _poll_lock:
        payload = {
            "weekday_id": _poll_state["weekday_id"],
            "friday_id": _poll_state["friday_id"],
            "sent_date": _poll_state["sent_date"].isoformat() if _poll_state["sent_date"] else None,
            "schedule_sent": _poll_state["schedule_sent"],
        }
    set_state(_STATE_KEY, payload)


# ── Schedule helpers ───────────────────────────────────────────────────────────

def _add_hours(time_str: str, hours: float) -> str:
    h, m = map(int, time_str.split(":"))
    total = h * 60 + m + int(hours * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _get_next_week_dates() -> list[date]:
    today = date.today()
    days_to_monday = (7 - today.weekday()) % 7 or 7
    monday = today + timedelta(days=days_to_monday)
    return [monday + timedelta(days=i) for i in range(5)]


def _day_info(d: date) -> dict:
    return HOLIDAYS.get(d, {"type": "work"})


def _random_start(_hours: float = 0) -> str:
    """Pick a random start time from the allowed window (08:00 / 08:15 / 08:30)."""
    return random.choice(START_OPTIONS)


def _generate_starts(
    dates: list[date],
    weekday_hours: float = DEFAULT_WEEKDAY_HOURS,
    friday_hours:  float = DEFAULT_FRIDAY_HOURS,
) -> list[str | None]:
    """Return a random start time per day, or None for closed days."""
    default_hours = [weekday_hours] * 4 + [friday_hours]
    starts = []
    for d, def_h in zip(dates, default_hours):
        info = _day_info(d)
        if info["type"] == "closed":
            starts.append(None)
        else:
            # Use holiday-specific hours if defined, else the week's default
            h = info.get("hours", def_h)
            starts.append(_random_start(h))
    return starts


def build_message(
    dates: list[date],
    starts: list[str | None],
    weekday_hours: float = DEFAULT_WEEKDAY_HOURS,
    friday_hours:  float = DEFAULT_FRIDAY_HOURS,
) -> str:
    day_names     = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    default_hours = [weekday_hours] * 4 + [friday_hours]
    lines         = ["*Hello everyone , The working times for the week:*", ""]
    shown_urls: set[str] = set()

    def holiday_ref(info: dict) -> str:
        name = info["name"]
        url  = info.get("url")
        if url and url not in shown_urls:
            shown_urls.add(url)
            return f"({name} {url})"
        return f"({name})"

    for day_name, d, start, def_h in zip(day_names, dates, starts, default_hours):
        info = _day_info(d)
        dd   = f"*{day_name} {d.day}/{d.month}*"

        if info["type"] == "closed":
            lines.append(f"{dd} - Closed {holiday_ref(info)}")
        elif info["type"] == "local_off":
            hours    = info.get("hours", def_h)
            end      = _add_hours(start, hours)
            name_url = f"{info['name']} {info['url']}" if info.get("url") else info["name"]
            lines.append(f"{dd} {start}-{end} (Local staff off - {name_url})")
        else:
            hours = info.get("hours", def_h)
            end   = _add_hours(start, hours)
            if info["type"] == "short":
                lines.append(f"{dd} {start}-{end} {holiday_ref(info)}")
            else:
                lines.append(f"{dd} {start}-{end}")

    lines += ["", "*Have a good week* \U0001f338\U0001f338"]
    return "\n".join(lines)


# ── Green API helpers ──────────────────────────────────────────────────────────

def _base(instance_id: str) -> str:
    return f"https://api.green-api.com/waInstance{instance_id}"


def _send_message(instance_id: str, token: str, chat_id: str, text: str) -> str | None:
    try:
        r = requests.post(
            f"{_base(instance_id)}/sendMessage/{token}",
            json={"chatId": chat_id, "message": text},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("idMessage")
    except Exception as e:
        log.error(f"sendMessage failed: {e}")
        return None


def _send_poll(instance_id: str, token: str, chat_id: str, question: str) -> str | None:
    try:
        r = requests.post(
            f"{_base(instance_id)}/sendPoll/{token}",
            json={
                "chatId": chat_id,
                "message": question,
                "options": POLL_OPTIONS,
                "multipleAnswers": False,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("idMessage")
    except Exception as e:
        log.error(f"sendPoll failed: {e}")
        return None


def _get_poll_winner(messages: list, poll_id: str) -> float | None:
    """
    Scan getChatHistory results for a poll by ID.
    Returns the float value of the most-voted option, or None if no votes.
    """
    for msg in messages:
        if msg.get("idMessage") != poll_id:
            continue
        poll_data = (
            msg.get("messageData", {}).get("pollMessageData") or
            msg.get("pollMessageData") or
            {}
        )
        votes = poll_data.get("votes", [])
        if not votes:
            continue
        best = max(votes, key=lambda v: len(v.get("optionVoters", [])))
        if best.get("optionVoters"):
            try:
                return float(best["optionName"])
            except (ValueError, TypeError):
                pass
    return None


def _fetch_history(instance_id: str, token: str, chat_id: str, count: int = 50) -> list:
    try:
        r = requests.post(
            f"{_base(instance_id)}/getChatHistory/{token}",
            json={"chatId": chat_id, "count": count},
            timeout=30,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        log.error(f"getChatHistory failed: {e}")
        return []


# ── Core actions ───────────────────────────────────────────────────────────────

def send_polls(instance_id: str, token: str, group_id: str) -> None:
    log.info("[schedule] Sending hour-selection polls...")

    weekday_id = _send_poll(
        instance_id, token, group_id,
        "⏰ כמה שעות עבודה השבוע הבא? (שני–חמישי)",
    )
    time.sleep(1)
    friday_id = _send_poll(
        instance_id, token, group_id,
        "⏰ כמה שעות עבודה ביום שישי הבא?",
    )

    with _poll_lock:
        _poll_state["weekday_id"]    = weekday_id
        _poll_state["friday_id"]     = friday_id
        _poll_state["sent_date"]     = date.today()
        _poll_state["schedule_sent"] = False
    _persist_state()

    log.info(f"[schedule] Polls sent — weekday={weekday_id}, friday={friday_id}")


def finalize_and_send(
    instance_id: str,
    token: str,
    group_id: str,
    per_day_hours: list | None = None,
) -> bool:
    """
    Read poll results from chat history and send the schedule.
    Falls back to env-var defaults if no votes were cast.

    per_day_hours: optional list of 5 values [mon, tue, wed, thu, fri].
      Each value is either a float (hours) or None (day off / closed).
      When provided, overrides poll results AND annual-schedule hours for
      regular work days — but "closed" holidays in HOLIDAYS still win.
    """
    with _poll_lock:
        weekday_id    = _poll_state["weekday_id"]
        friday_id     = _poll_state["friday_id"]
        already_sent  = _poll_state["schedule_sent"]

    if already_sent:
        log.info("[schedule] Schedule already sent for this week — skipping")
        return True

    dates = _get_next_week_dates()

    if per_day_hours:
        # Build message with full per-day control
        starts = _generate_starts(dates)   # start times sized to actual day hours
        msg    = _build_message_per_day(dates, starts, per_day_hours)
        log.info(f"[schedule] Using per-day override: {per_day_hours}")
    else:
        messages      = _fetch_history(instance_id, token, group_id, count=50)
        weekday_hours = _get_poll_winner(messages, weekday_id) if weekday_id else None
        friday_hours  = _get_poll_winner(messages, friday_id)  if friday_id  else None
        weekday_hours = weekday_hours or DEFAULT_WEEKDAY_HOURS
        friday_hours  = friday_hours  or DEFAULT_FRIDAY_HOURS
        log.info(f"[schedule] Hours resolved — weekday={weekday_hours}h, friday={friday_hours}h")
        starts = _generate_starts(dates, weekday_hours, friday_hours)
        msg    = build_message(dates, starts, weekday_hours, friday_hours)

    msg_id = _send_message(instance_id, token, group_id, msg)
    if msg_id:
        log.info(f"[schedule] Schedule sent (idMessage={msg_id})")
        with _poll_lock:
            _poll_state["schedule_sent"] = True
        _persist_state()
        return True

    log.error("[schedule] Failed to send schedule")
    return False


def _build_message_per_day(
    dates: list[date],
    starts: list[str | None],
    per_day_hours: list,
) -> str:
    """
    Build message where per_day_hours[i] is float hours or None (closed).
    Annual HOLIDAYS still override: closed holidays remain closed,
    short/local_off holidays keep their defined hours.
    """
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    lines     = ["*Hello everyone , The working times for the week:*", ""]
    shown_urls: set[str] = set()

    def holiday_ref(info: dict) -> str:
        name = info["name"]
        url  = info.get("url")
        if url and url not in shown_urls:
            shown_urls.add(url)
            return f"({name} {url})"
        return f"({name})"

    for i, (day_name, d) in enumerate(zip(day_names, dates)):
        info     = _day_info(d)
        dd       = f"*{day_name} {d.day}/{d.month}*"
        override = per_day_hours[i] if i < len(per_day_hours) else None

        if info["type"] == "closed":
            # Annual holiday always wins
            lines.append(f"{dd} - Closed {holiday_ref(info)}")
        elif info["type"] in ("short", "local_off") and "hours" in info:
            # Annual holiday has specific hours — use them, random start within window
            h     = info["hours"]
            start = _random_start(h)
            end   = _add_hours(start, h)
            if info["type"] == "short":
                lines.append(f"{dd} {start}-{end} {holiday_ref(info)}")
            else:
                name_url = f"{info['name']} {info['url']}" if info.get("url") else info["name"]
                lines.append(f"{dd} {start}-{end} (Local staff off - {name_url})")
        elif override is None:
            # Manual override: day off
            lines.append(f"{dd} - Closed")
        else:
            # Manual override: specific hours, random start within window
            h     = float(override)
            start = _random_start(h)
            end   = _add_hours(start, h)
            lines.append(f"{dd} {start}-{end}")

    lines += ["", "*Have a good week* \U0001f338\U0001f338"]
    return "\n".join(lines)


# ── Webhook handler (called from whatsapp_cloud.py) ───────────────────────────

def on_poll_vote(instance_id: str, token: str, group_id: str, poll_id: str) -> None:
    """
    Called when a poll vote webhook arrives for a message in our group.
    If the voted poll is one of ours AND both polls now have at least one vote,
    finalize immediately without waiting for the 15:00 timer.
    """
    with _poll_lock:
        our_ids   = {_poll_state["weekday_id"], _poll_state["friday_id"]}
        already   = _poll_state["schedule_sent"]
        sent_date = _poll_state["sent_date"]

    if already or poll_id not in our_ids or sent_date != date.today():
        return

    messages = _fetch_history(instance_id, token, group_id, count=20)

    with _poll_lock:
        w_id = _poll_state["weekday_id"]
        f_id = _poll_state["friday_id"]

    weekday_voted = bool(_get_poll_winner(messages, w_id)) if w_id else False
    friday_voted  = bool(_get_poll_winner(messages, f_id)) if f_id else False

    if weekday_voted and friday_voted:
        log.info("[schedule] Both polls answered — sending schedule now")
        threading.Thread(
            target=finalize_and_send,
            args=(instance_id, token, group_id),
            daemon=True,
        ).start()


# ── Main scheduling loop ───────────────────────────────────────────────────────

def schedule_loop(instance_id: str, token: str, group_id: str) -> None:
    _load_persisted_state()
    log.info(
        f"[schedule] Sender started — polls at {POLL_HOUR}:00 IST, "
        f"schedule at {SEND_HOUR}:00 IST every Saturday → {group_id}"
    )

    with _poll_lock:
        persisted_sent_date = _poll_state["sent_date"]
        persisted_sent = _poll_state["schedule_sent"]
    _last_poll_date: date | None = persisted_sent_date
    _last_send_date: date | None = persisted_sent_date if persisted_sent else None

    while True:
        try:
            now = datetime.now(INDIA_TZ)
            today = now.date()
            is_saturday = now.weekday() == SEND_WEEKDAY

            if is_saturday and now.hour == POLL_HOUR and _last_poll_date != today:
                send_polls(instance_id, token, group_id)
                _last_poll_date = today

            if is_saturday and now.hour >= SEND_HOUR and _last_send_date != today:
                finalize_and_send(instance_id, token, group_id)
                _last_send_date = today

        except Exception as e:
            log.error(f"[schedule] loop error: {e}")

        time.sleep(600)  # check every 10 minutes
