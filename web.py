from flask import Flask
import MKQ55596
import asyncio

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    # ربات را داخل event loop اصلی اجرا می‌کنیم
    loop.create_task(MKQ55596.run_bot())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
