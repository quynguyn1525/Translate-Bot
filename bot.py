import os
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from pydub import AudioSegment
from translate import Translator
from gtts import gTTS
import speech_recognition as sr

load_dotenv()
# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

print(f"Download folder: {DOWNLOADS_DIR.resolve()}")
# ----------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

translator = Translator(to_lang="vi", from_lang="km")


async def cleanup_old_files():
    """Delete files older than 1 day in DOWNLOADS_DIR every hour."""
    while True:
        try:
            now = datetime.now()
            cutoff = now - timedelta(days=1)
            logger.info("Running cleanup task...")

            for file in DOWNLOADS_DIR.iterdir():
                if file.is_file() and datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
                    try:
                        file.unlink()
                        logger.info(f"Deleted old file: {file}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {file}: {e}")
        except Exception as e:
            logger.exception("Error in cleanup task: %s", e)

        await asyncio.sleep(3600)  # run every hour


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ogg_path = wav_path = tts_path = None
    try:
        msg = update.message
        chat_id = update.effective_chat.id

        if not msg.voice:
            await msg.reply_text("Send me a voice message and I'll translate it.")
            return

        tg_file = await msg.voice.get_file()

        # Filenames
        ogg_path = DOWNLOADS_DIR / f"{tg_file.file_id}.ogg"
        wav_path = DOWNLOADS_DIR / f"{tg_file.file_id}.wav"
        tts_path = DOWNLOADS_DIR / f"{tg_file.file_id}_vi.mp3"

        # 1) Download voice message
        await tg_file.download_to_drive(str(ogg_path))
        logger.info(f"Downloaded to {ogg_path}")

        # 2) Convert to WAV (pydub uses ffmpeg)
        audio = AudioSegment.from_file(ogg_path)
        audio.export(wav_path, format="wav")
        logger.info(f"Converted to {wav_path}")

        # 3) Transcribe with Google Speech Recognition
        recognizer = sr.Recognizer()
        with sr.AudioFile(str(wav_path)) as source:
            audio_data = recognizer.record(source)

        try:
            khmer_text = recognizer.recognize_google(audio_data, language="km-KH")
        except sr.UnknownValueError:
            khmer_text = ""
        except sr.RequestError as e:
            khmer_text = f"[Google Speech Recognition error: {e}]"

        logger.info(f"Transcription: {khmer_text!r}")

        if not khmer_text:
            await msg.reply_text("I couldn't transcribe the audio. Try a clearer recording.")
            return

        # 4) Translate Khmer → Vietnamese
        vietnamese_text = translator.translate(khmer_text)
        logger.info(f"Translation: {vietnamese_text!r}")

        # 5) Reply with texts
        reply = f"🇰🇭 Khmer (transcript):\n{khmer_text}\n\n🇻🇳 Vietnamese (translation):\n{vietnamese_text}"
        await msg.reply_text(reply)

        # 6) Vietnamese TTS
        try:
            tts = gTTS(text=vietnamese_text, lang="vi")
            tts.save(str(tts_path))
            with open(tts_path, "rb") as f:
                await context.bot.send_audio(chat_id=chat_id, audio=InputFile(f, filename=tts_path.name),
                                             caption="🔊 Vietnamese (TTS)")
        except Exception as e:
            logger.warning("TTS failed: %s", e)

    except Exception as e:
        logger.exception("Error in handle_voice: %s", e)
        await update.message.reply_text(f"Something went wrong: {e}")

    finally:
        # Auto-cleanup for current files
        for file_path in [ogg_path, wav_path, tts_path]:
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                    logger.info(f"Deleted file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me a voice message (Khmer) and I'll translate it into Vietnamese.")


async def start_cleanup(app):
    asyncio.create_task(cleanup_old_files())


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(start_cleanup).build()

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()