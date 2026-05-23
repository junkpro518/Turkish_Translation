import asyncio
import html
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.models.translation import TranslationLayerResult, TranslationRequest
from app.services.layers import (
    LAYER_DEFINITIONS,
    TRANSLATION_MODE_AUTO,
    TRANSLATION_MODE_COMIC,
    TRANSLATION_MODE_GENERAL,
    TRANSLATION_MODE_LEGAL,
    TRANSLATION_MODE_LITERARY,
    TRANSLATION_MODE_MARKETING,
    TRANSLATION_MODE_SACRED,
    mode_label,
)
from app.services.pipeline import TranslationPipeline, extract_ek_not, extract_warnings
from app.services.translations import count_failed_translation_data, create_translation_request, delete_failed_translation_data
from app.utils import chunk_text

logger = logging.getLogger(__name__)

AR_TO_TR = "ar_to_tr"
TR_TO_AR = "tr_to_ar"
WAITING_DIRECTION_KEY = "waiting_direction"
SELECTED_MODEL_KEY = "selected_model"
SELECTED_MODE_KEY = "selected_translation_mode"
MODEL_MENU = "model_menu"
MODEL_PREFIX = "model:"
MODE_MENU = "mode_menu"
MODE_PREFIX = "mode:"
BTN_AR_TO_TR = "عربي -> تركي"
BTN_TR_TO_AR = "تركي -> عربي"
BTN_MODEL = "اختيار النموذج"
BTN_MODE = "نوع النص"
BTN_GUIDE = "الدليل"
BTN_CLEAR_FAILED = "تنظيف الطلبات الفاشلة"
CLEAR_FAILED_CONFIRMATION = "DELETE FAILED"
CLEAR_FAILED_PENDING_KEY = "clear_failed_pending"
TELEGRAM_SEND_TIMEOUT_SECONDS = 12
FINAL_TRANSLATION_CHUNK_LIMIT = 3400

MODEL_OPTIONS = [
    ("qwen/qwen3-235b-a22b-2507", "Qwen 3 235B - جودة عالية ورخيص"),
    ("deepseek/deepseek-v3.2", "DeepSeek V3.2 - قوي اقتصادي"),
    ("google/gemini-2.5-flash-lite", "Gemini Flash Lite - سريع ورخيص"),
    ("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet - جودة أعلى وتكلفة أعلى"),
]

MODE_OPTIONS = [
    (TRANSLATION_MODE_AUTO, "تلقائي - يحدد النظام النوع"),
    (TRANSLATION_MODE_GENERAL, "عام"),
    (TRANSLATION_MODE_COMIC, "كوميكس / مانجا / حوار"),
    (TRANSLATION_MODE_SACRED, "نص ديني حساس"),
    (TRANSLATION_MODE_LEGAL, "قانوني"),
    (TRANSLATION_MODE_LITERARY, "أدبي"),
    (TRANSLATION_MODE_MARKETING, "تسويقي"),
]


def model_label(model_id: str) -> str:
    for option_id, label in MODEL_OPTIONS:
        if option_id == model_id:
            return label
    return model_id


def parse_admin_user_ids(settings: Settings) -> set[int]:
    ids: set[int] = set()
    for raw_id in settings.telegram_admin_user_ids.replace(";", ",").split(","):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        try:
            ids.add(int(raw_id))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_ADMIN_USER_IDS entry: %s", raw_id)
    return ids


def is_telegram_admin(settings: Settings, user_id: int | None) -> bool:
    return user_id is not None and user_id in parse_admin_user_ids(settings)


def main_keyboard(settings: Settings | None = None, user_id: int | None = None) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(BTN_AR_TO_TR), KeyboardButton(BTN_TR_TO_AR)],
        [KeyboardButton(BTN_MODEL), KeyboardButton(BTN_MODE)],
        [KeyboardButton(BTN_GUIDE)],
    ]
    if settings is not None and is_telegram_admin(settings, user_id):
        rows.append([KeyboardButton(BTN_CLEAR_FAILED)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("عربي -> تركي", callback_data=AR_TO_TR),
                InlineKeyboardButton("تركي -> عربي", callback_data=TR_TO_AR),
            ],
            [
                InlineKeyboardButton("اختيار النموذج", callback_data=MODEL_MENU),
                InlineKeyboardButton("نوع النص", callback_data=MODE_MENU),
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


def mode_keyboard(selected_mode: str) -> InlineKeyboardMarkup:
    buttons = []
    for mode_id, label in MODE_OPTIONS:
        marker = "✓ " if mode_id == selected_mode else ""
        buttons.append([InlineKeyboardButton(f"{marker}{label}", callback_data=f"{MODE_PREFIX}{mode_id}")])
    buttons.append([InlineKeyboardButton("رجوع", callback_data="back_to_start")])
    return InlineKeyboardMarkup(buttons)


async def safe_reply_text(message, text: str, **kwargs):
    try:
        return await asyncio.wait_for(
            message.reply_text(text, **kwargs),
            timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("Could not send Telegram reply: %s", exc)
        return None


async def safe_edit_text(message, text: str, **kwargs) -> bool:
    try:
        await asyncio.wait_for(
            message.edit_text(text, **kwargs),
            timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:
        logger.warning("Could not edit Telegram message: %s", exc)
        return False


async def safe_send_text(context: ContextTypes.DEFAULT_TYPE, chat_id: int | None, text: str, **kwargs) -> bool:
    if chat_id is None:
        logger.warning("Could not send Telegram message: missing chat_id")
        return False
    try:
        await asyncio.wait_for(
            context.bot.send_message(chat_id=chat_id, text=text, **kwargs),
            timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:
        logger.warning("Could not send Telegram message: %s", exc)
        return False


GUIDE_TEXT = """
دليل بوت الترجمة العربي-التركي:

طريقة الاستخدام:
1. اضغط /start.
2. إذا أردت، اضغط "اختيار النموذج" واختر المودل المناسب.
3. إذا أردت، اضغط "نوع النص" واختر: تلقائي، عام، كوميكس، ديني حساس، قانوني، أدبي، أو تسويقي.
4. اختر اتجاه الترجمة: عربي -> تركي أو تركي -> عربي.
5. أرسل النص كما هو، حتى لو كان طويلا أو فيه تعبيرات عامية.
6. انتظر حتى تمر الترجمة على طبقات التحليل والمراجعة.
7. ستصلك النتيجة النهائية داخل مربع قابل للنسخ.

النماذج المتاحة:
- Qwen 3 235B: الخيار الموصى به للجودة العالية مع تكلفة منخفضة.
- DeepSeek V3.2: خيار قوي واقتصادي للنصوص التي تحتاج تفكير أعمق.
- Gemini Flash Lite: خيار سريع ورخيص عندما تكون السرعة أهم.
- Claude 3.5 Sonnet: خيار جودة أعلى لكنه أغلى، مناسب للنصوص الحساسة أو الصعبة.

أنواع النصوص:
- تلقائي: يحدد النظام النوع المناسب قبل الترجمة.
- عام: رسائل ونصوص عادية.
- كوميكس / مانجا / حوار: يجعل الجمل أقصر وأكثر طبيعية داخل الفقاعات ويحافظ على النبرة والشخصية.
- نص ديني حساس: يحافظ على الأمانة ولا يضيف تفسيرًا داخل الترجمة.
- قانوني: يعطي الأولوية للدقة والقيود والاستثناءات.
- أدبي: يحافظ على الأسلوب والجمال دون خيانة المعنى.
- تسويقي: يحسن الأسلوب دون تغيير الوعد أو السعر أو المزايا.

قواعد النصوص الدينية:
- لا يختصر العبارات التشريفية والدعائية مثل: عز وجل، سبحانه وتعالى، صلى الله عليه وسلم، رضي الله عنه، رضي الله عنهما، رحمه الله.
- لا يستخدم اختصارات مثل s.a.v. أو r.a. أو cc أو rh.a. إلا إذا طلبت ذلك صراحة.
- يكتب العبارات كاملة في التركية، مثل:
  صلى الله عليه وسلم = sallallahu aleyhi ve sellem
  رضي الله عنه = radıyallahu anh
  رضي الله عنهما = radıyallahu anhüma
  عز وجل = azze ve celle
  سبحانه وتعالى = sübhânehu ve teâlâ
  رحمه الله = rahimehullah
- إذا كان النص يحتوي حديثًا أو آية أو دعاء ثم أضفت تعليقًا أو فائدة من عندك، لا يدمجها داخل المتن الأصلي.
- في النصوص الدينية يظهر قسم EK NOT فقط إذا كان هناك تعليق أو فائدة خارج المتن الأصلي.

شكل مخرجات النصوص الدينية:
FINAL_TRANSLATION:
[ترجمة النص الأصلي فقط]

EK NOT:
[تعليق أو فائدة خارج النص الأصلي. لا يظهر هذا القسم إذا لا يوجد تعليق إضافي]

BRIEF_REASON:
[سبب مختصر]

WARNINGS:
[تحذيرات أو: لا يوجد]

كيف تعمل الطبقات؟

1. طبقة تصنيف النص وسياسة الترجمة:
تحدد نوع النص وسياسة الترجمة المناسبة، مثل كوميكس، نص ديني حساس، قانوني، أدبي، تسويقي، أو عام. إذا اخترت النوع يدويا يحترمه النظام، ومع ذلك يضيف حماية خاصة للأجزاء الدينية الحساسة داخل أي نص.

2. طبقة النية والسياق:
تحدد المقصود من النص قبل الترجمة. تفرق بين النص الرسمي، الرسالة اليومية، السؤال، الإعلان، التعليمات، أو الكلام العاطفي. الهدف أن لا تكون الترجمة حرفية إذا كان المعنى يحتاج أسلوبا مختلفا.

3. طبقة المعنى والمفردات:
تفكك الكلمات المهمة وتختار أقرب معنى لها حسب السياق. هذه الطبقة تنتبه للكلمات التي لها أكثر من معنى، وتمنع اختيار ترجمة صحيحة لغويا لكنها غير مناسبة للمقام.

4. طبقة النحو والصرف:
تراجع ترتيب الجملة، الأزمنة، الضمائر، الإفراد والجمع، وأثر اللواحق في التركية أو التراكيب في العربية. الهدف أن تكون الجملة طبيعية في اللغة الهدف، وليست مجرد نقل كلمة بكلمة.

5. طبقة الثقافة والتعبيرات:
تتعامل مع الأمثال، المجاملات، العبارات الدينية، التعابير اليومية، والكلمات التي لا تترجم حرفيا. في النصوص الدينية تمنع التكييف الحر وتفصل التعليقات عن المتن الأصلي.

6. طبقة الترجمة الأولية:
تبني نسخة مترجمة كاملة اعتمادا على نتائج الطبقات السابقة. هذه ليست النتيجة النهائية، لكنها المسودة الأساسية التي تجمع المعنى والسياق والأسلوب في نص واحد.

7. طبقة المراجعة المتخصصة:
تفحص المسودة وتصحح الركاكة، الغموض، الأخطاء الأسلوبية، والتراكيب غير الطبيعية. تركز على أن تبدو الترجمة كأنها مكتوبة أصلا باللغة الهدف.

8. طبقة الحكم النهائي:
تختار الصياغة النهائية وتزيل التردد أو البدائل غير اللازمة. في هذه المرحلة يتم تسليم ترجمة واحدة واضحة ومناسبة، مع الحفاظ على المعنى والنبرة قدر الإمكان.

نصيحة:
إذا كان النص له سياق خاص، اختر نوع النص من زر "نوع النص"، أو اكتبه مع الرسالة مثل: "رسالة رسمية"، "رد واتساب"، "نص تسويقي"، أو "ترجمة حرفية مطلوبة".
""".strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        settings: Settings = context.application.bot_data["settings"]
        selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
        selected_mode = context.user_data.get(SELECTED_MODE_KEY, TRANSLATION_MODE_AUTO)
        await update.message.reply_text(
            f"اختر اتجاه الترجمة:\nالنموذج الحالي: {model_label(selected_model)}\nنوع النص: {mode_label(selected_mode)}",
            reply_markup=main_keyboard(settings, update.effective_user.id if update.effective_user else None),
        )


async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        settings: Settings = context.application.bot_data["settings"]
        await update.message.reply_text(
            GUIDE_TEXT,
            reply_markup=main_keyboard(settings, update.effective_user.id if update.effective_user else None),
        )


async def show_model_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    settings: Settings = context.application.bot_data["settings"]
    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
    await update.message.reply_text(
        f"اختر نموذج الترجمة:\nالحالي: {model_label(selected_model)}",
        reply_markup=model_keyboard(selected_model),
    )


async def show_mode_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    selected_mode = context.user_data.get(SELECTED_MODE_KEY, TRANSLATION_MODE_AUTO)
    await update.message.reply_text(
        f"اختر نوع النص:\nالحالي: {mode_label(selected_mode)}",
        reply_markup=mode_keyboard(selected_mode),
    )


async def choose_direction(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str) -> None:
    if not update.message:
        return
    settings: Settings = context.application.bot_data["settings"]
    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
    selected_mode = context.user_data.get(SELECTED_MODE_KEY, TRANSLATION_MODE_AUTO)
    context.user_data[WAITING_DIRECTION_KEY] = direction
    label = "العربية إلى التركية" if direction == AR_TO_TR else "التركية إلى العربية"
    await update.message.reply_text(
        f"تم اختيار الترجمة من {label}.\nالنموذج: {model_label(selected_model)}\nنوع النص: {mode_label(selected_mode)}\nأرسل النص الآن.",
        reply_markup=main_keyboard(settings, update.effective_user.id if update.effective_user else None),
    )


async def show_clear_failed_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_telegram_admin(settings, user_id):
        await update.message.reply_text("هذا الإجراء متاح للإداري فقط.")
        return

    session_factory: async_sessionmaker = context.application.bot_data["session_factory"]
    async with session_factory() as session:
        request_count, layer_count = await count_failed_translation_data(session)

    if request_count == 0:
        await update.message.reply_text(
            "لا توجد طلبات فاشلة لتنظيفها.",
            reply_markup=main_keyboard(settings, user_id),
        )
        return

    context.user_data[CLEAR_FAILED_PENDING_KEY] = True
    await update.message.reply_text(
        "سيتم حذف الطلبات الفاشلة فقط من قاعدة البيانات.\n"
        f"عدد الطلبات: {request_count}\n"
        f"عدد الطبقات: {layer_count}\n\n"
        f"للتأكيد اكتب بالضبط:\n{CLEAR_FAILED_CONFIRMATION}\n\n"
        "أي رد آخر سيلغي العملية.",
        reply_markup=main_keyboard(settings, user_id),
    )


async def handle_clear_failed_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if not context.user_data.get(CLEAR_FAILED_PENDING_KEY):
        return False
    context.user_data.pop(CLEAR_FAILED_PENDING_KEY, None)
    if not update.message:
        return True

    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_telegram_admin(settings, user_id):
        await update.message.reply_text("تم إلغاء العملية. هذا الإجراء متاح للإداري فقط.")
        return True

    if text.strip() != CLEAR_FAILED_CONFIRMATION:
        await update.message.reply_text(
            "تم إلغاء تنظيف قاعدة البيانات.",
            reply_markup=main_keyboard(settings, user_id),
        )
        return True

    session_factory: async_sessionmaker = context.application.bot_data["session_factory"]
    async with session_factory() as session:
        request_count, layer_count = await delete_failed_translation_data(session)

    logger.info(
        "failed_translation_cleanup user_id=%s requests_deleted=%s layers_deleted=%s at=%s",
        user_id,
        request_count,
        layer_count,
        datetime.now(timezone.utc).isoformat(),
    )
    await update.message.reply_text(
        f"تم تنظيف الطلبات الفاشلة.\nالطلبات المحذوفة: {request_count}\nالطبقات المحذوفة: {layer_count}",
        reply_markup=main_keyboard(settings, user_id),
    )
    return True


def render_progress_text(
    request: TranslationRequest,
    layers: list[TranslationLayerResult],
    model: str,
    translation_mode: str,
    verbose: bool = False,
    running_elapsed_secs: int | None = None,
) -> str:
    if not verbose:
        running_layer = next((layer for layer in layers if layer.status == "running"), None)
        if request.status == "completed":
            status_line = "تم تجهيز الترجمة."
        elif request.status == "failed":
            status_line = f"تعذر إكمال الترجمة: {request.error or 'خطأ غير معروف'}"
        elif running_layer and running_layer.position <= 3:
            status_line = "جاري تحليل النص..."
        elif running_layer and running_layer.position <= 6:
            status_line = "جاري الترجمة..."
        elif running_layer:
            status_line = "جاري المراجعة..."
        else:
            status_line = "جاري تجهيز الطلب..."

        elapsed = f"\nيعمل منذ {running_elapsed_secs} ثانية" if running_elapsed_secs is not None else ""
        return (
            f"{status_line}\n"
            f"النموذج: {model_label(model)}\n"
            f"نوع النص: {mode_label(translation_mode)}\n"
            f"طلب #{request.id}"
            f"{elapsed}"
        )

    by_position = {layer.position: layer for layer in layers}
    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
    }
    lines = [
        f"حالة الترجمة عبر {len(LAYER_DEFINITIONS)} طبقات:",
        f"النموذج: {model_label(model)}",
        f"نوع النص: {mode_label(translation_mode)}",
        f"طلب #{request.id}",
        "",
    ]
    for definition in LAYER_DEFINITIONS:
        layer = by_position.get(definition.position)
        status = layer.status if layer else "pending"
        icon = status_icons.get(status, "•")
        duration = f" - {layer.duration_ms}ms" if layer and layer.duration_ms else ""
        elapsed = ""
        if layer and layer.status == "running" and running_elapsed_secs is not None:
            elapsed = f" - يعمل منذ {running_elapsed_secs} ثانية"
        lines.append(f"{icon} {definition.position}. {definition.name}{duration}{elapsed}")
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
        settings: Settings = context.application.bot_data["settings"]
        await query.message.reply_text(
            GUIDE_TEXT,
            reply_markup=main_keyboard(settings, query.from_user.id if query.from_user else None),
        )
        return

    settings: Settings = context.application.bot_data["settings"]
    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
    selected_mode = context.user_data.get(SELECTED_MODE_KEY, TRANSLATION_MODE_AUTO)

    if query.data == MODEL_MENU:
        await query.message.reply_text(
            f"اختر نموذج الترجمة:\nالحالي: {model_label(selected_model)}",
            reply_markup=model_keyboard(selected_model),
        )
        return

    if query.data == MODE_MENU:
        await query.message.reply_text(
            f"اختر نوع النص:\nالحالي: {mode_label(selected_mode)}",
            reply_markup=mode_keyboard(selected_mode),
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
            reply_markup=main_keyboard(settings, query.from_user.id if query.from_user else None),
        )
        return

    if query.data and query.data.startswith(MODE_PREFIX):
        mode_id = query.data.removeprefix(MODE_PREFIX)
        allowed_modes = {option_id for option_id, _ in MODE_OPTIONS}
        if mode_id not in allowed_modes:
            await query.message.reply_text("نوع النص غير معروف. اختر من القائمة.")
            return
        context.user_data[SELECTED_MODE_KEY] = mode_id
        await query.message.reply_text(
            f"تم اختيار نوع النص:\n{mode_label(mode_id)}\n\nاختر اتجاه الترجمة الآن:",
            reply_markup=main_keyboard(settings, query.from_user.id if query.from_user else None),
        )
        return

    if query.data == "back_to_start":
        await query.message.reply_text(
            f"اختر اتجاه الترجمة:\nالنموذج الحالي: {model_label(selected_model)}\nنوع النص: {mode_label(selected_mode)}",
            reply_markup=main_keyboard(settings, query.from_user.id if query.from_user else None),
        )
        return

    if query.data not in {AR_TO_TR, TR_TO_AR}:
        await query.message.reply_text("اختيار غير معروف. اضغط /start للمحاولة من جديد.")
        return

    context.user_data[WAITING_DIRECTION_KEY] = query.data
    label = "العربية إلى التركية" if query.data == AR_TO_TR else "التركية إلى العربية"
    await query.message.reply_text(
        f"تم اختيار الترجمة من {label}.\nالنموذج: {model_label(selected_model)}\nنوع النص: {mode_label(selected_mode)}\nأرسل النص الآن."
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    menu_text = update.message.text.strip()
    if await handle_clear_failed_confirmation(update, context, menu_text):
        return

    if menu_text == BTN_GUIDE:
        await guide(update, context)
        return
    if menu_text == BTN_CLEAR_FAILED:
        await show_clear_failed_prompt(update, context)
        return
    if menu_text == BTN_MODEL:
        await show_model_menu(update, context)
        return
    if menu_text == BTN_MODE:
        await show_mode_menu(update, context)
        return
    if menu_text == BTN_AR_TO_TR:
        await choose_direction(update, context, AR_TO_TR)
        return
    if menu_text == BTN_TR_TO_AR:
        await choose_direction(update, context, TR_TO_AR)
        return

    direction = context.user_data.get(WAITING_DIRECTION_KEY)
    if not direction:
        settings: Settings = context.application.bot_data["settings"]
        await update.message.reply_text(
            "اختر اتجاه الترجمة أولا من الأزرار:",
            reply_markup=main_keyboard(settings, update.effective_user.id if update.effective_user else None),
        )
        return

    source_text = update.message.text.strip()
    if not source_text:
        await update.message.reply_text("النص فارغ. أرسل نصا واضحا للترجمة.")
        return

    context.user_data.pop(WAITING_DIRECTION_KEY, None)
    session_factory: async_sessionmaker = context.application.bot_data["session_factory"]
    settings: Settings = context.application.bot_data["settings"]
    status_message = await safe_reply_text(
        update.message,
        "بدأت معالجة النص. سأرسل لك تحديثًا مختصرًا ثم الترجمة النهائية.",
        reply_markup=main_keyboard(settings, update.effective_user.id if update.effective_user else None),
    )

    selected_model = context.user_data.get(SELECTED_MODEL_KEY, settings.openrouter_model)
    selected_mode = context.user_data.get(SELECTED_MODE_KEY, TRANSLATION_MODE_AUTO)
    pipeline = TranslationPipeline(settings)
    verbose_progress = settings.telegram_verbose_mode and is_telegram_admin(
        settings, update.effective_user.id if update.effective_user else None
    )
    heartbeat_task: asyncio.Task | None = None
    heartbeat_position: int | None = None
    heartbeat_started_at = 0.0
    last_progress_text = ""
    final_layer_output = ""

    async def heartbeat(request: TranslationRequest, layers: list[TranslationLayerResult]) -> None:
        while True:
            await asyncio.sleep(20)
            if status_message is None:
                continue
            elapsed = int(time.monotonic() - heartbeat_started_at)
            await safe_edit_text(
                status_message,
                render_progress_text(
                    request,
                    layers,
                    selected_model,
                    selected_mode,
                    verbose_progress,
                    elapsed,
                ),
            )

    async def update_progress(request: TranslationRequest, layers: list[TranslationLayerResult]) -> None:
        nonlocal heartbeat_task, heartbeat_position, heartbeat_started_at, last_progress_text
        running_layer = next((layer for layer in layers if layer.status == "running"), None)
        if running_layer:
            if heartbeat_position != running_layer.position:
                if heartbeat_task:
                    heartbeat_task.cancel()
                heartbeat_position = running_layer.position
                heartbeat_started_at = time.monotonic()
                heartbeat_task = asyncio.create_task(heartbeat(request, layers))
        else:
            if heartbeat_task:
                heartbeat_task.cancel()
                heartbeat_task = None
            heartbeat_position = None
        if status_message is not None:
            progress_text = render_progress_text(
                request,
                layers,
                selected_model,
                selected_mode,
                verbose_progress,
            )
            if progress_text != last_progress_text:
                last_progress_text = progress_text
                await safe_edit_text(status_message, progress_text)

    async with session_factory() as session:
        request = await create_translation_request(
            session=session,
            direction=direction,
            source_text=source_text,
            telegram_user_id=update.effective_user.id if update.effective_user else None,
            telegram_chat_id=update.effective_chat.id if update.effective_chat else None,
        )
        await update_progress(request, [])
        request = await pipeline.run(
            session,
            request,
            model=selected_model,
            translation_mode=selected_mode,
            on_progress=update_progress,
        )
        await session.refresh(request, ["layers"])
        final_layer = max(request.layers, key=lambda layer: layer.position, default=None)
        final_layer_output = final_layer.output_text if final_layer and final_layer.output_text else request.final_translation or ""

    if heartbeat_task:
        heartbeat_task.cancel()

    if request.status == "completed" and request.final_translation:
        if status_message is not None:
            await safe_edit_text(status_message, "اكتملت الترجمة. النتيجة:")
        await send_copyable_translation(update, context, build_telegram_result_text(request.final_translation, final_layer_output))
    elif request.status == "quality_failed":
        failure_text = "تعذّر إرسال الترجمة لأن مراجعة الجودة وجدت احتمال تغيّر واضح في المعنى. تم حفظ الطلب للمراجعة."
        if status_message is not None:
            await safe_edit_text(status_message, failure_text)
        else:
            await safe_send_text(context, update.effective_chat.id if update.effective_chat else None, failure_text)
    else:
        failure_text = f"تعذرت الترجمة. السبب: {request.error or 'خطأ غير معروف'}"
        if status_message is not None:
            await safe_edit_text(status_message, failure_text)
        else:
            await safe_send_text(context, update.effective_chat.id if update.effective_chat else None, failure_text)


async def send_copyable_translation(update: Update, context: ContextTypes.DEFAULT_TYPE, translation: str) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    for chunk in chunk_text(translation, limit=FINAL_TRANSLATION_CHUNK_LIMIT):
        sent = await safe_send_text(
            context,
            chat_id,
            f"<pre>{html.escape(chunk)}</pre>",
            parse_mode=ParseMode.HTML,
        )
        if not sent:
            await safe_send_text(context, chat_id, chunk)


def build_telegram_result_text(final_translation: str, final_layer_output: str) -> str:
    parts = [final_translation.strip()]
    ek_not = extract_ek_not(final_layer_output)
    warnings = extract_warnings(final_layer_output)

    if ek_not:
        parts.append(f"EK NOT:\n{ek_not}")
    if warnings:
        parts.append(f"WARNINGS:\n{warnings}")

    return "\n\n".join(part for part in parts if part.strip())


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
