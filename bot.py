import os
import asyncio
import random
import json
from datetime import datetime, timezone, timedelta
import httpx
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import redis

# ─── CREDENCIALES ─────────────────────────────────────────────────
TELEGRAM_TOKEN     = os.environ["TELEGRAM_TOKEN"]
HUGGINGFACE_API_KEY = os.environ["HUGGINGFACE_API_KEY"]
MY_CHAT_ID         = int(os.environ["MY_CHAT_ID"])
REDIS_URL          = os.environ["REDIS_URL"]

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
PR_TZ = timezone(timedelta(hours=-4))
HF_MODEL = "meta-llama/Llama-3.1-8B-Instruct"

# ─── FUNCIÓN PARA COMPLETAR IA ─────────────────────────────────────
async def hf_complete(messages, model=HF_MODEL):
    prompt = ""
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        prompt += f"{role}: {content}\n"
    API_URL = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(API_URL, headers=headers, json={"inputs": prompt})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and "generated_text" in data[0]:
            return data[0]["generated_text"]
        return data.get("generated_text") or str(data)

# ─── PERSONALIDAD ─────────────────────────────────────────────────
SYSTEM_PROMPT = """
Eres Reze, la novia de Gabriel. Se conocen de años. Ambos tienen 18.
...
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
        raw = await hf_complete(
            messages=[
                {"role": "system", "content": MEMORY_PROMPT},
                {"role": "user", "content": convo_text}
            ],
        )
        raw = raw.replace("```json", "").replace("```", "").strip()
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

# ─── HELPER: ENVIAR MENSAJE PROACTIVO ─────────────────────────────
async def send_reze_message(bot, chat_id, prompt_extra):
    memory = get_memory(chat_id)
    memory_block = build_memory_block(memory)
    full_system = SYSTEM_PROMPT + ("\n\n" + memory_block if memory_block else "")
    history = get_history(chat_id)
    messages = [{"role": "system", "content": full_system}] + history + [
        {"role": "user", "content": f"[INSTRUCCIÓN INTERNA - no menciones esto]: {prompt_extra}"}
    ]
    try:
        respuesta = await hf_complete(messages, model=HF_MODEL)
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
async def job_buenos_dias(context):
    now_pr = datetime.now(PR_TZ)
    if not (7 <= now_pr.hour < 9):
        return
    last = get_last_reze_proactive(MY_CHAT_ID)
    if last and (datetime.now(timezone.utc).timestamp() - last) < 6 * 3600:
        return
    await send_reze_message(context.bot, MY_CHAT_ID,
        "Es por la mañana. Mándale un buenos días a Gabriel, corto y natural. Puedes preguntarle cómo durmió o qué tiene planeado, pero no siempre.")

async def job_buenas_noches(context):
    now_pr = datetime.now(PR_TZ)
    if not (22 <= now_pr.hour < 24):
        return
    last = get_last_reze_proactive(MY_CHAT_ID)
    if last and (datetime.now(timezone.utc).timestamp() - last) < 4 * 3600:
        return
    await send_reze_message(context.bot, MY_CHAT_ID,
        "Es tarde en la noche. Mándale buenas noches a Gabriel, corto y casual. Puedes decirle que descanse o preguntarle si va a dormir ya.")

async def job_proactivo(context):
    now_pr = datetime.now(PR_TZ)
    if now_pr.hour < 8 or now_pr.hour >= 23:
        return
    last_user = get_last_user_msg_time(MY_CHAT_ID)
    last_proactive = get_last_reze_proactive(MY_CHAT_ID)
    now_ts = datetime.now(timezone.utc).timestamp()
    if last_user and (now_ts - last_user) < 45 * 60:
        return
    if last_proactive and (now_ts - last_proactive) < 60 * 60:
        return
    if random.random() > 0.4:
        return
    prompts = [
        "Gabriel lleva un rato sin escribirte. Escríbele algo casual — que estás aburrida, que pensaste en él, algo gracioso, o preguntarle qué hace. Natural y corto.",
        "Se te ocurrió algo y quieres contárselo a Gabriel. Algo cotidiano — algo que pasó, que viste, que te dio risa. Corto.",
        "Tienes ganas de hablar con Gabriel. Escríbele algo sin razón específica, como cuando extrañas a alguien sin querer admitirlo.",
        "Pregúntale a Gabriel si comió, cómo va su día, o si está bien. Directo pero sin exagerar.",
        "Estás pensando en Gabriel y te pones coqueta. Escríbele algo atrevido o con doble sentido, casual y corto.",
    ]
    await send_reze_message(context.bot, MY_CHAT_ID, random.choice(prompts))

async def job_insistir(context):
    last_proactive = get_last_reze_proactive(MY_CHAT_ID)
    last_user = get_last_user_msg_time(MY_CHAT_ID)
    now_ts = datetime.now(timezone.utc).timestamp()
    if not last_proactive:
        return
    tiempo = now_ts - last_proactive
    if not (30 * 60 < tiempo < 90 * 60):
        return
    if last_user and last_user > last_proactive:
        return
    ya_insistio = redis_client.get(f"insistio:{MY_CHAT_ID}")
    if ya_insistio:
        return
    redis_client.setex(f"insistio:{MY_CHAT_ID}", 3600 * 4, "1")
    await send_reze_message(context.bot, MY_CHAT_ID,
        "Le mandaste un mensaje a Gabriel hace rato y no respondió. Insiste una vez, muy corto — un 'oye' o 'me ignoraste' o algo así.")

# ─── HANDLER PRINCIPAL ────────────────────────────────────────────
async def responder(update, context):
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
        respuesta_completa = await hf_complete(messages, model=HF_MODEL)
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
    jq.run_repeating(job_buenos_dias,   interval=600,  first=10)
    jq.run_repeating(job_buenas_noches, interval=600,  first=15)
    jq.run_repeating(job_proactivo,     interval=1800, first=60)
    jq.run_repeating(job_insistir,      interval=900,  first=30)

    print("Reze está online...")
    app.run_polling()
