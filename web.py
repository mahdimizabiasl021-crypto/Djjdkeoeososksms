from flask import Flask
import MKQ55596
import threading
import asyncio
import os
import nest_asyncio

nest_asyncio.apply()  # مهم برای Render

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    loop = asyncio.get_event_loop()
    loop.create_task(MKQ55596.run_bot())
