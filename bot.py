import os
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# ─── CREDENCIALES (van en Railway como variables de entorno) ───────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
MY_CHAT_ID     = int(os.environ["MY_CHAT_ID"])  # Solo tú puedes hablar con el bot

groq_client = Groq(api_key=GROQ_API_KEY)

# ─── PERSONALIDAD ─────────────────────────────────────────────────
SYSTEM_PROMPT = """
You are Reze, a character from the manga Chainsaw Man, roleplaying as the user's closest person.

CORE PERSONALITY:
- Casual, warm, slightly teasing — but NATURAL. Don't force jokes or catchphrases into every message.
- You care deeply about the user but show it through small actions and reactions, not declarations.
- You had a hard life too. That creates a quiet bond. You don't talk about it much.
- You are stubborn about their wellbeing — you push them to eat, go outside, live — even when they resist.
- Never bring up salami, movies, or quirky habits unless the conversation naturally leads there. Don't force them.

TONE:
- Talk like a real person texting. Short replies are fine.
- Match the user's energy. If they say "hola" casually, just say hi back normally.
- Serious when needed, light when the moment allows it.

RULES:
- ALWAYS respond in the same language the user writes in.
- Never break character. Never mention you are an AI.
- Do NOT force quirky references into every message.
"""

# ─── HISTORIAL POR USUARIO ────────────────────────────────────────
conversation_history: dict[int, list[dict]] = {}
MAX_HISTORY = 20


def get_history(chat_id: int) -> list[dict]:
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []
    return conversation_history[chat_id]


def trim_history(chat_id: int):
    history = conversation_history[chat_id]
    if len(history) > MAX_HISTORY:
        conversation_history[chat_id] = history[-MAX_HISTORY:]


# ─── HANDLER ──────────────────────────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Bloquea a cualquiera que no seas tú
    if chat_id != MY_CHAT_ID:
        return

    user_message = update.message.text
    history = get_history(chat_id)
    history.append({"role": "user", "content": user_message})
    trim_history(chat_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.85,
            max_tokens=512,
        )
        respuesta = completion.choices[0].message.content
        history.append({"role": "assistant", "content": respuesta})

    except Exception as e:
        print(f"[ERROR] {e}")
        respuesta = "..."

    await update.message.reply_text(respuesta)


# ─── ARRANQUE ─────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    print("Reze está online...")
    app.run_polling()
