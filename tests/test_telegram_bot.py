from types import SimpleNamespace

from app.bot.telegram_bot import build_telegram_result_text, render_progress_text
from app.models.translation import TranslationLayerResult


def test_telegram_progress_is_concise_by_default() -> None:
    request = SimpleNamespace(id=123, status="running", error=None)
    layers = [TranslationLayerResult(position=1, name="تصنيف النص وسياسة الترجمة", status="running")]

    text = render_progress_text(request, layers, "test-model", "sacred")

    assert "جاري تحليل النص" in text
    assert "تصنيف النص وسياسة الترجمة" not in text
    assert "حالة الترجمة عبر" not in text


def test_telegram_result_includes_ek_not_and_warnings_without_reason() -> None:
    final_output = (
        "FINAL_TRANSLATION:\nHadis metni.\n\n"
        "EK NOT:\nBu açıklama metnin aslından değil, ek bir nottur: Ek açıklama.\n\n"
        "BRIEF_REASON:\nTerimler korundu.\n\n"
        "WARNINGS:\nEk not metnin aslından değildir."
    )

    text = build_telegram_result_text("Hadis metni.", final_output)

    assert "Hadis metni." in text
    assert "EK NOT:" in text
    assert "Bu açıklama metnin aslından değil" in text
    assert "WARNINGS:" in text
    assert "BRIEF_REASON" not in text
    assert "Terimler korundu" not in text
