import html
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.models.translation import TranslationLayerResult, TranslationRequest
from app.services.layers import LAYER_DEFINITIONS
from app.services.pipeline import TranslationPipeline
from app.services.translations import create_translation_request
from app.utils import chunk_text

logger = logging.getLogger(__name__)

AR_TO_TR = "ar_to_tr"
TR_TO_AR = "tr_to_ar"
WAITING_DIRECTION_KEY = "waiting_direction"
SELECTED_MODEL_KEY = "selected_model"
MODEL_MENU = "model_menu"
MODEL_PREFIX = "model:"
BTN_AR_TO_TR = "عربي -> تركي"
BTN_TR_TO_AR = "تركي -> عربي"
BTN_MODEL = "اختيار النموذج"
BTN_GUIDE = "الدليل"

MODEL_OPTIONS = [
    ("qwen/qwen3-235b-a22b-2507", "Qwen 3 235B - جودة عالية ورخيص"),
    ("deepseek/deepseek-v3.2", "DeepSeek V3.2 - قوي اقتصادي"),
    ("google/gemini-2.5-flash-lite", "Gemini Flash Lite - سريع ورخيص"),
    ("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet - جودة أعلى وتكلفة أعلى"),
]


def model_label(model_id: str) -> str:
    for option_id, label in MODEL_OPTIONS:
        if option_id == model_id:
            return label
    return model_id


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_AR_TO_TR), KeyboardButton(BTN_TR_TO_AR)],
            [KeyboardButton(BTN_MODEL), KeyboardButton(BTN_GUIDE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("عربي -> تركي", callback_data=AR_TO_TR),
                InlineKeyboardButton("تركي -> عربي", callback_data=TR_TO_AR),
            ],
            [
                InlineKeyboardButton("اختيار النموذج", callback_data=MODEL_MENU),
                InlineKeyboardButton("الدليل", callback_data="guide"),
            ],
        ]
    )


def model_keyboard(selected_model: str) -> InlineKeyboardMarkup:
    buttons = []
    for model_id, label in MODEL_OPTIONS:
        marker = "✓ " if model_id == selected_model else ""
        buttons.append([InlineKeyboardButton(f"{marker}{label}", callback_data=f"{MODEL_PREFIX}{model_id}")])
    buttons.append([InlineKeyboardButton("رجوع", callback_data="back_to_start")])
    return InlineKeyboardMarkup(buttons)


GUIDE_TEXT = """
دليل بوت الترجمة العربي-التركي:

طريقة الاستخدام:
1. اضغط /start.
2. إذا أردت، اضغط "اختيار النموذج" واختر المودل المناسب.
3. اختر اتجاه الترجمة: عربي -> تركي أو تركي -> عربي.
4. أرسل النص كما هو، حتى لو كان طويلا أو فيه تعبيرات عامية.
5. انتظر حتى تمر الترجمة على الطبقات اللغوية.
6. ستصلك النتيجة النهائية داخل مربع قابل للنسخ.

النماذج المتاحة:
- Qwen 3 235B: الخيار الموصى به للجودة العالية مع تكلفة منخفضة.
- DeepSeek V3.2: خيار قوي واقتصادي للنصوص التي تحتاج تفكير أعمق.
- Gemini Flash Lite: خيار سريع ورخيص عندما تكون السرعة أهم.
- Claude 3.5 Sonnet: خيار جودة أعلى لكنه أغلى، مناسب للنصوص الحساسة أو الصعبة.

كيف تعمل الطبقات؟

1. طبقة النية والسياق:
تحدد المقصود من النص قبل الترجمة. تفرق بين النص الرسمي، الرسالة اليومية، السؤال، الإعلان، التعليمات، أو الكلام العاطفي. الهدف أن لا تكون الترجمة حرفية إذا كان المعنى يحتاج أسلوبا مختلفا.

2. طبقة المعنى والمفردات:
تفكك الكلمات المهمة وتختار أقرب معنى لها حسب السياق. هذه الطبقة تنتبه للكلمات التي لها أكثر من معنى، وتمنع اختيار ترجمة صحيحة لغويا لكنها غير مناسبة للمقام.

3. طبقة النحو والصرف:
تراجع ترتيب الجملة، الأزمنة، الضمائر، الإفراد والجمع، وأثر اللواحق في التركية أو التراكيب في العربية. الهدف أن تكون الجملة طبيعية في اللغة الهدف، وليست مجرد نقل كلمة بكلمة.

4. طبقة الثقافة والتعبيرات:
تتعامل مع الأمثال، المجاملات، العبارات الدينية، التعابير اليومية، والكلمات التي لا تترجم حرفيا. إذا كان التعبير يحتاج مقابلا ثقافيا أقرب، تختاره بدلا من ترجمة جامدة.

5. طبقة الترجمة الأولية:
تبني نسخة مترجمة كاملة اعتمادا على نتائج الطبقات السابقة. هذه ليست النتيجة النهائية، لكنها المسودة الأساسية التي تجمع المعنى والسياق والأسلوب في نص واحد.

6. طبقة المراجعة المتخصصة:
تفحص المسودة وتصحح الركاكة، الغموض، الأخطاء الأسلوبية، والتراكيب غير الطبيعية. تركز على أن تبدو الترجمة كأنها مكتوبة أصلا باللغة الهدف.

7. طبقة الحكم النهائي:
تختار الصياغة النهائية وتزيل التردد أو البدائل غير اللازمة. في هذه المرحلة يتم تسليم ترجمة واحدة واضحة ومناسبة، مع الحفاظ على المعنى والنبرة قدر الإمكان.

نصيحة:
إذا كان النص له سياق خاص، اكتبه مع الرسالة مثل: "رسالة رسمية"، "رد واتساب"، "نص تسويقي"، أو "ترجمة حرفية مطلوبة". هذا يساعد الطبقات على اختيار الأسلوب الصحيح.
""".strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        settings: Settings = context.application.bot_data["settings"]
        selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
        await update.message.reply_text(
            f"اختر اتجاه الترجمة:\nالنموذج الحالي: {model_label(selected_model)}",
            reply_markup=main_keyboard(),
        )


async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(GUIDE_TEXT, reply_markup=main_keyboard())


async def show_model_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    settings: Settings = context.application.bot_data["settings"]
    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
    await update.message.reply_text(
        f"اختر نموذج الترجمة:\nالحالي: {model_label(selected_model)}",
        reply_markup=model_keyboard(selected_model),
    )


async def choose_direction(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str) -> None:
    if not update.message:
        return
    settings: Settings = context.application.bot_data["settings"]
    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
    context.user_data[WAITING_DIRECTION_KEY] = direction
    label = "العربية إلى التركية" if direction == AR_TO_TR else "التركية إلى العربية"
    await update.message.reply_text(
        f"تم اختيار الترجمة من {label}.\nالنموذج: {model_label(selected_model)}\nأرسل النص الآن.",
        reply_markup=main_keyboard(),
    )


def render_progress_text(request: TranslationRequest, layers: list[TranslationLayerResult], model: str) -> str:
    by_position = {layer.position: layer for layer in layers}
    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
    }
    lines = [
        "حالة الترجمة عبر الطبقات السبع:",
        f"النموذج: {model_label(model)}",
        f"طلب #{request.id}",
        "",
    ]
    for definition in LAYER_DEFINITIONS:
        layer = by_position.get(definition.position)
        status = layer.status if layer else "pending"
        icon = status_icons.get(status, "•")
        duration = f" - {layer.duration_ms}ms" if layer and layer.duration_ms else ""
        lines.append(f"{icon} {definition.position}. {definition.name}{duration}")
    lines.append("")
    if request.status == "completed":
        lines.append("اكتملت كل الطبقات. أرسل لك الترجمة النهائية الآن.")
    elif request.status == "failed":
        lines.append(f"تعذر إكمال الترجمة: {request.error or 'خطأ غير معروف'}")
    else:
        lines.append("لا تقفل المحادثة. سيتم تحديث هذه الرسالة بعد كل طبقة.")
    return "\n".join(lines)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if query.data == "guide":
        await query.message.reply_text(GUIDE_TEXT, reply_markup=main_keyboard())
        return

    settings: Settings = context.application.bot_data["settings"]
    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)

    if query.data == MODEL_MENU:
        await query.message.reply_text(
            f"اختر نموذج الترجمة:\nالحالي: {model_label(selected_model)}",
            reply_markup=model_keyboard(selected_model),
        )
        return

    if query.data and query.data.startswith(MODEL_PREFIX):
        model_id = query.data.removeprefix(MODEL_PREFIX)
        allowed_models = {option_id for option_id, _ in MODEL_OPTIONS}
        if model_id not in allowed_models:
            await query.message.reply_text("النموذج غير معروف. اختر من القائمة.")
            return
        context.user_data[SELECTED_MODEL_KEY] = model_id
        await query.message.reply_text(
            f"تم اختيار النموذج:\n{model_label(model_id)}\n\nاختر اتجاه الترجمة الآن:",
            reply_markup=main_keyboard(),
        )
        return

    if query.data == "back_to_start":
        await query.message.reply_text(
            f"اختر اتجاه الترجمة:\nالنموذج الحالي: {model_label(selected_model)}",
            reply_markup=main_keyboard(),
        )
        return

    if query.data not in {AR_TO_TR, TR_TO_AR}:
        await query.message.reply_text("اختيار غير معروف. اضغط /start للمحاولة من جديد.")
        return

    context.user_data[WAITING_DIRECTION_KEY] = query.data
    label = "العربية إلى التركية" if query.data == AR_TO_TR else "التركية إلى العربية"
    await query.message.reply_text(
        f"تم اختيار الترجمة من {label}.\nالنموذج: {model_label(selected_model)}\nأرسل النص الآن."
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    menu_text = update.message.text.strip()
    if menu_text == BTN_GUIDE:
        await guide(update, context)
        return
    if menu_text == BTN_MODEL:
        await show_model_menu(update, context)
        return
    if menu_text == BTN_AR_TO_TR:
        await choose_direction(update, context, AR_TO_TR)
        return
    if menu_text == BTN_TR_TO_AR:
        await choose_direction(update, context, TR_TO_AR)
        return

    direction = context.user_data.get(WAITING_DIRECTION_KEY)
    if not direction:
        await update.message.reply_text("اختر اتجاه الترجمة أولا من الأزرار:", reply_markup=main_keyboard())
        return

    source_text = update.message.text.strip()
    if not source_text:
        await update.message.reply_text("النص فارغ. أرسل نصا واضحا للترجمة.")
        return

    context.user_data.pop(WAITING_DIRECTION_KEY, None)
    status_message = await update.message.reply_text(
        "بدأت معالجة النص. سأعرض لك حالة الطبقات السبع هنا.",
        reply_markup=main_keyboard(),
    )

    session_factory: async_sessionmaker = context.application.bot_data["session_factory"]
    settings: Settings = context.application.bot_data["settings"]
    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
    pipeline = TranslationPipeline(settings)

    async def update_progress(request: TranslationRequest, layers: list[TranslationLayerResult]) -> None:
        try:
            await status_message.edit_text(render_progress_text(request, layers, selected_model))
        except Exception as exc:
            logger.warning("Could not update translation progress message: %s", exc)

    async with session_factory() as session:
        request = await create_translation_request(
            session=session,
            direction=direction,
            source_text=source_text,
            telegram_user_id=update.effective_user.id if update.effective_user else None,
            telegram_chat_id=update.effective_chat.id if update.effective_chat else None,
        )
        await update_progress(request, [])
        request = await pipeline.run(session, request, model=selected_model, on_progress=update_progress)

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
