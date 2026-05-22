from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.translation import TranslationRequest


async def create_translation_request(
    session: AsyncSession,
    direction: str,
    source_text: str,
    telegram_user_id: int | None = None,
    telegram_chat_id: int | None = None,
) -> TranslationRequest:
    request = TranslationRequest(
        direction=direction,
        source_text=source_text,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        status="pending",
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request


async def list_translation_requests(session: AsyncSession, limit: int = 100) -> list[TranslationRequest]:
    result = await session.execute(
        select(TranslationRequest)
        .options(selectinload(TranslationRequest.layers))
        .order_by(desc(TranslationRequest.created_at))
        .limit(limit)
    )
    return list(result.scalars().unique())


async def get_translation_request(session: AsyncSession, request_id: int) -> TranslationRequest | None:
    result = await session.execute(
        select(TranslationRequest)
        .options(selectinload(TranslationRequest.layers))
        .where(TranslationRequest.id == request_id)
    )
    return result.scalars().unique().one_or_none()
