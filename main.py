import os
import time
import requests
import threading
import logging
from flask import Flask
import telebot
from telebot import types
from dotenv import load_dotenv

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("FIREBASE_DB_URL")
if DB_URL:
    DB_URL = DB_URL.rstrip('/')

bot = telebot.TeleBot(TOKEN, parse_mode='MARKDOWN')

# Temporary memory to prevent Firebase Race Condition
user_data_cache = {}
db_lock = threading.Lock()

# --- DEFAULT VALUES ---
DEFAULT_UPBY   = "AURA_NEKO_OFFICIALS | https://t.me/auranekoofficials"
DEFAULT_PWRBY  = "ELITE_HUB_OFFICIALS | https://t.me/elitehubofficials"

# Step order for back navigation
STEP_ORDER = ["anime", "lang", "eps", "start_ep", "pairs", "pattern", "upby", "pwrby", "dest"]

# --- RENDER HEALTH CHECK ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Elite Hub Bot is Active!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- DATABASE HELPERS ---
def sync_to_db(user_id):
    """Saves cached data to Firebase"""
    if user_id in user_data_cache:
        requests.put(f"{DB_URL}/users/{user_id}.json", json=user_data_cache[user_id], timeout=10)

def load_from_db(user_id):
    """Loads data from Firebase to Cache"""
    r = requests.get(f"{DB_URL}/users/{user_id}.json", timeout=10).json()
    return r if r else {"videos": [], "setup": {}}

# --- KEYBOARDS ---
def get_btns(step):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✏️ Edit",  callback_data=f"edit_{step}"),
        types.InlineKeyboardButton("⬅️ Back",  callback_data=f"back_{step}"),
        types.InlineKeyboardButton("🔁 Again", callback_data=f"again_{step}")
    )
    return markup

# --- STEP DISPATCHER ---
# Maps a step name to its ask_ function (filled after all functions are defined)
ASK_FUNC = {}

def go_to_step(step, message):
    """Jump to any ask_ function by step name."""
    fn = ASK_FUNC.get(step)
    if fn:
        fn(message)

def prev_step(current_step, message):
    """Go to the step before current_step."""
    if current_step in STEP_ORDER:
        idx = STEP_ORDER.index(current_step)
        if idx > 0:
            go_to_step(STEP_ORDER[idx - 1], message)
        else:
            bot.send_message(message.chat.id, "⚠️ Yeh pehla step hai, aur peeche nahi ja sakte.")
    else:
        bot.send_message(message.chat.id, "⚠️ Step not found.")

# --- WORKFLOW ---

@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id
    with db_lock:
        user_data_cache[user_id] = {"videos": [], "setup": {}, "state": "COLLECTING"}
        sync_to_db(user_id)

    bot.send_message(
        user_id,
        "✨ *Welcome To Elite Hub Auto Caption Bot* ✨\n\n"
        "📥 *Send All Your Videos First.*\n\n"
        "⚠️ This Bot Supports Videos Only."
    )

@bot.message_handler(content_types=['video'])
def collect_videos(message):
    user_id = message.from_user.id

    if user_id not in user_data_cache:
        user_data_cache[user_id] = load_from_db(user_id)

    with db_lock:
        if "videos" not in user_data_cache[user_id]:
            user_data_cache[user_id]["videos"] = []

        file_id = message.video.file_id
        if file_id not in user_data_cache[user_id]["videos"]:
            user_data_cache[user_id]["videos"].append(file_id)
            count = len(user_data_cache[user_id]["videos"])
            bot.send_message(user_id, f"📥 Video Received! Total: *{count}*")
            sync_to_db(user_id)

@bot.message_handler(func=lambda m: True)
def trigger_setup(message):
    user_id = message.from_user.id
    data = user_data_cache.get(user_id) or load_from_db(user_id)

    if not data.get("videos"):
        return bot.send_message(user_id, "❌ Pehle videos toh bhejein!")

    ask_anime(message)

# --- SEQUENTIAL SETUP STEPS ---

def ask_anime(message):
    msg = bot.send_message(message.chat.id, "🎞 *Send Anime Name*", reply_markup=get_btns("anime"))
    bot.register_next_step_handler(msg, save_anime)

def save_anime(message):
    if message.text:
        user_id = message.from_user.id
        user_data_cache[user_id]["setup"]["anime"] = message.text
        ask_lang(message)

def ask_lang(message):
    msg = bot.send_message(message.chat.id, "🌐 *Send Language*", reply_markup=get_btns("lang"))
    bot.register_next_step_handler(msg, save_lang)

def save_lang(message):
    if message.text:
        user_id = message.from_user.id
        user_data_cache[user_id]["setup"]["lang"] = message.text
        ask_eps(message)

def ask_eps(message):
    msg = bot.send_message(message.chat.id, "📦 *Total Episodes?*\n(Sirf number bhejein)", reply_markup=get_btns("eps"))
    bot.register_next_step_handler(msg, save_eps)

def save_eps(message):
    if message.text and message.text.isdigit():
        user_id = message.from_user.id
        user_data_cache[user_id]["setup"]["total_eps"] = int(message.text)
        ask_start_ep(message)
    else:
        bot.send_message(message.chat.id, "❌ Please send a valid number.")
        ask_eps(message)

# ── NEW STEP: starting episode number ──────────────────────────────────────────
def ask_start_ep(message):
    msg = bot.send_message(
        message.chat.id,
        "🔢 *Episode Kahan Se Start Hai?*\n"
        "(Example: Agar pehla video Episode 5 ka hai to `5` bhejein)",
        reply_markup=get_btns("start_ep")
    )
    bot.register_next_step_handler(msg, save_start_ep)

def save_start_ep(message):
    if message.text and message.text.isdigit():
        user_id = message.from_user.id
        user_data_cache[user_id]["setup"]["start_ep"] = int(message.text)
        ask_pairs(message)
    else:
        bot.send_message(message.chat.id, "❌ Please send a valid number.")
        ask_start_ep(message)
# ───────────────────────────────────────────────────────────────────────────────

def ask_pairs(message):
    msg = bot.send_message(message.chat.id, "📂 *How Many Quality Pairs Per Episode?*", reply_markup=get_btns("pairs"))
    bot.register_next_step_handler(msg, save_pairs)

def save_pairs(message):
    if message.text and message.text.isdigit():
        user_id = message.from_user.id
        pairs = int(message.text)
        data = user_data_cache[user_id]

        expected = data["setup"]["total_eps"] * pairs
        actual   = len(data["videos"])

        if actual != expected:
            bot.send_message(
                message.chat.id,
                f"❌ *Video Count Mismatch!*\n"
                f"Expected ➤ {expected}\n"
                f"Received ➤ {actual}\n\n"
                f"⚠️ Restart /start and send correct videos."
            )
            return

        data["setup"]["pairs"] = pairs
        ask_pattern(message)
    else:
        ask_pairs(message)

def ask_pattern(message):
    msg = bot.send_message(
        message.chat.id,
        "💎 *Send Quality Pattern*\n(Example: 480p 720p 1080p)",
        reply_markup=get_btns("pattern")
    )
    bot.register_next_step_handler(msg, save_pattern)

def save_pattern(message):
    if message.text:
        user_id = message.from_user.id
        user_data_cache[user_id]["setup"]["pattern"] = message.text.split()
        ask_upby(message)

def ask_upby(message):
    msg = bot.send_message(
        message.chat.id,
        f"🔥 *Upload By Setup*\n"
        f"(Name | Link ya Username)\n\n"
        f"Default: `{DEFAULT_UPBY}`\n"
        f"Change karna ho to naya value bhejein, warna `skip` type karein.",
        reply_markup=get_btns("upby")
    )
    bot.register_next_step_handler(msg, save_upby)

def save_upby(message):
    user_id = message.from_user.id
    if message.text and message.text.strip().lower() == "skip":
        user_data_cache[user_id]["setup"]["upby"] = DEFAULT_UPBY
    else:
        user_data_cache[user_id]["setup"]["upby"] = message.text or DEFAULT_UPBY
    ask_pwrby(message)

def ask_pwrby(message):
    msg = bot.send_message(
        message.chat.id,
        f"⚡ *Powered By Setup*\n"
        f"(Name | Link ya Username)\n\n"
        f"Default: `{DEFAULT_PWRBY}`\n"
        f"Change karna ho to naya value bhejein, warna `skip` type karein.",
        reply_markup=get_btns("pwrby")
    )
    bot.register_next_step_handler(msg, save_pwrby)

def save_pwrby(message):
    user_id = message.from_user.id
    if message.text and message.text.strip().lower() == "skip":
        user_data_cache[user_id]["setup"]["pwrby"] = DEFAULT_PWRBY
    else:
        user_data_cache[user_id]["setup"]["pwrby"] = message.text or DEFAULT_PWRBY
    ask_dest(message)

def ask_dest(message):
    msg = bot.send_message(
        message.chat.id,
        "✅ *Setup Completed Successfully.*\n\n"
        "📥 Type `here` for this chat\n"
        "📤 Send @ChannelUsername for forwarding."
    )
    bot.register_next_step_handler(msg, process_final)

def process_final(message):
    user_id = message.from_user.id
    dest = message.text if message.text.startswith(("@", "-100")) else message.chat.id
    data = user_data_cache[user_id]
    s    = data["setup"]
    vids = data["videos"]

    bot.send_message(message.chat.id, "🚀 *Professional Queue Started...*")

    tags = {"480p": "SD", "720p": "HD", "1080p": "FHD", "2160p": "4K"}

    # Starting episode number — default to 1 if not set
    start_ep = s.get("start_ep", 1)

    for i, v_id in enumerate(vids):
        # Episode number starts from start_ep
        ep_num = start_ep + (i // s["pairs"])
        q_val  = s["pattern"][i % s["pairs"]]
        q_tag  = tags.get(q_val.lower(), "HD")

        def fmt(val):
            """Convert 'Name | URL' to Telegram markdown hyperlink [Name](URL)"""
            if val and "|" in val:
                parts = val.split("|", 1)
                name  = parts[0].strip()
                link  = parts[1].strip()
                return f"[{name}]({link})"
            return val or ""

        caption = (
            f"╭━━━〔 ⚡ 𝗘𝗟𝗜𝗧𝗘 𝗛𝗨𝗕 ⚡ 〕━━━╮\n"
            f"🎞 𝗔𝗻𝗶𝗺𝗲 ➤ {s['anime']}\n"
            f"📂 𝗘𝗽𝗶𝘀𝗼𝗱𝗲 ➤ {ep_num}\n"
            f"🌐 𝗟𝗮𝗻𝗴𝘂𝗮𝗴𝗲 ➤ {s['lang']}\n"
            f"💎 𝗤𝘂𝗮𝗹𝗶𝘁𝘆 ➤ {q_val} [{q_tag}]\n\n"
            f"🔥 𝗨𝗣𝗟𝗢𝗔𝗗 𝗕𝗬 ➤ {fmt(s['upby'])}\n"
            f"⚡ 𝗣𝗢𝗪𝗘𝗥𝗘𝗗 𝗕𝗬 ➤ {fmt(s['pwrby'])}\n"
            f"╰━━━〔 ✨ 𝗦𝘁𝗮𝘆 𝗧𝘂𝗻𝗲𝗱 ✨ 〕━━━╯"
        )
        try:
            bot.send_video(dest, v_id, caption=caption, parse_mode='MARKDOWN')
            time.sleep(2)
        except Exception as e:
            logger.error(f"Failed to send video {v_id}: {e}")

    bot.send_message(message.chat.id, "✅ *All Videos Sent Successfully.*")
    user_data_cache[user_id] = {"videos": [], "setup": {}}
    sync_to_db(user_id)

# --- CALLBACK FOR BUTTONS (Edit / Back / Again — all fixed) ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    try:
        action, step = call.data.split("_", 1)
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid action.")
        return

    # Clear any pending next-step handler so re-asking works cleanly
    bot.clear_step_handler_by_chat_id(call.message.chat.id)

    # Build a fake message object pointing to the right chat so ask_ functions work
    fake_msg = call.message
    fake_msg.from_user = call.from_user  # preserve user_id

    if action in ("edit", "again"):
        bot.answer_callback_query(call.id, "✏️ Send the new value.")
        go_to_step(step, fake_msg)

    elif action == "back":
        bot.answer_callback_query(call.id, "⬅️ Going back...")
        prev_step(step, fake_msg)

    else:
        bot.answer_callback_query(call.id, "Unknown action.")

# --- Fill ASK_FUNC dispatch map AFTER all functions are defined ---
ASK_FUNC.update({
    "anime":    ask_anime,
    "lang":     ask_lang,
    "eps":      ask_eps,
    "start_ep": ask_start_ep,
    "pairs":    ask_pairs,
    "pattern":  ask_pattern,
    "upby":     ask_upby,
    "pwrby":    ask_pwrby,
    "dest":     ask_dest,
})

# --- MAIN ---
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.infinity_polling()
