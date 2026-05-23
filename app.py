"""
Weekly Schedule Sender — standalone Flask service.
Runs on Railway. No receipt processing, no Drive, no Amazon.

Env vars required:
  GREEN_INSTANCE_ID   — Green API instance ID
  GREEN_TOKEN         — Green API token
  SCHEDULE_GROUP_ID   — WhatsApp group ID (default: הקיץ של אביה)
  PORT                — (set by Railway automatically)
"""

import logging
import os
import threading
import time

import schedule_sender
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

app = Flask(__name__)

INSTANCE_ID    = os.environ["GREEN_INSTANCE_ID"]
INSTANCE_TOKEN = os.environ["GREEN_TOKEN"]
SCHEDULE_GROUP = os.environ.get("SCHEDULE_GROUP_ID", "120363042003803655@g.us")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    with schedule_sender._poll_lock:
        state = {k: str(v) for k, v in schedule_sender._poll_state.items()}
    return jsonify({"status": "ok", "poll_state": state})


@app.route("/send-polls-now", methods=["POST"])
def send_polls_now():
    """Send hour-selection polls immediately."""
    schedule_sender.send_polls(INSTANCE_ID, INSTANCE_TOKEN, SCHEDULE_GROUP)
    return jsonify({"status": "ok"})


@app.route("/send-schedule-now", methods=["POST"])
def send_schedule_now():
    """Read poll results and send the schedule.
    ?force=true  — resend even if already sent this week.
    Body: {"hours": [8.5, 8.5, 8.5, 5.5, null]}  — per-day override.
    """
    force = request.args.get("force", "").lower() == "true"
    body  = request.get_json(silent=True) or {}
    per_day_hours = body.get("hours")

    if force:
        with schedule_sender._poll_lock:
            schedule_sender._poll_state["schedule_sent"] = False

    ok = schedule_sender.finalize_and_send(
        INSTANCE_ID, INSTANCE_TOKEN, SCHEDULE_GROUP,
        per_day_hours=per_day_hours,
    )
    return jsonify({"status": "ok" if ok else "error"})


# ── Background schedule loop ───────────────────────────────────────────────────

def _safe_loop(fn, name):
    while True:
        try:
            fn()
        except Exception as e:
            logging.error(f"[{name}] crashed, restarting in 10s: {e}")
            time.sleep(10)


def main():
    logging.info(f"Schedule sender starting → group {SCHEDULE_GROUP}")

    t = threading.Thread(
        target=_safe_loop,
        args=(
            lambda: schedule_sender.schedule_loop(INSTANCE_ID, INSTANCE_TOKEN, SCHEDULE_GROUP),
            "schedule",
        ),
        daemon=True,
        name="schedule",
    )
    t.start()

    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Flask on port {port}")
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
