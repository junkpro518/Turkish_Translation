from app.prompts.translation_prompts import SACRED_SYSTEM_PROMPT


def test_sacred_prompt_rejects_religious_abbreviations() -> None:
    assert "لا تختصر" in SACRED_SYSTEM_PROMPT
    assert "s.a.v." in SACRED_SYSTEM_PROMPT
    assert "r.a." in SACRED_SYSTEM_PROMPT
    assert "cc" in SACRED_SYSTEM_PROMPT
    assert "rh.a." in SACRED_SYSTEM_PROMPT
    assert "عز وجل = azze ve celle" in SACRED_SYSTEM_PROMPT
    assert "سبحانه وتعالى = sübhânehu ve teâlâ" in SACRED_SYSTEM_PROMPT
    assert "صلى الله عليه وسلم = sallallahu aleyhi ve sellem" in SACRED_SYSTEM_PROMPT
    assert "رضي الله عنه = radıyallahu anh" in SACRED_SYSTEM_PROMPT
    assert "رضي الله عنهما = radıyallahu anhüma" in SACRED_SYSTEM_PROMPT
    assert "رحمه الله = rahimehullah" in SACRED_SYSTEM_PROMPT


def test_sacred_prompt_separates_external_notes_from_religious_text() -> None:
    assert "EK NOT" in SACRED_SYSTEM_PROMPT
    assert "FINAL_TRANSLATION يجب أن يحتوي على ترجمة النص الأصلي فقط" in SACRED_SYSTEM_PROMPT
    assert "Bu açıklama metnin aslından değil, ek bir nottur" in SACRED_SYSTEM_PROMPT
    assert "لا تحذف هذا التعليق" in SACRED_SYSTEM_PROMPT
    assert "إذا لم يوجد تعليق أو فائدة إضافية من المستخدم، لا تضف قسم EK NOT نهائيًا" in SACRED_SYSTEM_PROMPT
    assert "EK NOT يحتوي على ترجمة تعليق المستخدم أو الفائدة الإضافية فقط" in SACRED_SYSTEM_PROMPT
    assert "sacred_source_text" in SACRED_SYSTEM_PROMPT
    assert "user_extra_note" in SACRED_SYSTEM_PROMPT


def test_sacred_prompt_allows_preserving_prophet_symbol_and_better_exception_wording() -> None:
    assert "يجوز تركه كما هو" in SACRED_SYSTEM_PROMPT
    assert "ﷺ = ﷺ" in SACRED_SYSTEM_PROMPT
    assert "dönmeyene kadar hariç" in SACRED_SYSTEM_PROMPT
    assert "Ancak bir kimse canıyla ve malıyla çıkıp da bunlardan hiçbir şeyle geri dönmezse, o müstesnadır." in SACRED_SYSTEM_PROMPT


def test_sacred_prompt_prefers_accuracy_and_contemporary_religious_turkish() -> None:
    assert "إذا كانت الترجمة الطبيعية قد تُضعف الدقة في النصوص الشرعية، فاختر الدقة" in SACRED_SYSTEM_PROMPT
    assert "لا تستخدم لغة عثمانية قديمة أو ثقيلة" in SACRED_SYSTEM_PROMPT
    assert "amel-i salih" in SACRED_SYSTEM_PROMPT
    assert "cihad-ı fîsebilillah" in SACRED_SYSTEM_PROMPT
    assert "ziyade notu" in SACRED_SYSTEM_PROMPT
    assert "salih amel, Allah yolunda cihad, ek not" in SACRED_SYSTEM_PROMPT
    assert "استخدم تركية دينية معاصرة ومفهومة" in SACRED_SYSTEM_PROMPT
