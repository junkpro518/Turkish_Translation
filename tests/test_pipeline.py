import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base
from app.services.pipeline import (
    QUALITY_CRITICAL,
    QUALITY_FAILED_STATUS,
    QUALITY_ISSUE_FLUENCY,
    QUALITY_ISSUE_INTERPRETIVE_EXPANSION,
    QUALITY_PASS,
    QUALITY_WARNING,
    TranslationPipeline,
    extract_ek_not,
    extract_final_translation,
    extract_warnings,
    is_complete_warning,
    parse_quality_gate_output,
    requires_strict_final_translation,
    split_sacred_text,
)
from app.services.translations import count_failed_translation_data, create_translation_request, delete_failed_translation_data


class FakeOpenRouterClient:
    def __init__(self):
        self.calls = 0
        self.models = []
        self.system_prompts = []
        self.user_prompts = []

    async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
        self.calls += 1
        self.models.append(model)
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        if self.calls == 1:
            return (
                "detected_mode: general\n"
                "recommended_mode: general\n"
                "has_sacred_segment: false\n"
                "freedom_level: medium\n"
                "allow_paraphrase: true\n"
                "sensitive_terms: لا يوجد\n"
                "warnings: لا يوجد"
            )
        if self.calls == 8:
            return "FINAL_TRANSLATION:\nMerhaba dünya\n\nBRIEF_REASON:\nNatural Turkish.\n\nWARNINGS:\nلا يوجد"
        return f"Layer {self.calls} analysis"


class GoldenCaseOpenRouterClient(FakeOpenRouterClient):
    def __init__(self, case: dict[str, str]):
        super().__init__()
        self.case = case

    async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
        self.calls += 1
        self.models.append(model)
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        if self.calls == 1:
            return self.case["classifier_output"]
        if self.calls in {8, 10}:
            return self.case["final_output"]
        if self.calls in {9, 11}:
            return self.case["quality_gate_output"]
        return f"Layer {self.calls} analysis"


GOLDEN_CASE_DIR = Path(__file__).parent / "fixtures" / "golden_cases"
GOLDEN_CASE_FILES = (
    GOLDEN_CASE_DIR / "sacred_hadith_extra_note.json",
    GOLDEN_CASE_DIR / "sacred_hadith_fluency_warning.json",
    GOLDEN_CASE_DIR / "comic_with_sacred_segment.json",
    GOLDEN_CASE_DIR / "legal_structural_violation.json",
)


def test_extract_final_translation_with_reason() -> None:
    text = "FINAL_TRANSLATION:\nMerhaba dünya\n\nBRIEF_REASON:\nNatural Turkish."
    assert extract_final_translation(text) == "Merhaba dünya"


def test_extract_final_translation_strict_requires_marker() -> None:
    text = "Merhaba dünya\n\nBRIEF_REASON:\nNatural Turkish."

    assert extract_final_translation(text) == text
    assert extract_final_translation(text, strict=True) == ""


def test_extract_final_translation_stops_before_ek_not() -> None:
    text = (
        "FINAL_TRANSLATION:\n"
        "Peygamber sallallahu aleyhi ve sellem şöyle buyurdu...\n\n"
        "EK NOT:\n"
        "Bu açıklama metnin aslından değil, ek bir nottur: Ek açıklama.\n\n"
        "BRIEF_REASON:\n"
        "Metin ile ek not ayrıldı.\n\n"
        "WARNINGS:\n"
        "لا يوجد"
    )
    assert extract_final_translation(text) == "Peygamber sallallahu aleyhi ve sellem şöyle buyurdu..."
    assert extract_ek_not(text) == "Bu açıklama metnin aslından değil, ek bir nottur: Ek açıklama."
    assert extract_warnings(text) == ""


def test_strict_final_translation_required_for_sensitive_modes() -> None:
    assert requires_strict_final_translation("sacred", False)
    assert requires_strict_final_translation("legal", False)
    assert requires_strict_final_translation("comic", True)
    assert not requires_strict_final_translation("general", False)


def test_extract_warnings_hides_incomplete_warning() -> None:
    text = "FINAL_TRANSLATION:\nMetin.\n\nWARNINGS:\nKesinlikle"
    assert extract_warnings(text) == ""
    assert not is_complete_warning("Bu yüzden")
    assert not is_complete_warning("Açıklama metnin aslından değil çünkü")
    assert is_complete_warning("Ek not metnin aslından değildir.")


def test_parse_quality_gate_output_reads_severity_and_feedback() -> None:
    warning = parse_quality_gate_output(
        '{"issue_type":"register_issue","severity":"warning","feedback":"Ek not metnin aslından değildir."}'
    )
    critical = parse_quality_gate_output(
        '{"issue_type":"structural_violation","severity":"critical","feedback":"Soru rapora dönüştü."}'
    )
    passed = parse_quality_gate_output('{"issue_type":"fluency_issue","severity":"pass","feedback":"لا يوجد"}')

    assert warning.severity == QUALITY_WARNING
    assert warning.feedback == "Ek not metnin aslından değildir."
    assert critical.severity == QUALITY_CRITICAL
    assert passed.severity == QUALITY_PASS


def test_parse_quality_gate_invalid_json_falls_back_to_warning() -> None:
    result = parse_quality_gate_output("severity: critical\nFEEDBACK:\nSoru rapora dönüştü.")

    assert result.severity == QUALITY_WARNING
    assert "تعذر قراءة نتيجة فحص الجودة" in result.feedback


def test_parse_quality_gate_missing_fields_falls_back_to_warning() -> None:
    result = parse_quality_gate_output('{"severity":"critical","feedback":"Soru rapora dönüştü."}')

    assert result.severity == QUALITY_WARNING
    assert "تعذر قراءة نتيجة فحص الجودة" in result.feedback


def test_parse_quality_gate_unknown_issue_type_falls_back_to_warning() -> None:
    result = parse_quality_gate_output(
        '{"issue_type":"unclear_problem","severity":"critical","feedback":"Soru rapora dönüştü."}'
    )

    assert result.severity == QUALITY_WARNING
    assert "تعذر قراءة نتيجة فحص الجودة" in result.feedback


def test_parse_quality_gate_downgrades_simple_interpretive_expansion_to_warning() -> None:
    result = parse_quality_gate_output(
        json.dumps(
            {
                "issue_type": "interpretive_expansion",
                "severity": "critical",
                "feedback": "Parantez içi açıklama FINAL_TRANSLATION içinde yer almamalıdır.",
            }
        )
    )

    assert result.issue_type == QUALITY_ISSUE_INTERPRETIVE_EXPANSION
    assert result.severity == QUALITY_WARNING


def test_parse_quality_gate_downgrades_generic_critical_feedback_to_warning() -> None:
    result = parse_quality_gate_output(
        json.dumps(
            {
                "issue_type": "semantic_drift",
                "severity": "critical",
                "feedback": "قد تغيّر المعنى ويحتاج النص إلى مراجعة.",
            }
        )
    )

    assert result.issue_type == "semantic_drift"
    assert result.severity == QUALITY_WARNING


def test_parse_quality_gate_downgrades_semantic_drift_without_specific_evidence() -> None:
    result = parse_quality_gate_output(
        '{"issue_type":"semantic_drift","severity":"critical","feedback":"قد تغيّر المعنى."}'
    )

    assert result.severity == QUALITY_WARNING


def test_parse_quality_gate_downgrades_fluency_critical_to_warning() -> None:
    result = parse_quality_gate_output(
        '{"issue_type":"fluency_issue","severity":"critical","feedback":"Türkçe cümle akışı doğal değildir."}'
    )

    assert result.issue_type == QUALITY_ISSUE_FLUENCY
    assert result.severity == QUALITY_WARNING


def test_split_sacred_text_moves_extra_note_outside_hadith() -> None:
    text = (
        "1/1249- عن ابن عباس رضي الله عنهما، قال: قال رسول الله ﷺ: ما من أيام العمل الصالح فيها أحب إلى الله من هذه الأيام رواه البخاري.\n\n"
        "حتى انها افضل من العشر الاواخر من رمضان"
    )

    sacred_source_text, user_extra_note = split_sacred_text(text)

    assert "رواه البخاري" in sacred_source_text
    assert user_extra_note == "حتى انها افضل من العشر الاواخر من رمضان"


def test_split_sacred_text_uses_explicit_note_marker() -> None:
    text = "قال رسول الله ﷺ: إنما الأعمال بالنيات.\nملاحظة: هذا للتذكير فقط."

    sacred_source_text, user_extra_note = split_sacred_text(text)

    assert "إنما الأعمال بالنيات" in sacred_source_text
    assert user_extra_note == "هذا للتذكير فقط."


def test_split_sacred_text_keeps_inline_yaani_inside_hadith() -> None:
    text = (
        "قال رسول الله ﷺ: ما من أيام العمل الصالح فيها أحب إلى الله من هذه الأيام "
        "يعني: أيام العشر."
    )

    sacred_source_text, user_extra_note = split_sacred_text(text)

    assert "يعني: أيام العشر" in sacred_source_text
    assert user_extra_note == ""


async def test_pipeline_persists_all_layers_and_final_translation() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    fake_client = FakeOpenRouterClient()
    pipeline = TranslationPipeline(settings=settings, openrouter_client=fake_client)

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "مرحبا بالعالم", 1, 2)
        result = await pipeline.run(session, request)
        await session.refresh(result, ["layers"])

        assert result.status == "completed"
        assert result.final_translation == "Merhaba dünya"
        assert len(result.layers) == 8
        assert all(layer.status == "completed" for layer in result.layers)
        assert all(layer.model == "test-model" for layer in result.layers)
        assert fake_client.calls == 8
        assert fake_client.models == ["test-model"] * 8

    await engine.dispose()


async def test_pipeline_uses_selected_model_override() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="default-model", OPENROUTER_API_KEY="fake")
    fake_client = FakeOpenRouterClient()
    pipeline = TranslationPipeline(settings=settings, openrouter_client=fake_client)

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "مرحبا بالعالم", 1, 2)
        result = await pipeline.run(session, request, model="selected-model")
        await session.refresh(result, ["layers"])

        assert result.status == "completed"
        assert all(layer.model == "selected-model" for layer in result.layers)
        assert fake_client.models == ["selected-model"] * 8

    await engine.dispose()


async def test_pipeline_adds_selected_comic_prompt_only_when_comic_mode() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    fake_client = FakeOpenRouterClient()
    pipeline = TranslationPipeline(settings=settings, openrouter_client=fake_client)

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "ما هذا؟!", 1, 2)
        result = await pipeline.run(session, request, translation_mode="comic")

        assert result.status == "completed"
        assert any("mode: comic" in prompt for prompt in fake_client.system_prompts)
        assert not any("mode: legal" in prompt for prompt in fake_client.system_prompts)

    await engine.dispose()


async def test_pipeline_adds_sacred_guard_when_classifier_finds_sacred_segment() -> None:
    class SacredSegmentClient(FakeOpenRouterClient):
        async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
            self.calls += 1
            self.models.append(model)
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.calls == 1:
                return (
                    "detected_mode: comic\n"
                    "recommended_mode: comic\n"
                    "has_sacred_segment: true\n"
                    "freedom_level: low\n"
                    "allow_paraphrase: false\n"
                    "sensitive_terms: حديث\n"
                    "warnings: يحتوي جزءا دينيا حساسا"
                )
            if self.calls == 8:
                return "FINAL_TRANSLATION:\nMerhaba dünya\n\nBRIEF_REASON:\nNatural Turkish.\n\nWARNINGS:\nلا يوجد"
            return f"Layer {self.calls} analysis"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    fake_client = SacredSegmentClient()
    pipeline = TranslationPipeline(settings=settings, openrouter_client=fake_client)

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "قال النبي ﷺ...", 1, 2)
        result = await pipeline.run(session, request)

        assert result.status == "completed"
        assert "mode: comic" in fake_client.system_prompts[1]
        assert "Sacred segment guard" in fake_client.system_prompts[1]
        assert "mode: sacred" in fake_client.system_prompts[1]

    await engine.dispose()


async def test_pipeline_includes_sacred_split_in_prompts() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    fake_client = FakeOpenRouterClient()
    pipeline = TranslationPipeline(settings=settings, openrouter_client=fake_client)
    text = (
        "عن ابن عباس رضي الله عنهما، قال: قال رسول الله ﷺ: ما من أيام العمل الصالح فيها أحب إلى الله من هذه الأيام رواه البخاري.\n\n"
        "حتى انها افضل من العشر الاواخر من رمضان"
    )

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", text, 1, 2)
        result = await pipeline.run(session, request, translation_mode="sacred")

        assert result.status == "completed"
        assert any("sacred_source_text" in prompt for prompt in fake_client.user_prompts)
        assert any("user_extra_note" in prompt for prompt in fake_client.user_prompts)
        assert any("حتى انها افضل من العشر الاواخر من رمضان" in prompt for prompt in fake_client.user_prompts)

    await engine.dispose()


async def test_translation_request_accepts_large_telegram_ids() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    large_id = 5_464_178_168
    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "مرحبا", large_id, large_id)

        assert request.telegram_user_id == large_id
        assert request.telegram_chat_id == large_id

    await engine.dispose()


async def test_quality_gate_marks_sacred_request_failed_after_retry_stays_critical() -> None:
    class CriticalQualityClient(FakeOpenRouterClient):
        async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
            self.calls += 1
            self.models.append(model)
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.calls == 1:
                return (
                    "detected_mode: sacred\n"
                    "recommended_mode: sacred\n"
                    "has_sacred_segment: true\n"
                    "freedom_level: low\n"
                    "allow_paraphrase: false\n"
                    "sensitive_terms: حديث\n"
                    "warnings: لا يوجد"
                )
            if self.calls == 8:
                return "FINAL_TRANSLATION:\nYanlış anlam.\n\nBRIEF_REASON:\nKısa.\n\nWARNINGS:\nلا يوجد"
            if self.calls in {9, 11}:
                return '{"issue_type":"structural_violation","severity":"critical","feedback":"Soru rapora dönüştü."}'
            if self.calls == 10:
                return "FINAL_TRANSLATION:\nHâlâ yanlış.\n\nBRIEF_REASON:\nKısa.\n\nWARNINGS:\nلا يوجد"
            return f"Layer {self.calls} analysis"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    pipeline = TranslationPipeline(settings=settings, openrouter_client=CriticalQualityClient())

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "قال النبي ﷺ...", 1, 2)
        result = await pipeline.run(session, request, translation_mode="sacred")

        assert result.status == QUALITY_FAILED_STATUS
        assert "Soru rapora dönüştü" in (result.error or "")

    await engine.dispose()


async def test_sacred_final_layer_missing_final_translation_fails_without_raw_translation() -> None:
    class MissingFinalSectionClient(FakeOpenRouterClient):
        async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
            self.calls += 1
            self.models.append(model)
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.calls == 1:
                return (
                    "detected_mode: sacred\n"
                    "recommended_mode: sacred\n"
                    "has_sacred_segment: true\n"
                    "freedom_level: low\n"
                    "allow_paraphrase: false\n"
                    "sensitive_terms: حديث\n"
                    "warnings: لا يوجد"
                )
            if self.calls == 8:
                return "Bu raw output should not be sent as final translation."
            return f"Layer {self.calls} analysis"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    pipeline = TranslationPipeline(settings=settings, openrouter_client=MissingFinalSectionClient())

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "قال رسول الله ﷺ...", 1, 2)
        result = await pipeline.run(session, request, translation_mode="sacred")

        assert result.status == "failed"
        assert result.final_translation in {None, ""}
        assert "Missing FINAL_TRANSLATION" in (result.error or "")

    await engine.dispose()


async def test_quality_gate_warning_keeps_translation_and_appends_warning() -> None:
    class WarningQualityClient(FakeOpenRouterClient):
        async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
            self.calls += 1
            self.models.append(model)
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.calls == 1:
                return (
                    "detected_mode: legal\n"
                    "recommended_mode: legal\n"
                    "has_sacred_segment: false\n"
                    "freedom_level: low\n"
                    "allow_paraphrase: false\n"
                    "sensitive_terms: عقد\n"
                    "warnings: لا يوجد"
                )
            if self.calls == 8:
                return "FINAL_TRANSLATION:\nSözleşme metni.\n\nBRIEF_REASON:\nKısa.\n\nWARNINGS:\nلا يوجد"
            if self.calls == 9:
                return '{"issue_type":"register_issue","severity":"warning","feedback":"Bir terim ayrıca kontrol edilmelidir."}'
            return f"Layer {self.calls} analysis"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    pipeline = TranslationPipeline(settings=settings, openrouter_client=WarningQualityClient())

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "نص عقد", 1, 2)
        result = await pipeline.run(session, request, translation_mode="legal")
        await session.refresh(result, ["layers"])
        final_layer = max(result.layers, key=lambda layer: layer.position)

        assert result.status == "completed"
        assert "Bir terim ayrıca kontrol edilmelidir." in (final_layer.output_text or "")

    await engine.dispose()


async def test_quality_gate_sacred_interpretive_expansion_warning_does_not_fail() -> None:
    class InterpretiveExpansionClient(FakeOpenRouterClient):
        async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
            self.calls += 1
            self.models.append(model)
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.calls == 1:
                return (
                    "detected_mode: sacred\n"
                    "recommended_mode: sacred\n"
                    "has_sacred_segment: true\n"
                    "freedom_level: low\n"
                    "allow_paraphrase: false\n"
                    "sensitive_terms: حديث\n"
                    "warnings: لا يوجد"
                )
            if self.calls == 8:
                return (
                    "FINAL_TRANSLATION:\n"
                    "Bu günlerde yapılan salih amellerden daha sevimli hiçbir amel yoktur (daha sevaplı).\n\n"
                    "BRIEF_REASON:\n"
                    "تم الحفاظ على البنية الشرعية للنص ومنع التفسير داخل الترجمة الأساسية.\n\n"
                    "WARNINGS:\n"
                    "لا يوجد"
                )
            if self.calls == 9:
                return (
                    '{"issue_type":"interpretive_expansion","severity":"critical",'
                    '"feedback":"Parantez içi açıklama FINAL_TRANSLATION içinde yer almamalıdır."}'
                )
            return f"Layer {self.calls} analysis"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    client = InterpretiveExpansionClient()
    pipeline = TranslationPipeline(settings=settings, openrouter_client=client)

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "قال رسول الله ﷺ...", 1, 2)
        result = await pipeline.run(session, request, translation_mode="sacred")
        await session.refresh(result, ["layers"])
        final_layer = max(result.layers, key=lambda layer: layer.position)
        await session.refresh(final_layer)

        assert result.status == "completed"
        assert client.calls == 9
        assert "Parantez içi açıklama" in (final_layer.output_text or "")

    await engine.dispose()


async def test_quality_gate_sacred_fluency_issue_warning_does_not_fail() -> None:
    class FluencyIssueClient(FakeOpenRouterClient):
        async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
            self.calls += 1
            self.models.append(model)
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.calls == 1:
                return (
                    "detected_mode: sacred\n"
                    "recommended_mode: sacred\n"
                    "has_sacred_segment: true\n"
                    "freedom_level: low\n"
                    "allow_paraphrase: false\n"
                    "sensitive_terms: حديث ابن عباس\n"
                    "warnings: لا يوجد"
                )
            if self.calls == 8:
                return (
                    "FINAL_TRANSLATION:\n"
                    "Allah katında bu günlerde yapılan salih amellerden daha sevimli hiçbir amel yoktur.\n\n"
                    "BRIEF_REASON:\n"
                    "تم الحفاظ على البنية الشرعية للنص.\n\n"
                    "WARNINGS:\n"
                    "لا يوجد"
                )
            if self.calls == 9:
                return '{"issue_type":"fluency_issue","severity":"critical","feedback":"Türkçe cümle akışı doğal değildir."}'
            return f"Layer {self.calls} analysis"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    client = FluencyIssueClient()
    pipeline = TranslationPipeline(settings=settings, openrouter_client=client)

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "حديث ابن عباس عن عشر ذي الحجة", 1, 2)
        result = await pipeline.run(session, request, translation_mode="sacred")
        await session.refresh(result, ["layers"])
        final_layer = max(result.layers, key=lambda layer: layer.position)
        await session.refresh(final_layer)

        assert result.status == "completed"
        assert "Türkçe cümle akışı doğal değildir." in (final_layer.output_text or "")

    await engine.dispose()


async def test_quality_retry_missing_final_translation_marks_quality_failed() -> None:
    class MissingRetryFinalSectionClient(FakeOpenRouterClient):
        async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
            self.calls += 1
            self.models.append(model)
            self.system_prompts.append(system_prompt)
            self.user_prompts.append(user_prompt)
            if self.calls == 1:
                return (
                    "detected_mode: sacred\n"
                    "recommended_mode: sacred\n"
                    "has_sacred_segment: true\n"
                    "freedom_level: low\n"
                    "allow_paraphrase: false\n"
                    "sensitive_terms: حديث\n"
                    "warnings: لا يوجد"
                )
            if self.calls == 8:
                return "FINAL_TRANSLATION:\nYanlış anlam.\n\nBRIEF_REASON:\nKısa.\n\nWARNINGS:\nلا يوجد"
            if self.calls == 9:
                return '{"issue_type":"semantic_drift","severity":"critical","feedback":"Anlam değişmiştir."}'
            if self.calls == 10:
                return "Retry raw output without required section."
            return f"Layer {self.calls} analysis"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    pipeline = TranslationPipeline(settings=settings, openrouter_client=MissingRetryFinalSectionClient())

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", "قال رسول الله ﷺ...", 1, 2)
        result = await pipeline.run(session, request, translation_mode="sacred")

        assert result.status == QUALITY_FAILED_STATUS
        assert result.final_translation == ""
        assert "retry output missing FINAL_TRANSLATION" in (result.error or "")

    await engine.dispose()


@pytest.mark.parametrize("case_path", GOLDEN_CASE_FILES, ids=lambda path: path.stem)
async def test_golden_translation_cases(case_path: Path) -> None:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(OPENROUTER_MODEL="test-model", OPENROUTER_API_KEY="fake")
    client = GoldenCaseOpenRouterClient(case)
    pipeline = TranslationPipeline(settings=settings, openrouter_client=client)

    async with session_factory() as session:
        request = await create_translation_request(session, "ar_to_tr", case["source_text"], 1, 2)
        result = await pipeline.run(session, request, translation_mode=case["mode"])
        await session.refresh(result, ["layers"])

        assert result.status == case["expected_status"]
        expected_text = case["expected_warning_contains"]
        if result.status == "completed":
            final_layer = max(result.layers, key=lambda layer: layer.position)
            await session.refresh(final_layer)
            searchable_output = f"{result.final_translation or ''}\n{final_layer.output_text or ''}"
            assert expected_text in searchable_output
        else:
            assert expected_text in (result.error or "")

    await engine.dispose()


async def test_failed_cleanup_counts_and_deletes_only_failed_requests() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        failed = await create_translation_request(session, "ar_to_tr", "فشل", 1, 2)
        quality_failed = await create_translation_request(session, "ar_to_tr", "فشل جودة", 1, 2)
        completed = await create_translation_request(session, "ar_to_tr", "نجح", 1, 2)
        failed.status = "failed"
        quality_failed.status = QUALITY_FAILED_STATUS
        completed.status = "completed"
        session.add_all([failed, quality_failed, completed])
        await session.commit()

        request_count, layer_count = await count_failed_translation_data(session)
        deleted_requests, deleted_layers = await delete_failed_translation_data(session)
        remaining_count, _ = await count_failed_translation_data(session)

        assert request_count == 2
        assert layer_count == 0
        assert deleted_requests == 2
        assert deleted_layers == 0
        assert remaining_count == 0
        assert completed.id is not None

    await engine.dispose()
