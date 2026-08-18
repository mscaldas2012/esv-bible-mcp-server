"""Chat web app for talking to the ESV Bible agent (text + audio).

Run with:  uv run webapp.py

Rides your Claude.ai subscription via the `claude` CLI (see agent.py) rather
than spending Developer Platform API credits — make sure ANTHROPIC_API_KEY
is NOT set in the environment this runs in, or it'll be used instead.

Stateless-per-request design, same shape as the repo-root webapp.py: each
request opens a fresh ClaudeSDKClient (inside agent.reply), resuming the
prior turn via the Claude session_id carried in Flask's signed-cookie
session rather than keeping a client connected for the server's lifetime.
"""

import asyncio
import os

from flask import Flask, jsonify, render_template, request, session

import agent

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

# Populated in __main__ below, at process startup — printed to the console
# and also handed to the frontend so the log pane shows it before any turn.
STARTUP_LOG: list[str] = []


@app.route("/")
def index():
    return render_template(
        "index.html", startup_log=STARTUP_LOG, slash_commands=agent.SLASH_COMMAND_INFO
    )


@app.route("/api/message", methods=["POST"])
def message():
    body = request.get_json(silent=True) or {}
    text = (body.get("message") or "").strip()
    if not text:
        return jsonify({"type": "error", "message": "Type a message, e.g. \"explain Matthew 5\"."}), 400

    resume_session_id = session.get("claude_session_id")

    try:
        if text.startswith("/"):
            command, _, argument = text[1:].partition(" ")
            result = asyncio.run(agent.run_command(command.lower(), argument, resume_session_id))
        else:
            result = asyncio.run(agent.reply(text, resume_session_id))
    except Exception as exc:  # noqa: BLE001 - surface any agent/transport failure to the UI
        return jsonify({"type": "error", "message": f"Agent error: {exc}"}), 502

    session["claude_session_id"] = result["session_id"]

    return jsonify(
        {
            "type": "assistant",
            "text": result["reply"],
            "audio_url": result.get("audio_url"),
            "log": result.get("log", []),
        }
    )


@app.route("/api/reset", methods=["POST"])
def reset():
    session.pop("claude_session_id", None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    STARTUP_LOG.extend(asyncio.run(agent.describe_primitives()))
    for line in STARTUP_LOG:
        print(line)

    # debug=False deliberately: Flask's debugger exposes a Werkzeug
    # interactive console on any unhandled exception, which is a real RCE
    # risk even when only bound to localhost (browser/DNS-rebinding
    # attacks against local debug servers are a documented technique).
    # Matches the repo-root webapp.py's app.run(..., debug=False).
    app.run(debug=False, port=5050)
