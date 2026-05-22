from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base
from app.services.pipeline import (
    TranslationPipeline,
    extract_ek_not,
    extract_final_translation,
    extract_warnings,
    is_complete_warning,
    split_sacred_text,
)
from app.services.translations import create_translation_request


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


def test_extract_final_translation_with_reason() -> None:
    text = "FINAL_TRANSLATION:\nMerhaba dünya\n\nBRIEF_REASON:\nNatural Turkish."
    assert extract_final_translation(text) == "Merhaba dünya"


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


def test_extract_warnings_hides_incomplete_warning() -> None:
    text = "FINAL_TRANSLATION:\nMetin.\n\nWARNINGS:\nKesinlikle"
    assert extract_warnings(text) == ""
    assert not is_complete_warning("Bu yüzden")
    assert not is_complete_warning("Açıklama metnin aslından değil çünkü")
    assert is_complete_warning("Ek not metnin aslından değildir.")


def test_split_sacred_text_moves_extra_note_outside_hadith() -> None:
    text = (
        "1/1249- عن ابن عباس رضي الله عنهما، قال: قال رسول الله ﷺ: ما من أيام العمل الصالح فيها أحب إلى الله من هذه الأيام رواه البخاري.\n\n"
        "حتى انها افضل من العشر الاواخر من رمضان"
    )

    sacred_source_text, user_extra_note = split_sacred_text(text)

    assert "رواه البخاري" in sacred_source_text
    assert user_extra_note == "حتى انها افضل من العشر الاواخر من رمضان"


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
        assert any("مترجم كوميكس ومانجا" in prompt for prompt in fake_client.system_prompts)
        assert not any("مترجم قانوني محترف" in prompt for prompt in fake_client.system_prompts)

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
        assert "مترجم كوميكس ومانجا" in fake_client.system_prompts[1]
        assert "مترجم دقيق للنصوص الإسلامية" in fake_client.system_prompts[1]

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
