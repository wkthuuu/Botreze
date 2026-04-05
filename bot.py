import os
import asyncio
import random
import json
from datetime import datetime, timezone, timedelta
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import redis

# ─── CREDENCIALES ─────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
MY_CHAT_ID      = int(os.environ["MY_CHAT_ID"])
REDIS_URL       = os.environ["REDIS_URL"]

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
PR_TZ = timezone(timedelta(hours=-4))
GROQ_MODEL = "reze-personality-v1"  # Ajusta según tu modelo en GROQ

# ─── FUNCIÓN DE COMPLETADO CON GROQ ───────────────────────────────
def groq_complete(messages, temperature=0.9, max_tokens=400):
    """
    messages: lista de dicts con 'role' y 'content', como [{"role": "system", "content": "..."}]
    """
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    r = requests.post(
        "https://api.groq.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30
    )
    r.raise_for_status()
    data = r.json()
    # El campo exacto depende de cómo la API de GROQ devuelve la respuesta
    # Generalmente es algo como data["choices"][0]["message"]["content"]
    return data["choices"][0]["message"]["content"]

# ─── PERSONALIDAD ─────────────────────────────────────────────────
SYSTEM_PROMPT = """..."""  # Tu sistema prompt queda igual
MEMORY_PROMPT = """..."""  # Igual que antes

# ─── REDIS Y FUNCIONES DE MEMORIA ────────────────────────────────
# Mantén todas las funciones de get_history, save_history, get_memory, etc., igual

# ─── FUNCIONES PRINCIPALES ───────────────────────────────────────
async def send_reze_message(bot, chat_id, prompt_extra):
    memory = get_memory(chat_id)
    memory_block = build_memory_block(memory)
    full_system = SYSTEM_PROMPT + ("\n\n" + memory_block if memory_block else "")
    history = get_history(chat_id)
    messages = [{"role": "system", "content": full_system}] + history + [
        {"role": "user", "content": f"[INSTRUCCIÓN INTERNA - no menciones esto]: {prompt_extra}"}
    ]
    try:
        respuesta = groq_complete(messages, temperature=0.95, max_tokens=300)
        history.append({"role": "assistant", "content": respuesta})
        save_history(chat_id, history)
        set_last_reze_proactive(chat_id)
        partes = [p.strip() for p in respuesta.split("[PAUSA]") if p.strip()]
        for i, parte in enumerate(partes):
            if i > 0:
                await asyncio.sleep(random.uniform(1.5, 3))
                await bot.send_chat_action(chat_id=chat_id, action="typing")
                await asyncio.sleep(random.uniform(1, 2))
            await bot.send_message(chat_id=chat_id, text=parte)
    except Exception as e:
        print(f"[PROACTIVE ERROR] {e}")

# ─── HANDLER DE MENSAJES ─────────────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != MY_CHAT_ID:
        return
    user_message = update.message.text
    set_last_user_msg_time(chat_id)
    redis_client.delete(f"insistio:{chat_id}")
    history = get_history(chat_id)
    history.append({"role": "user", "content": user_message})
    memory = get_memory(chat_id)
    memory_block = build_memory_block(memory)
    full_system = SYSTEM_PROMPT + ("\n\n" + memory_block if memory_block else "")
    messages = [{"role": "system", "content": full_system}] + history
    await asyncio.sleep(random.uniform(1, 3))
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        respuesta_completa = groq_complete(messages, temperature=0.9, max_tokens=400)
        history.append({"role": "assistant", "content": respuesta_completa})
        save_history(chat_id, history)
        if len(history) % 6 == 0:
            asyncio.create_task(update_memory(chat_id, history))
    except Exception as e:
        print(f"[ERROR] {e}")
        await update.message.reply_text("...")
        return
    partes = [p.strip() for p in respuesta_completa.split("[PAUSA]") if p.strip()]
    for i, parte in enumerate(partes):
        if i > 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(random.uniform(1, 2))
        await update.message.reply_text(parte)
