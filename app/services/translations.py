from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.translation import TranslationLayerResult, TranslationRequest

FAILED_CLEANUP_STATUSES = ("failed", "quality_failed")


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


async def count_failed_translation_data(session: AsyncSession) -> tuple[int, int]:
    request_count_result = await session.execute(
        select(func.count()).select_from(TranslationRequest).where(TranslationRequest.status.in_(FAILED_CLEANUP_STATUSES))
    )
    layer_count_result = await session.execute(
        select(func.count())
        .select_from(TranslationLayerResult)
        .join(TranslationRequest)
        .where(TranslationRequest.status.in_(FAILED_CLEANUP_STATUSES))
    )
    return int(request_count_result.scalar_one()), int(layer_count_result.scalar_one())


async def delete_failed_translation_data(session: AsyncSession) -> tuple[int, int]:
    request_ids_result = await session.execute(
        select(TranslationRequest.id).where(TranslationRequest.status.in_(FAILED_CLEANUP_STATUSES))
    )
    request_ids = list(request_ids_result.scalars())
    if not request_ids:
        return 0, 0

    layer_count_result = await session.execute(
        select(func.count()).select_from(TranslationLayerResult).where(TranslationLayerResult.request_id.in_(request_ids))
    )
    layer_count = int(layer_count_result.scalar_one())
    await session.execute(delete(TranslationLayerResult).where(TranslationLayerResult.request_id.in_(request_ids)))
    await session.execute(delete(TranslationRequest).where(TranslationRequest.id.in_(request_ids)))
    await session.commit()
    return len(request_ids), layer_count
