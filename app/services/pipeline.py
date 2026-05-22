import time
import re
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.translation import TranslationLayerResult, TranslationRequest
from app.prompts.translation_prompts import build_system_prompt
from app.services.layers import (
    LAYER_DEFINITIONS,
    SUPPORTED_TRANSLATION_MODES,
    TRANSLATION_MODE_AUTO,
    TRANSLATION_MODE_GENERAL,
    direction_label,
    mode_label,
    normalize_translation_mode,
)
from app.services.openrouter import OpenRouterClient

ProgressCallback = Callable[[TranslationRequest, list[TranslationLayerResult]], Awaitable[None]]

MODE_PATTERN = re.compile(
    r"(?:recommended_mode|detected_mode|mode)\s*[:=]\s*"
    r"(general|comic|sacred|legal|literary|marketing)",
    re.IGNORECASE,
)
SACRED_PATTERN = re.compile(
    r"has_sacred_segment\s*[:=]\s*(true|yes|نعم|صحيح|1)",
    re.IGNORECASE,
)


def build_layer_prompt(
    source_text: str,
    direction: str,
    previous_outputs: list[TranslationLayerResult],
    requested_mode: str,
    effective_mode: str,
    has_sacred_segment: bool,
) -> str:
    previous = "\n\n".join(
        f"## {layer.position}. {layer.name}\n{layer.output_text or layer.error or ''}" for layer in previous_outputs
    )
    if not previous:
        previous = "لا توجد طبقات سابقة."

    return (
        f"اتجاه الترجمة: {direction_label(direction)}\n\n"
        f"وضع الترجمة الذي اختاره المستخدم: {mode_label(requested_mode)} ({requested_mode})\n"
        f"وضع الترجمة المستخدم في هذه الطبقة: {mode_label(effective_mode)} ({effective_mode})\n"
        f"هل يوجد جزء ديني/شرعي حساس داخل النص: {'نعم' if has_sacred_segment else 'لا'}\n\n"
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
    reason_markers = ["EK NOT", "BRIEF_REASON", "Brief reason", "سبب مختصر", "WARNINGS"]
    end = len(after)
    for reason_marker in reason_markers:
        idx = after.find(reason_marker)
        if idx != -1:
            end = min(end, idx)
    return after[:end].strip(" \n\r\t:-")


def is_auto_mode(mode: str | None) -> bool:
    return (mode or TRANSLATION_MODE_AUTO).strip().lower() == TRANSLATION_MODE_AUTO


def detect_mode_from_policy_output(text: str) -> str | None:
    match = MODE_PATTERN.search(text or "")
    if not match:
        return None
    mode = match.group(1).lower()
    if mode not in SUPPORTED_TRANSLATION_MODES:
        return None
    return mode


def detect_sacred_segment_from_policy_output(text: str) -> bool:
    return bool(SACRED_PATTERN.search(text or ""))


class TranslationPipeline:
    def __init__(self, settings: Settings, openrouter_client: OpenRouterClient | None = None):
        self.settings = settings
        self.openrouter = openrouter_client or OpenRouterClient(settings)

    async def run(
        self,
        session: AsyncSession,
        request: TranslationRequest,
        model: str | None = None,
        translation_mode: str = TRANSLATION_MODE_AUTO,
        on_progress: ProgressCallback | None = None,
    ) -> TranslationRequest:
        selected_model = model or self.settings.openrouter_model
        requested_mode = (translation_mode or TRANSLATION_MODE_AUTO).strip().lower()
        effective_mode = TRANSLATION_MODE_GENERAL if is_auto_mode(requested_mode) else normalize_translation_mode(requested_mode)
        has_sacred_segment = False
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
                    input_summary=(
                        f"{direction_label(request.direction)} | {len(request.source_text)} characters | "
                        f"mode={requested_mode} | effective={effective_mode}"
                    ),
                )
                session.add(result)
                await session.commit()
                await session.refresh(result)
                if on_progress:
                    await on_progress(request, [*completed_layers, result])

                started = time.perf_counter()
                try:
                    output = await self.openrouter.complete(
                        system_prompt=f"{build_system_prompt(effective_mode, has_sacred_segment)}\n\n{layer.system_prompt}",
                        user_prompt=build_layer_prompt(
                            source_text=request.source_text,
                            direction=request.direction,
                            previous_outputs=completed_layers,
                            requested_mode=requested_mode,
                            effective_mode=effective_mode,
                            has_sacred_segment=has_sacred_segment,
                        ),
                        model=selected_model,
                    )
                    result.output_text = output
                    result.status = "completed"
                    result.duration_ms = int((time.perf_counter() - started) * 1000)
                    completed_layers.append(result)

                    if layer.position == 1:
                        detected_mode = detect_mode_from_policy_output(output)
                        if detected_mode and is_auto_mode(requested_mode):
                            effective_mode = detected_mode
                        has_sacred_segment = detect_sacred_segment_from_policy_output(output)

                    if layer.position == LAYER_DEFINITIONS[-1].position:
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
