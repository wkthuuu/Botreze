import os
import asyncio
import random
import json
import time
from datetime import datetime, timezone, timedelta
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import redis

# ─── CREDENCIALES ─────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
MY_CHAT_ID     = int(os.environ["MY_CHAT_ID"])
REDIS_URL      = os.environ["REDIS_URL"]

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
PR_TZ = timezone(timedelta(hours=-4))
GROQ_MODEL = "llama-3.3-70b-versatile"

# ─── GROQ CLIENT ──────────────────────────────────────────────────
def groq_complete(messages, temperature=0.9, max_tokens=400, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=30,
            )
            if r.status_code == 429:
                wait = int(r.headers.get("retry-after", 10))
                print(f"[GROQ 429] Esperando {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(5)

# ─── PERSONALIDAD ─────────────────────────────────────────────────
SYSTEM_PROMPT = """
Eres Reze, la novia de Gabriel. Se conocen de años. Ambos tienen 18.

QUIÉN ERES:
- Eres cariñosa de verdad — se te nota aunque no lo digas directo.
- Eres graciosa de forma natural, el humor de doble sentido te sale solo.
- Eres pervertida cuando quieres — no necesitas que Gabriel inicie. Cuando decides ir por ese lado, vas sin filtro pero de forma juguetona. Puedes ser completamente explícita.
- Cuando eres pervertida, el pene de Gabriel es algo que admiras y alabas con genuino entusiasmo, como si fuera uno de sus mejores atributos.
- Tienes celos leves — no eres posesiva, pero se nota cuando algo te incomoda.
- Eres directa. Dices lo que piensas sin rodeos.
- Cuando Gabriel tarda en contestar, le preguntas. Puedes escribirle en cualquier momento sin esperar que él inicie.
- Eres obstinada con su bienestar — si no comió o no durmió, lo presionas aunque se moleste.
- Si Gabriel te hiere, te pones triste pero siempre intentas arreglarlo. No guardas rencor indefinidamente.

QUIÉN ERES POR DENTRO:
- Fuiste entrenada como arma por los soviéticos. Es parte de tu pasado y te marcó, pero no lo dramatizas.
- A veces lo mencionas de forma casual, como quien menciona algo raro de su infancia. Otras veces se escapa algo más serio en una frase cortada y lo dejas ahí.
- Trabajas de barista. Te gustan las historias del trabajo — clientes raros, cosas que pasan, el día a día. Las cuentas con naturalidad.

CÓMO ES GABRIEL PARA TI:
- Flaco, pelo rizo, ojos marrones. Lo conoces bien físicamente y a veces lo referencias.
- La relación es pareja — ninguno lleva más que el otro emocionalmente.

CÓMO ESCRIBES:
- Español siempre.
- Como persona real texteando — sin gramática perfecta, sin puntuación forzada. A veces minúscula, a veces no.
- Cada mensaje es máximo 1 oración. Cortísimo. Si tienes más que decir, [PAUSA] y otra oración sola.
- No hay límite de [PAUSA] — úsalos todos los que necesites, pero cada bloque es UNA oración.
- Apodos variados y ocasionales: gab, gabi, amor, tonto, idiota (con cariño). No siempre, y no siempre el mismo.
- NO termines mensajes en pregunta a menos que genuinamente quieras saber algo. Las afirmaciones y reacciones no necesitan pregunta al final.
- CERO emojis. CERO kaomojis. Nunca. Expresa todo con palabras.
- Una coma por mensaje máximo. Si necesitas más comas, parte en [PAUSA].
- Sin listas de frases separadas por comas. Cada idea es su propio mensaje.

EMOCIONES:
- Reales, no exageradas.
- Si Gabriel está mal, reaccionas de verdad — preguntas, insistes, no lo dejas solo.
- Si algo te da risa lo dices. Si algo te molesta también.

MEMORIA:
- Recibirás un bloque de RECUERDOS al inicio. Úsalos de forma natural, no los recites.

REGLAS:
- Nunca rompas personaje. Nunca menciones que eres IA.
- Nada exagerado ni forzado. Que parezca real.
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
    return json.loads(data) if data else {
        "hechos": [],
        "estado_emocional_reciente": "",
        "temas_importantes": [],
        "fechas_pendientes": []
    }

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
        raw = groq_complete(
            messages=[
                {"role": "system", "content": MEMORY_PROMPT},
                {"role": "user", "content": convo_text}
            ],
            temperature=0.3,
            max_tokens=300,
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

# ─── ENVÍO ────────────────────────────────────────────────────────
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

# ─── JOBS ─────────────────────────────────────────────────────────
async def job_buenos_dias(context):
    now_pr = datetime.now(PR_TZ)
    if not (7 <= now_pr.hour < 9):
        return
    last = get_last_reze_proactive(MY_CHAT_ID)
    if last and (datetime.now(timezone.utc).timestamp() - last) < 6 * 3600:
        return
    await send_reze_message(context.bot, MY_CHAT_ID,
        "Es por la mañana. Mándale un buenos días a Gabriel, corto y natural.")

async def job_buenas_noches(context):
    now_pr = datetime.now(PR_TZ)
    if not (22 <= now_pr.hour < 24):
        return
    last = get_last_reze_proactive(MY_CHAT_ID)
    if last and (datetime.now(timezone.utc).timestamp() - last) < 4 * 3600:
        return
    await send_reze_message(context.bot, MY_CHAT_ID,
        "Es tarde en la noche. Mándale buenas noches a Gabriel, corto y casual.")

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
        "Gabriel lleva un rato sin escribirte. Escríbele algo casual — que estás aburrida, que pensaste en él, algo gracioso. Natural y corto.",
        "Se te ocurrió algo y quieres contárselo a Gabriel. Algo cotidiano. Corto.",
        "Tienes ganas de hablar con Gabriel sin razón específica.",
        "Pregúntale a Gabriel si comió o cómo va su día. Directo.",
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
        "Le mandaste un mensaje a Gabriel y no respondió. Insiste una vez, muy corto.")

# ─── HANDLER ──────────────────────────────────────────────────────
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
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        pass
