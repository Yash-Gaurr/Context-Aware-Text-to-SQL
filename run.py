"""
Convenience entry point to run the Text-to-SQL web application from the project root.
"""
import os
import sys

# Ensure text2sql folder is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEXT2SQL_DIR = os.path.join(BASE_DIR, "text2sql")
if TEXT2SQL_DIR not in sys.path:
    sys.path.insert(0, TEXT2SQL_DIR)

try:
    from dotenv import load_dotenv
    for env_path in [
        os.path.join(BASE_DIR, ".env"),
        os.path.join(TEXT2SQL_DIR, ".env"),
    ]:
        if os.path.exists(env_path):
            load_dotenv(env_path)
except ImportError:
    pass

from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[startup] Starting Text-to-SQL Web Server on http://localhost:{port} ...")
    app.run(host="0.0.0.0", port=port, debug=True)
