from app.prompts.translation_prompts import build_system_prompt
from app.services.translation_policy import COMIC_POLICY, LEGAL_POLICY, SACRED_POLICY


def test_sacred_policy_contains_general_fidelity_rules() -> None:
    prompt = SACRED_POLICY.to_prompt()

    assert SACRED_POLICY.mode == "sacred"
    assert not SACRED_POLICY.allow_paraphrase
    assert SACRED_POLICY.preserve_sentence_type
    assert SACRED_POLICY.preserve_negation
    assert SACRED_POLICY.preserve_conditionals
    assert SACRED_POLICY.preserve_exceptions
    assert SACRED_POLICY.preserve_certainty_level
    assert SACRED_POLICY.quality_gate_required
    assert "إعادة الصياغة الحرة" in prompt
    assert "تحويل السؤال إلى تقرير" in prompt
    assert "تحويل اليقين إلى احتمال" in prompt
    assert "s.a.v." in prompt
    assert "r.a." in prompt
    assert "cc" in prompt
    assert "rh.a." in prompt
    assert "salih amel" in prompt
    assert "Allah yolunda cihad" in prompt
    assert "ek not" in prompt


def test_comic_policy_is_dialogue_oriented_without_sacred_by_default() -> None:
    prompt = COMIC_POLICY.to_prompt()

    assert COMIC_POLICY.mode == "comic"
    assert COMIC_POLICY.allow_paraphrase
    assert "حوار طبيعي" in prompt
    assert "فقاعة الكلام" in prompt
    assert "شرح النكتة" in prompt
    assert "معاملة جزء ديني حساس" in prompt
    assert not COMIC_POLICY.quality_gate_required


def test_legal_policy_requires_quality_gate_and_preserves_obligations() -> None:
    prompt = LEGAL_POLICY.to_prompt()

    assert LEGAL_POLICY.mode == "legal"
    assert not LEGAL_POLICY.allow_paraphrase
    assert LEGAL_POLICY.quality_gate_required
    assert "تغيير قوة الالتزام القانوني" in prompt
    assert "حذف شرط أو قيد أو استثناء" in prompt


def test_build_system_prompt_adds_only_selected_policy_unless_sacred_guard_needed() -> None:
    comic_prompt = build_system_prompt("comic")
    guarded_comic_prompt = build_system_prompt("comic", has_sacred_segment=True)
    sacred_prompt = build_system_prompt("sacred")

    assert "mode: comic" in comic_prompt
    assert "mode: sacred" not in comic_prompt
    assert "Sacred segment guard" in guarded_comic_prompt
    assert "mode: comic" in guarded_comic_prompt
    assert "mode: sacred" in guarded_comic_prompt
    assert "mode: sacred" in sacred_prompt
