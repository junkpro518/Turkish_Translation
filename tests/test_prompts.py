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
