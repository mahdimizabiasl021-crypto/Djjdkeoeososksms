import os
import threading
from flask import Flask

# ایمپورت ربات
import MKQ55596

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

if __name__ == "__main__":
    threading.Thread(target=MKQ55596.run_bot).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
