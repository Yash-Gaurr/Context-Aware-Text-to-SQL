"""
app.py
-------
Thin Flask web layer. Contains almost no logic of its own: every request is
handed straight to core.query_handler.handle_query(), which wires the
semantic reasoning layer (core/reasoner.py) to the deterministic safety
layer (sql_validator + db_executor). Keeping the web layer this thin means
the whole pipeline can also be exercised and tested without Flask at all
(see tests/test_pipeline.py).

Which Reasoner backend is active is chosen ONCE here, at startup, from the
REASONER environment variable ('openai' | 'claude' | 'rule' | 'auto', see
core/reasoner.get_reasoner). Nothing downstream of this file needs to know
or care which one it got -- that's the point of the Reasoner interface.
"""

import os
import sys
import uuid
from flask import Flask, request, jsonify, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    for env_path in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]:
        if os.path.exists(env_path):
            load_dotenv(env_path)
except ImportError:
    pass

from core.query_handler import handle_query, context_manager
from core.schema_manager import SchemaManager
from core.reasoner import get_reasoner

app = Flask(__name__, static_folder="static", static_url_path="")
schema_manager = SchemaManager()

reasoner, startup_warning = get_reasoner()
if startup_warning:
    print(f"[startup] {startup_warning}")
print(f"[startup] Reasoning layer: {reasoner.label()}")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/session", methods=["POST"])
def new_session():
    return jsonify({"session_id": str(uuid.uuid4())})


@app.route("/api/mode", methods=["GET"])
def mode():
    return jsonify({"label": reasoner.label(), "warning": startup_warning})


@app.route("/api/query", methods=["POST"])
def query():
    data = request.get_json(force=True) or {}
    session_id = data.get("session_id") or "default"
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"type": "error", "stage": "input", "message": "Please enter a question."}), 400
    result = handle_query(session_id, message, reasoner)
    return jsonify(result)


@app.route("/api/schema", methods=["GET"])
def schema():
    return jsonify(schema_manager.as_dict())


@app.route("/api/history", methods=["GET"])
def history():
    session_id = request.args.get("session_id", "default")
    return jsonify({"history": context_manager.get_query_history(session_id)})


@app.route("/api/reset", methods=["POST"])
def reset():
    data = request.get_json(force=True) or {}
    session_id = data.get("session_id") or "default"
    context_manager.reset(session_id)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
