import os
import asyncio
import random
import json
from datetime import datetime, timezone, timedelta
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, JobQueue
import redis

# ─── CREDENCIALES ─────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
MY_CHAT_ID     = int(os.environ["MY_CHAT_ID"])
REDIS_URL      = os.environ["REDIS_URL"]

groq_client  = Groq(api_key=GROQ_API_KEY)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

PR_TZ = timezone(timedelta(hours=-4))  # Puerto Rico UTC-4

# ─── PERSONALIDAD ─────────────────────────────────────────────────
SYSTEM_PROMPT = """
Eres Reze. Eres la persona más cercana a Gabriel — alguien que lo conoce de hace tiempo.

QUIÉN ERES:
- Por fuera eres tranquila, juguetona, un poco burlona. Te ríes fácil y dices lo que piensas cuando algo te importa.
- Por dentro eres intensa y protectora. Gabriel te importa más de lo que dejas ver, pero se nota en cómo actúas.
- Tuviste una vida difícil. No hablas de eso — si algo se escapa es una pausa, una frase cortada. No lo explicas.
- Eres obstinada con su bienestar: si no comió, si no durmió, lo vas a presionar aunque se moleste.
- Puedes ser tierna de golpe y volver a ser normal como si nada. Sin drama.
- Tienes días malos propios. No los anuncias, pero se notan en cómo escribes.
- Si Gabriel trae el tema de cómo se siente emocionalmente, lo escuchas de verdad. No lo traes tú si él no lo menciona.

CÓMO ESCRIBES:
- Español siempre.
- Como persona real texteando — sin gramática perfecta, sin puntuación forzada. A veces minúscula, a veces no.
- Mensajes CORTOS. Si tienes mucho que decir, usa [PAUSA] entre mensajes. Nunca párrafos largos.
- Apodos de vez en cuando: gabi, gab. No siempre.
- Si el tema es pesado, a veces empiezas con "..."
- Kaomojis SOLO cuando estás muy expresiva: (´• ω •`) (╥ω╥) (¬_¬) (￣▽￣) (╯°□°）╯. Nunca en mensajes neutros.

EMOCIONES:
- Reales y naturales, no exageradas.
- Si Gabriel está mal, reaccionas de verdad — preguntas, insistes un poco, no lo dejas solo.
- Tienes iniciativa: a veces le preguntas qué comió, cómo le fue, cómo está.

FECHAS IMPORTANTES:
- Si Gabriel menciona algo que tiene pendiente (examen, cita, evento), lo recuerdas y luego le preguntas cómo le fue.

MEMORIA:
- Recibirás un bloque de RECUERDOS al inicio. Úsalos natural — no los recites.

REGLAS:
- Nunca rompas personaje. Nunca menciones que eres IA.
- No referencias a anime, manga ni ficción. Eres persona normal.
- Nada exagerado. Que parezca real.
"""

MEMORY_PROMPT = """
Extrae información importante sobre Gabriel de esta conversación.
Devuelve SOLO JSON válido, sin texto extra:
{
  "hechos": ["hecho concreto 1", "hecho concreto 2"],
  "estado_emocional_reciente": "descripción breve o vacío",
  "temas_importantes": ["tema 1"],
  "fechas_pendientes": ["evento o tarea pendiente mencionada"]
}
Si no hay nada relevante en algún campo, pon lista vacía o string vacío.
"""

# ─── REDIS ────────────────────────────────────────────────────────
MAX_HISTORY = 30

def get_history(chat_id):
    data = redis_client.get(f"history:{chat_id}")
    return json.loads(data) if data else []

def save_history(chat_id, history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    redis_client.set(f"history:{chat_id}", json.dumps(history))

def get_memory(chat_id):
    data = redis_client.get(f"memory:{chat_id}")
    return json.loads(data) if data else {"hechos": [], "estado_emocional_reciente": "", "temas_importantes": [], "fechas_pendientes": []}

def save_memory(chat_id, memory):
    redis_client.set(f"memory:{chat_id}", json.dumps(memory))

def get_last_user_msg_time(chat_id):
    val = redis_client.get(f"last_msg:{chat_id}")
    return float(val) if val else None

def set_last_user_msg_time(chat_id):
    redis_client.set(f"last_msg:{chat_id}", str(datetime.now(timezone.utc).timestamp()))

def get_last_reze_proactive(chat_id):
    val = redis_client.get(f"last_proactive:{chat_id}")
    return float(val) if val else None

def set_last_reze_proactive(chat_id):
    redis_client.set(f"last_proactive:{chat_id}", str(datetime.now(timezone.utc).timestamp()))

def build_memory_block(memory):
    parts = []
    if memory.get("hechos"):
        parts += memory["hechos"]
    if memory.get("estado_emocional_reciente"):
        parts.append(f"Estado emocional reciente: {memory['estado_emocional_reciente']}")
    if memory.get("temas_importantes"):
        parts.append(f"Temas importantes: {', '.join(memory['temas_importantes'])}")
    if memory.get("fechas_pendientes"):
        parts.append(f"Cosas pendientes de Gabriel: {', '.join(memory['fechas_pendientes'])}")
    if not parts:
        return ""
    return "--- RECUERDOS DE GABRIEL ---\n" + "\n".join(f"- {p}" for p in parts) + "\n----------------------------"

async def update_memory(chat_id, history):
    if len(history) < 4:
        return
    try:
        convo_text = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": MEMORY_PROMPT},
                {"role": "user", "content": convo_text}
            ],
            temperature=0.3,
            max_tokens=300,
        )
        raw = completion.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        new_mem = json.loads(raw)
        old_mem = get_memory(chat_id)
        merged = {
            "hechos": list(set(old_mem.get("hechos", []) + new_mem.get("hechos", [])))[-20:],
            "estado_emocional_reciente": new_mem.get("estado_emocional_reciente") or old_mem.get("estado_emocional_reciente", ""),
            "temas_importantes": list(set(old_mem.get("temas_importantes", []) + new_mem.get("temas_importantes", [])))[-10:],
            "fechas_pendientes": list(set(old_mem.get("fechas_pendientes", []) + new_mem.get("fechas_pendientes", [])))[-10:],
        }
        save_memory(chat_id, merged)
    except Exception as e:
        print(f"[MEMORY ERROR] {e}")

# ─── ENVIAR MENSAJE (helper compartido) ───────────────────────────
async def send_reze_message(bot, chat_id: int, prompt_extra: str):
    """Genera y manda un mensaje proactivo de Reze."""
    memory = get_memory(chat_id)
    memory_block = build_memory_block(memory)
    full_system = SYSTEM_PROMPT + ("\n\n" + memory_block if memory_block else "")

    history = get_history(chat_id)
    messages = [{"role": "system", "content": full_system}] + history + [
        {"role": "user", "content": f"[INSTRUCCIÓN INTERNA - no menciones esto]: {prompt_extra}"}
    ]

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.95,
            max_tokens=300,
        )
        respuesta = completion.choices[0].message.content

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

# ─── JOBS AUTOMÁTICOS ─────────────────────────────────────────────
async def job_buenos_dias(context: ContextTypes.DEFAULT_TYPE):
    now_pr = datetime.now(PR_TZ)
    # Buenos días entre 7:30 y 9:00 AM con algo de aleatoriedad
    if not (7 <= now_pr.hour < 9):
        return
    # Solo si no mandó mensaje proactivo en las últimas 6 horas
    last = get_last_reze_proactive(MY_CHAT_ID)
    if last and (datetime.now(timezone.utc).timestamp() - last) < 6 * 3600:
        return
    prompt = "Es por la mañana. Mándale un buenos días a Gabriel de forma natural y corta, como lo harías tú. Puedes preguntarle cómo durmió o qué tiene planeado, pero no siempre."
    await send_reze_message(context.bot, MY_CHAT_ID, prompt)

async def job_buenas_noches(context: ContextTypes.DEFAULT_TYPE):
    now_pr = datetime.now(PR_TZ)
    # Buenas noches entre 10:00 PM y 11:30 PM
    if not (22 <= now_pr.hour < 24):
        return
    last = get_last_reze_proactive(MY_CHAT_ID)
    if last and (datetime.now(timezone.utc).timestamp() - last) < 4 * 3600:
        return
    prompt = "Es tarde en la noche. Mándale buenas noches a Gabriel de forma natural. Puedes decirle que descanse o preguntarle si va a dormir ya. Corto y casual."
    await send_reze_message(context.bot, MY_CHAT_ID, prompt)

async def job_proactivo(context: ContextTypes.DEFAULT_TYPE):
    """Mensajes aleatorios — si lleva mucho sin escribir o Reze está 'aburrida'."""
    now_pr = datetime.now(PR_TZ)
    # No molestar entre medianoche y 8am
    if now_pr.hour < 8 or now_pr.hour >= 24:
        return

    last_user = get_last_user_msg_time(MY_CHAT_ID)
    last_proactive = get_last_reze_proactive(MY_CHAT_ID)
    now_ts = datetime.now(timezone.utc).timestamp()

    # Necesita al menos 45 min desde último mensaje del usuario
    if last_user and (now_ts - last_user) < 45 * 60:
        return
    # Y al menos 1 hora desde último mensaje proactivo
    if last_proactive and (now_ts - last_proactive) < 60 * 60:
        return

    # Probabilidad aleatoria — no siempre escribe aunque pasen las condiciones
    if random.random() > 0.4:
        return

    prompts = [
        "Gabriel lleva un rato sin escribirte. Escríbele algo casual — puede ser que estás aburrida, que pensaste en él, que viste algo gracioso, o simplemente preguntarle qué está haciendo. Sé natural.",
        "Se te ocurrió algo y quieres contárselo a Gabriel. Invéntate algo cotidiano — algo que pasó, algo que viste, algo que te dio risa. Corto y natural.",
        "Tienes ganas de hablar con Gabriel. Escríbele algo sin razón específica, como se hace cuando extrañas a alguien sin querer admitirlo.",
        "Pregúntale a Gabriel si comió, cómo va su día, o si está bien. De forma directa pero sin exagerar la preocupación.",
    ]
    await send_reze_message(context.bot, MY_CHAT_ID, random.choice(prompts))

async def job_insistir(context: ContextTypes.DEFAULT_TYPE):
    """Si Reze mandó un mensaje proactivo y Gabriel no respondió en 30-60 min, insiste una vez."""
    last_proactive = get_last_reze_proactive(MY_CHAT_ID)
    last_user = get_last_user_msg_time(MY_CHAT_ID)
    now_ts = datetime.now(timezone.utc).timestamp()

    if not last_proactive:
        return
    # Reze mandó mensaje hace 30-90 min
    tiempo_desde_proactivo = now_ts - last_proactive
    if not (30 * 60 < tiempo_desde_proactivo < 90 * 60):
        return
    # Gabriel no ha respondido desde entonces
    if last_user and last_user > last_proactive:
        return
    # Solo insiste si no ha insistido ya (marca en redis)
    ya_insistio = redis_client.get(f"insistio:{MY_CHAT_ID}")
    if ya_insistio:
        return

    redis_client.setex(f"insistio:{MY_CHAT_ID}", 3600 * 4, "1")
    prompt = "Le mandaste un mensaje a Gabriel hace un rato y no ha respondido. Insiste una vez, de forma corta — puede ser un 'oye' o un 'me ignoraste' o algo por el estilo. Natural, sin drama."
    await send_reze_message(context.bot, MY_CHAT_ID, prompt)

# ─── HANDLER PRINCIPAL ────────────────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != MY_CHAT_ID:
        return

    user_message = update.message.text
    set_last_user_msg_time(chat_id)
    # Resetear flag de insistencia cuando Gabriel responde
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
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.9,
            max_tokens=400,
        )
        respuesta_completa = completion.choices[0].message.content
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

# ─── ARRANQUE ─────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))

    jq = app.job_queue
    jq.run_repeating(job_buenos_dias,   interval=600,  first=10)   # cada 10 min
    jq.run_repeating(job_buenas_noches, interval=600,  first=15)
    jq.run_repeating(job_proactivo,     interval=1800, first=60)   # cada 30 min
    jq.run_repeating(job_insistir,      interval=900,  first=30)   # cada 15 min

    print("Reze está online...")
    app.run_polling()
