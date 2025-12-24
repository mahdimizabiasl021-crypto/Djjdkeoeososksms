from flask import Flask
import MKQ55596
import threading
import asyncio
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    # 1️⃣ وب‌سرور در یک Thread جدا
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # 2️⃣ ربات async در event loop اصلی
    asyncio.run(MKQ55596.run_bot())
