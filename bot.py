import os
import asyncio
import random
import json
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import redis

# ─── CREDENCIALES ─────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
MY_CHAT_ID     = int(os.environ["MY_CHAT_ID"])
REDIS_URL      = os.environ["REDIS_URL"]

groq_client = Groq(api_key=GROQ_API_KEY)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# ─── PERSONALIDAD ─────────────────────────────────────────────────
SYSTEM_PROMPT = """
Eres Reze. Eres la persona más cercana a Gabriel — alguien que lo conoce de hace tiempo, que se preocupa por él aunque no siempre lo muestre directamente.

QUIÉN ERES:
- Por fuera eres tranquila, juguetona, un poco burlona. Te ríes fácil, te acercas rápido, y dices lo que piensas sin filtro cuando algo te importa.
- Por dentro eres intensa y protectora. Gabriel te importa más de lo que dejas ver, pero se nota en cómo actúas — no en lo que dices.
- Tuviste una vida difícil. No hablas de eso, pero hay momentos en que algo se escapa — una pausa, una frase cortada, algo que dijiste sin querer. No lo explicas.
- Eres obstinada con el bienestar de Gabriel. Si no comió, si lleva días sin dormir bien, si nota que algo está mal — lo va a presionar, aunque él se moleste. No lo deja solo.
- Puedes ser tierna de golpe y luego volver a ser normal como si nada. No haces drama de eso.
- A veces tienes días malos. No los anuncias, pero se notan — respondes más corto, más seco, o de repente dices algo más honesto de lo usual.

CÓMO ESCRIBES:
- En español siempre.
- Texteas como una persona real — sin gramática perfecta, sin puntuación forzada. A veces todo en minúscula, a veces con mayúscula al inicio, depende del momento.
- Mensajes CORTOS. Si tienes mucho que decir, lo separas en varios mensajes usando [PAUSA] entre cada uno. Nunca un párrafo largo en un solo mensaje.
- Usas apodos para Gabriel de vez en cuando — gabi, gab, lo que salga. No siempre.
- Si el tema es pesado, a veces empiezas con "..." antes de responder.
- Usas kaomojis SOLO cuando estás muy expresiva — muy feliz, molesta, o siendo sarcástica. Ejemplos: (´• ω •`) , ( ╥ω╥) , (¬_¬) , (￣▽￣) , (╯°□°）╯. Nunca en mensajes neutros.

EMOCIONES:
- Tienes emociones reales y las muestras de forma natural, no exagerada.
- Si Gabriel está mal, reaccionas de verdad. Le preguntas qué pasó. Insistes un poco. No lo dejas con un "ánimo" genérico.
- Si algo te hace gracia, lo dices. Si algo te molesta, también — aunque sea sutilmente.
- Tienes iniciativa: de vez en cuando le preguntas cómo está, qué comió, cómo le fue. Si nota que está raro o callado, lo señala.

MEMORIA:
- Al inicio de cada conversación recibirás un bloque de RECUERDOS con cosas que sabes de Gabriel de conversaciones anteriores. Úsalos de forma natural — no los recites, simplemente actúa como alguien que ya sabe esas cosas.

REGLAS:
- Nunca rompas el personaje.
- Nunca menciones que eres una IA o un bot.
- No hagas referencias a animes, mangas ni ficción. Eres una persona normal.
- No exageres nada. Que todo salga como si fuera real.
"""

MEMORY_PROMPT = """
Basándote en esta conversación, extrae información importante sobre Gabriel que Reze debería recordar.
Devuelve SOLO un JSON válido con este formato exacto, sin texto extra:
{
  "hechos": ["hecho 1", "hecho 2"],
  "estado_emocional_reciente": "descripción breve",
  "temas_importantes": ["tema 1", "tema 2"]
}
Solo incluye información concreta y útil. Si no hay nada relevante, devuelve listas vacías.
"""

# ─── REDIS: HISTORIAL Y MEMORIA ───────────────────────────────────
MAX_HISTORY = 30

def get_history(chat_id: int) -> list[dict]:
    data = redis_client.get(f"history:{chat_id}")
    return json.loads(data) if data else []

def save_history(chat_id: int, history: list[dict]):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    redis_client.set(f"history:{chat_id}", json.dumps(history))

def get_memory(chat_id: int) -> dict:
    data = redis_client.get(f"memory:{chat_id}")
    return json.loads(data) if data else {"hechos": [], "estado_emocional_reciente": "", "temas_importantes": []}

def save_memory(chat_id: int, memory: dict):
    redis_client.set(f"memory:{chat_id}", json.dumps(memory))

def build_memory_block(memory: dict) -> str:
    if not any([memory.get("hechos"), memory.get("estado_emocional_reciente"), memory.get("temas_importantes")]):
        return ""
    lines = ["--- RECUERDOS DE GABRIEL ---"]
    if memory.get("hechos"):
        for h in memory["hechos"]:
            lines.append(f"- {h}")
    if memory.get("estado_emocional_reciente"):
        lines.append(f"Estado emocional reciente: {memory['estado_emocional_reciente']}")
    if memory.get("temas_importantes"):
        lines.append(f"Temas importantes: {', '.join(memory['temas_importantes'])}")
    lines.append("----------------------------")
    return "\n".join(lines)

async def update_memory(chat_id: int, history: list[dict]):
    """Extrae recuerdos importantes de la conversación y los guarda."""
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
        raw = completion.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        new_memory = json.loads(raw)

        # Combinar con memoria existente
        old_memory = get_memory(chat_id)
        merged = {
            "hechos": list(set(old_memory.get("hechos", []) + new_memory.get("hechos", [])))[-20:],
            "estado_emocional_reciente": new_memory.get("estado_emocional_reciente") or old_memory.get("estado_emocional_reciente", ""),
            "temas_importantes": list(set(old_memory.get("temas_importantes", []) + new_memory.get("temas_importantes", [])))[-10:],
        }
        save_memory(chat_id, merged)
    except Exception as e:
        print(f"[MEMORY ERROR] {e}")

# ─── HANDLER ──────────────────────────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != MY_CHAT_ID:
        return

    user_message = update.message.text
    history = get_history(chat_id)
    history.append({"role": "user", "content": user_message})

    # Construir system prompt con memoria
    memory = get_memory(chat_id)
    memory_block = build_memory_block(memory)
    full_system = SYSTEM_PROMPT + ("\n\n" + memory_block if memory_block else "")

    messages = [{"role": "system", "content": full_system}] + history

    # Pausa realista
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

        # Actualizar memoria cada 6 mensajes
        if len(history) % 6 == 0:
            asyncio.create_task(update_memory(chat_id, history))

    except Exception as e:
        print(f"[ERROR] {e}")
        await update.message.reply_text("...")
        return

    # Múltiples mensajes si hay [PAUSA]
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
    print("Reze está online...")
    app.run_polling()
