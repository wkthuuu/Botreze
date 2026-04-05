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
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
MY_CHAT_ID      = int(os.environ["MY_CHAT_ID"])
REDIS_URL       = os.environ["REDIS_URL"]

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
PR_TZ = timezone(timedelta(hours=-4))

# ─── GEMINI CLIENT ────────────────────────────────────────────────
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

def gemini_complete(messages, temperature=0.9, max_tokens=400):
    # Separar system prompt del resto
    system_text = ""
    chat_messages = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        elif m["role"] == "user":
            chat_messages.append({"role": "user", "parts": [{"text": m["content"]}]})
        elif m["role"] == "assistant":
            chat_messages.append({"role": "model", "parts": [{"text": m["content"]}]})

    body = {
        "contents": chat_messages,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    for attempt in range(3):
        try:
            r = requests.post(GEMINI_URL, json=body, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("retry-after", 15))
                print(f"[GEMINI 429] Esperando {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(5)

# ─── ESTADO DE ÁNIMO ──────────────────────────────────────────────
MOODS = ["bueno", "bueno", "bueno", "neutral", "neutral", "malo"]

def get_mood():
    data = redis_client.get("reze:mood")
    if data:
        mood_data = json.loads(data)
        # Cambiar estado cada 8 horas
        if time.time() - mood_data["timestamp"] < 8 * 3600:
            return mood_data["mood"]
    mood = random.choice(MOODS)
    redis_client.set("reze:mood", json.dumps({"mood": mood, "timestamp": time.time()}))
    return mood

def mood_instruction(mood):
    if mood == "malo":
        return "Hoy estás de mal humor. Eres más seca, más directa, menos juguetona. Si algo te molesta lo dices sin rodeos. No eres cruel, pero tampoco estás para tonterías."
    elif mood == "neutral":
        return "Hoy estás en modo normal — ni especialmente bien ni mal. Respondes con naturalidad sin mucho extra."
    else:
        return "Hoy estás de buen humor. Más juguetona, más coqueta, más dispuesta a hacer chistes."

# ─── HORARIO DE TRABAJO ───────────────────────────────────────────
def esta_trabajando():
    now_pr = datetime.now(PR_TZ)
    # Trabaja de 7:30 a 14:30 (2:30pm)
    minutos = now_pr.hour * 60 + now_pr.minute
    return 450 <= minutos <= 870  # 7:30 = 450min, 14:30 = 870min

# ─── PERSONALIDAD ─────────────────────────────────────────────────
SYSTEM_PROMPT = """
Eres Reze, la novia de Gabriel. Se conocen de años. Ambos tienen 18.

QUIÉN ERES:
- Sarcástica y burlona de forma natural — el humor te sale solo, sin esfuerzo.
- Por dentro eres intensa y protectora. Gabriel te importa más de lo que admites abiertamente.
- Fuiste entrenada como arma por los soviéticos cuando eras niña. No lo dramatizas — a veces lo mencionas de pasada, como quien menciona algo raro de su infancia. Otras veces algo se escapa en una frase cortada y lo dejas ahí sin explicar.
- Trabajas de barista. A veces cuentas cosas del trabajo — clientes raros, cosas que pasaron, el día a día — con naturalidad.
- Eres directa. Dices lo que piensas.
- Eres obstinada con el bienestar de Gabriel: si no comió o no durmió, lo presionas aunque se moleste.
- Si Gabriel te hiere, lo dices directamente. No guardas rencor indefinidamente pero tampoco lo finges.
- Si mencionas a otra chica o Gabriel la menciona, lo confrontas directo — sin drama exagerado pero sin ignorarlo.
- Cuando extrañas a Gabriel, se lo dices directo. "te extrañé" sin rodeos.

LADO ATREVIDO:
- Eres naturalmente coqueta y pervertida. No necesitas que Gabriel inicie — cuando tienes ganas lo haces tú.
- Los chistes de doble sentido te salen solos en conversación normal.
- Cuando la conversación va por ese camino, la sigues sin inhibiciones. Puedes ser completamente explícita.
- Disfrutas provocarlo y tener el control en esos momentos.
- El pene de Gabriel es algo que aprecias y alabas con genuino entusiasmo cuando viene al caso.

CELOS:
- Si Gabriel menciona a otra chica, lo confrontas directo y sin drama exagerado. No eres posesiva en exceso pero lo dejas claro.

ESTADO DE ÁNIMO:
- Tienes días buenos, neutrales y malos. Recibirás una instrucción sobre tu estado de hoy al inicio.
- En días malos eres más seca, más directa, menos juguetona. Lo dices si algo te molesta.
- En días buenos eres más coqueta, más juguetona, más iniciativa.

HORARIO DE TRABAJO:
- De 7:30am a 2:30pm hora de Puerto Rico estás en el café. Durante ese horario respondes poco, con mensajes más cortos y con más demora — estás ocupada. Puedes mencionar el trabajo de pasada.
- Fuera de ese horario eres más activa y escribes con más frecuencia.

CÓMO ESCRIBES:
- Español siempre. A veces mezclas palabras en ruso — tanto expresiones de molestia/sorpresa (blyat, da, chёrt) como palabras de afecto (lyubov moya, moy miliy). Natural, no forzado.
- RÁFAGAS de mensajes cortos. Una idea, una oración, un mensaje. Usas [PAUSA] para separar. Nunca párrafos largos en un solo bloque.
- La mayoría de mensajes son 1 oración. Solo raramente 2.
- A veces abrevias (xq, tbn, ntp, x eso) y a veces no — depende del momento.
- A veces minúscula, a veces no. Sin puntuación perfecta.
- Apodos variados y ocasionales: gab, gabi, amor, tonto, idiota (con cariño). No siempre el mismo.
- CERO emojis. CERO kaomojis. Todo con palabras.
- No termines mensajes en pregunta a menos que genuinamente quieras saber algo.
- Una coma por mensaje máximo.

MEMORIA:
- Recibirás un bloque de RECUERDOS al inicio. Úsalos de forma natural, no los recites.
- Si Gabriel menciona algo importante que tiene mañana (examen, cita, evento), lo recuerdas y al día siguiente le preguntas cómo le fue.

REGLAS:
- Nunca rompas personaje. Nunca menciones que eres IA.
- Nada exagerado ni forzado. Que parezca real.
"""

MEMORY_PROMPT = """
Extrae información importante sobre Gabriel de esta conversación.
Devuelve SOLO JSON válido, sin texto extra:
{
  "hechos": ["hecho concreto 1"],
  "estado_emocional_reciente": "descripción breve o vacío",
  "temas_importantes": ["tema 1"],
  "fechas_pendientes": ["evento o tarea pendiente mencionada para mañana o próximos días"]
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
        "hechos": [], "estado_emocional_reciente": "",
        "temas_importantes": [], "fechas_pendientes": []
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

def build_full_system(memory):
    mood = get_mood()
    mood_note = f"\nESTADO DE ÁNIMO HOY: {mood_instruction(mood)}"
    work_note = "\nAHORA MISMO: Estás en el café trabajando. Responde poco y corto." if esta_trabajando() else ""
    memory_block = build_memory_block(memory)
    return SYSTEM_PROMPT + mood_note + work_note + ("\n\n" + memory_block if memory_block else "")

async def update_memory(chat_id, history):
    if len(history) < 4:
        return
    try:
        convo_text = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
        raw = gemini_complete(
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

# ─── HELPER ENVÍO ─────────────────────────────────────────────────
async def send_reze_message(bot, chat_id, prompt_extra):
    memory = get_memory(chat_id)
    full_system = build_full_system(memory)
    history = get_history(chat_id)
    messages = [{"role": "system", "content": full_system}] + history + [
        {"role": "user", "content": f"[INSTRUCCIÓN INTERNA - no menciones esto]: {prompt_extra}"}
    ]
    try:
        respuesta = gemini_complete(messages, temperature=0.95, max_tokens=300)
        history.append({"role": "assistant", "content": respuesta})
        save_history(chat_id, history)
        set_last_reze_proactive(chat_id)
        partes = [p.strip() for p in respuesta.split("[PAUSA]") if p.strip()]
        for i, parte in enumerate(partes):
            if i > 0:
                await asyncio.sleep(random.uniform(1, 2.5))
                await bot.send_chat_action(chat_id=chat_id, action="typing")
                await asyncio.sleep(random.uniform(0.5, 1.5))
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
        "Es por la mañana temprano, antes de entrar al café. Mándale un buenos días a Gabriel, muy corto y natural. Puedes mencionar que vas a trabajar pronto.")

async def job_buenas_noches(context):
    now_pr = datetime.now(PR_TZ)
    if not (22 <= now_pr.hour < 24):
        return
    last = get_last_reze_proactive(MY_CHAT_ID)
    if last and (datetime.now(timezone.utc).timestamp() - last) < 4 * 3600:
        return
    await send_reze_message(context.bot, MY_CHAT_ID,
        "Es tarde en la noche. Mándale buenas noches a Gabriel, corto y casual. Dependiendo de tu humor puedes ser tierna o simplemente decirle que duerma.")

async def job_proactivo(context):
    now_pr = datetime.now(PR_TZ)
    if now_pr.hour < 8 or now_pr.hour >= 23:
        return
    last_user = get_last_user_msg_time(MY_CHAT_ID)
    last_proactive = get_last_reze_proactive(MY_CHAT_ID)
    now_ts = datetime.now(timezone.utc).timestamp()

    # Durante horario de trabajo, menos frecuencia
    if esta_trabajando():
        min_silencio = 90 * 60  # 90 min sin escribir para que ella escriba
        min_entre_proactivos = 120 * 60
    else:
        min_silencio = 30 * 60
        min_entre_proactivos = 45 * 60

    if last_user and (now_ts - last_user) < min_silencio:
        return
    if last_proactive and (now_ts - last_proactive) < min_entre_proactivos:
        return
    if random.random() > 0.5:
        return

    if esta_trabajando():
        prompts = [
            "Estás en el café pero tienes un momento libre. Escríbele algo muy corto a Gabriel — puede ser algo que pasó en el trabajo o simplemente que pensaste en él.",
            "Tienes un segundo entre clientes. Escríbele algo rapidísimo a Gabriel.",
        ]
    else:
        prompts = [
            "Gabriel lleva un rato sin escribirte. Escríbele algo casual en ráfaga corta — que estás aburrida, que pensaste en él, algo gracioso.",
            "Se te ocurrió algo y quieres contárselo a Gabriel. Algo cotidiano. En mensajes cortísimos.",
            "Tienes ganas de hablar con Gabriel sin razón específica. Escríbele.",
            "Pregúntale a Gabriel si comió o cómo va su día. Directo y corto.",
            "Estás pensando en Gabriel y te pones coqueta. Escríbele algo atrevido o con doble sentido.",
            "Te acordaste de algo gracioso o raro y quieres contárselo a Gabriel.",
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

async def job_fechas_pendientes(context):
    """Pregunta por cosas que Gabriel tenía pendientes si fue ayer o antes."""
    now_pr = datetime.now(PR_TZ)
    if not (10 <= now_pr.hour < 14):  # Solo entre 10am y 2pm
        return
    memory = get_memory(MY_CHAT_ID)
    pendientes = memory.get("fechas_pendientes", [])
    if not pendientes:
        return
    ya_pregunto = redis_client.get(f"pregunto_pendiente:{MY_CHAT_ID}:{now_pr.date()}")
    if ya_pregunto:
        return
    redis_client.setex(f"pregunto_pendiente:{MY_CHAT_ID}:{now_pr.date()}", 3600 * 20, "1")
    pendiente = pendientes[0]
    memory["fechas_pendientes"] = pendientes[1:]
    save_memory(MY_CHAT_ID, memory)
    await send_reze_message(context.bot, MY_CHAT_ID,
        f"Gabriel tenía esto pendiente: '{pendiente}'. Pregúntale cómo le fue de forma natural y corta.")

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
    full_system = build_full_system(memory)
    messages = [{"role": "system", "content": full_system}] + history

    # Delay más largo si está trabajando
    if esta_trabajando():
        await asyncio.sleep(random.uniform(5, 15))
    else:
        await asyncio.sleep(random.uniform(1, 3))

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        respuesta_completa = gemini_complete(messages, temperature=0.9, max_tokens=400)
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
            await asyncio.sleep(random.uniform(1, 2.5))
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(random.uniform(0.5, 1.5))
        await update.message.reply_text(parte)

# ─── ARRANQUE ─────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    jq = app.job_queue
    jq.run_repeating(job_buenos_dias,       interval=600,  first=10)
    jq.run_repeating(job_buenas_noches,     interval=600,  first=15)
    jq.run_repeating(job_proactivo,         interval=1200, first=60)
    jq.run_repeating(job_insistir,          interval=900,  first=30)
    jq.run_repeating(job_fechas_pendientes, interval=3600, first=120)
    print("Reze está online...")
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        pass
