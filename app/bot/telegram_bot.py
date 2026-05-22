import html
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.services.pipeline import TranslationPipeline
from app.services.translations import create_translation_request
from app.utils import chunk_text

logger = logging.getLogger(__name__)

AR_TO_TR = "ar_to_tr"
TR_TO_AR = "tr_to_ar"
WAITING_DIRECTION_KEY = "waiting_direction"


def direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("عربي -> تركي", callback_data=AR_TO_TR),
                InlineKeyboardButton("تركي -> عربي", callback_data=TR_TO_AR),
            ],
            [InlineKeyboardButton("الدليل", callback_data="guide")],
        ]
    )


GUIDE_TEXT = """
دليل بوت الترجمة العربي-التركي:

1. اضغط /start.
2. اختر اتجاه الترجمة من الأزرار.
3. أرسل النص المطلوب ترجمته.
4. انتظر معالجة الطبقات اللغوية.
5. ستصلك الترجمة النهائية داخل مربع قابل للنسخ.

الطبقات:
1. تحليل النية والسياق
2. تحليل المعنى والمفردات والمرادفات
3. تحليل النحو والصرف
4. تحليل الثقافة والتعبيرات الاصطلاحية
5. إنتاج ترجمة أولية
6. مراجعة لغوية متخصصة
7. الحكم النهائي
""".strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("اختر اتجاه الترجمة:", reply_markup=direction_keyboard())


async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(GUIDE_TEXT, reply_markup=direction_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if query.data == "guide":
        await query.message.reply_text(GUIDE_TEXT, reply_markup=direction_keyboard())
        return

    if query.data not in {AR_TO_TR, TR_TO_AR}:
        await query.message.reply_text("اختيار غير معروف. اضغط /start للمحاولة من جديد.")
        return

    context.user_data[WAITING_DIRECTION_KEY] = query.data
    label = "العربية إلى التركية" if query.data == AR_TO_TR else "التركية إلى العربية"
    await query.message.reply_text(f"تم اختيار الترجمة من {label}. أرسل النص الآن.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    direction = context.user_data.get(WAITING_DIRECTION_KEY)
    if not direction:
        await update.message.reply_text("اختر اتجاه الترجمة أولا:", reply_markup=direction_keyboard())
        return

    source_text = update.message.text.strip()
    if not source_text:
        await update.message.reply_text("النص فارغ. أرسل نصا واضحا للترجمة.")
        return

    context.user_data.pop(WAITING_DIRECTION_KEY, None)
    status_message = await update.message.reply_text("جاري معالجة النص عبر الطبقات اللغوية...")

    session_factory: async_sessionmaker = context.application.bot_data["session_factory"]
    settings: Settings = context.application.bot_data["settings"]
    pipeline = TranslationPipeline(settings)

    async with session_factory() as session:
        request = await create_translation_request(
            session=session,
            direction=direction,
            source_text=source_text,
            telegram_user_id=update.effective_user.id if update.effective_user else None,
            telegram_chat_id=update.effective_chat.id if update.effective_chat else None,
        )
        request = await pipeline.run(session, request)

    if request.status == "completed" and request.final_translation:
        await status_message.edit_text("اكتملت الترجمة. النتيجة:")
        await send_copyable_translation(update, request.final_translation)
    else:
        await status_message.edit_text(f"تعذرت الترجمة. السبب: {request.error or 'خطأ غير معروف'}")


async def send_copyable_translation(update: Update, translation: str) -> None:
    for chunk in chunk_text(translation, limit=3800):
        await update.message.reply_text(f"<pre>{html.escape(chunk)}</pre>", parse_mode=ParseMode.HTML)


def build_application(settings: Settings, session_factory: async_sessionmaker) -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["session_factory"] = session_factory
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("guide", guide))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    return app


async def start_polling(app: Application) -> None:
    logger.info("Starting Telegram polling")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()


async def stop_polling(app: Application) -> None:
    logger.info("Stopping Telegram polling")
    if app.updater:
        await app.updater.stop()
    await app.stop()
    await app.shutdown()
