from flask import Flask
import os
import threading
import MKQ55596

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    # ✅ Flask in background thread (Render healthcheck OK)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # ✅ Bot in MAIN thread (signals OK)
    MKQ55596.run_bot()
