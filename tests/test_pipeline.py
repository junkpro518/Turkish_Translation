from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base
from app.services.pipeline import TranslationPipeline, extract_final_translation
from app.services.translations import create_translation_request


class FakeOpenRouterClient:
    def __init__(self):
        self.calls = 0

    async def complete(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
        self.calls += 1
        if self.calls == 7:
            return "FINAL_TRANSLATION:\nMerhaba dünya\n\nBRIEF_REASON:\nNatural Turkish."
        return f"Layer {self.calls} analysis"


def test_extract_final_translation_with_reason() -> None:
    text = "FINAL_TRANSLATION:\nMerhaba dünya\n\nBRIEF_REASON:\nNatural Turkish."
    assert extract_final_translation(text) == "Merhaba dünya"


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
        assert len(result.layers) == 7
        assert all(layer.status == "completed" for layer in result.layers)
        assert fake_client.calls == 7

    await engine.dispose()
