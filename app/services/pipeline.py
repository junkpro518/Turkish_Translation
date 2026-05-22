import time
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.translation import TranslationLayerResult, TranslationRequest
from app.services.layers import LAYER_DEFINITIONS, direction_label
from app.services.openrouter import OpenRouterClient

ProgressCallback = Callable[[TranslationRequest, list[TranslationLayerResult]], Awaitable[None]]


def build_layer_prompt(
    source_text: str,
    direction: str,
    previous_outputs: list[TranslationLayerResult],
) -> str:
    previous = "\n\n".join(
        f"## {layer.position}. {layer.name}\n{layer.output_text or layer.error or ''}" for layer in previous_outputs
    )
    if not previous:
        previous = "لا توجد طبقات سابقة."

    return (
        f"اتجاه الترجمة: {direction_label(direction)}\n\n"
        f"النص الأصلي:\n{source_text}\n\n"
        f"تحليلات/نتائج الطبقات السابقة:\n{previous}\n\n"
        "التزم بدورك فقط، واكتب مخرجا واضحا قابلا للمراجعة في لوحة التحكم."
    )


def extract_final_translation(text: str) -> str:
    marker = "FINAL_TRANSLATION"
    if marker not in text:
        return text.strip()
    after = text.split(marker, 1)[1]
    after = after.lstrip(" :\n\r\t-")
    reason_markers = ["BRIEF_REASON", "Brief reason", "سبب مختصر"]
    end = len(after)
    for reason_marker in reason_markers:
        idx = after.find(reason_marker)
        if idx != -1:
            end = min(end, idx)
    return after[:end].strip(" \n\r\t:-")


class TranslationPipeline:
    def __init__(self, settings: Settings, openrouter_client: OpenRouterClient | None = None):
        self.settings = settings
        self.openrouter = openrouter_client or OpenRouterClient(settings)

    async def run(
        self,
        session: AsyncSession,
        request: TranslationRequest,
        model: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranslationRequest:
        selected_model = model or self.settings.openrouter_model
        request.status = "running"
        request.error = None
        await session.commit()
        await session.refresh(request)

        completed_layers: list[TranslationLayerResult] = []
        try:
            for layer in LAYER_DEFINITIONS:
                result = TranslationLayerResult(
                    request_id=request.id,
                    position=layer.position,
                    name=layer.name,
                    model=selected_model,
                    status="running",
                    input_summary=f"{direction_label(request.direction)} | {len(request.source_text)} characters",
                )
                session.add(result)
                await session.commit()
                await session.refresh(result)
                if on_progress:
                    await on_progress(request, [*completed_layers, result])

                started = time.perf_counter()
                try:
                    output = await self.openrouter.complete(
                        system_prompt=layer.system_prompt,
                        user_prompt=build_layer_prompt(request.source_text, request.direction, completed_layers),
                        model=selected_model,
                    )
                    result.output_text = output
                    result.status = "completed"
                    result.duration_ms = int((time.perf_counter() - started) * 1000)
                    completed_layers.append(result)
                    if layer.position == 7:
                        request.final_translation = extract_final_translation(output)
                    await session.commit()
                    if on_progress:
                        await on_progress(request, completed_layers)
                except Exception as exc:
                    result.status = "failed"
                    result.error = str(exc)
                    result.duration_ms = int((time.perf_counter() - started) * 1000)
                    request.status = "failed"
                    request.error = f"{layer.name}: {exc}"
                    await session.commit()
                    if on_progress:
                        await on_progress(request, [*completed_layers, result])
                    return request

            request.status = "completed"
            await session.commit()
            await session.refresh(request)
            if on_progress:
                await on_progress(request, completed_layers)
            return request
        except Exception as exc:
            request.status = "failed"
            request.error = str(exc)
            await session.commit()
            return request
