from app.models.translation import TranslationRequest
from app.services.pipeline import extract_ek_not, extract_warnings


def build_telegram_result_text(final_translation: str, final_layer_output: str) -> str:
    parts = [final_translation.strip()]
    ek_not = extract_ek_not(final_layer_output)
    warnings = extract_warnings(final_layer_output)

    if ek_not:
        parts.append(f"EK NOT:\n{ek_not}")
    if warnings:
        parts.append(f"WARNINGS:\n{warnings}")

    return "\n\n".join(part for part in parts if part.strip())


def build_failure_text(request: TranslationRequest) -> str:
    if request.status == "quality_failed":
        return (
            "لم أرسل الترجمة لأن فحص الجودة رفض النتيجة.\n"
            "السبب المختصر: الترجمة قد تغيّر معنى النص، خصوصًا في نص حساس.\n"
            "راجع الموقع للاطلاع على التفاصيل."
        )

    reason = (request.error or "خطأ غير معروف").strip()
    if len(reason) > 600:
        reason = f"{reason[:600].rstrip()}..."
    return f"تعذرت الترجمة. السبب: {reason}"
