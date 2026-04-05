import httpx
import traceback

# ─── NUEVA FUNCIÓN ASYNC PARA COMPLETAR ───────────────────────────
async def together_complete_async(messages, temperature=0.9, max_tokens=400):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.together.xyz/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {TOGETHER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": TOGETHER_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("choices"):
                raise ValueError("API no devolvió 'choices'")
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[ERROR TOGETHER] {e}")
        traceback.print_exc()
        raise e  # relanza para que el handler lo capture

# ─── HANDLER PRINCIPAL MODIFICADO ────────────────────────────────
async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != MY_CHAT_ID:
        return

    user_message = update.message.text
    set_last_user_msg_time(chat_id)
    redis_client.delete(f"insistio:{chat_id}")

    # Traer toda la historia desde Redis
    history = get_history(chat_id)
    history.append({"role": "user", "content": user_message})

    memory = get_memory(chat_id)
    memory_block = build_memory_block(memory)
    full_system = SYSTEM_PROMPT + ("\n\n" + memory_block if memory_block else "")

    # Limitar los mensajes que enviamos a la API, pero guardar toda la historia en Redis
    messages_to_api = [{"role": "system", "content": full_system}] + history[-10:]

    await asyncio.sleep(random.uniform(1, 3))
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # Llamada async a la API
        respuesta_completa = await together_complete_async(
            messages=messages_to_api,
            temperature=0.9,
            max_tokens=400
        )

        # Guardar toda la historia
        history.append({"role": "assistant", "content": respuesta_completa})
        save_history(chat_id, history)

        # Actualizar memoria cada 6 mensajes
        if len(history) % 6 == 0:
            asyncio.create_task(update_memory(chat_id, history))

    except Exception as e:
        await update.message.reply_text("Hubo un error al generar la respuesta 😅")
        return

    # Dividir la respuesta por [PAUSA] y enviarla en partes
    partes = [p.strip() for p in respuesta_completa.split("[PAUSA]") if p.strip()]
    for i, parte in enumerate(partes):
        if i > 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(random.uniform(1, 2))
        await update.message.reply_text(parte)
