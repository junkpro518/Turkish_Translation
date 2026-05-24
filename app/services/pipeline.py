import re
import time
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
    TRANSLATION_MODE_LEGAL,
    TRANSLATION_MODE_SACRED,
    direction_label,
    mode_label,
    normalize_translation_mode,
)
from app.services.openrouter import OpenRouterClient
from app.services.quality_gate import (
    QUALITY_CRITICAL,
    QUALITY_FAILED_STATUS,
    QUALITY_ISSUE_FLUENCY,
    QUALITY_ISSUE_INTERPRETIVE_EXPANSION,
    QUALITY_ISSUE_LANGUAGE_CONTAMINATION,
    QUALITY_ISSUE_REGISTER,
    QUALITY_ISSUE_SEMANTIC_DRIFT,
    QUALITY_ISSUE_STRUCTURAL,
    QUALITY_ISSUE_TYPES,
    QUALITY_PASS,
    QUALITY_WARNING,
    QualityGateResult,
    parse_quality_gate_output,
)
from app.services.translation_policy import get_translation_policy

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
RELIGIOUS_SOURCE_PATTERN = re.compile(
    r"(رواه|قال\s+رسول|رسول\s+الله|النبي|ﷺ|حديث|دعاء|آية|قال\s+الله|تعالى|رضي\s+الله)",
    re.IGNORECASE,
)
EXTRA_NOTE_MARKER_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:ملاحظة|تنبيه|فائدة|تعليق|ملحوظة|note|not|ek\s+not)\s*[:：-]\s*",
    re.IGNORECASE,
)
EXTRA_NOTE_SENTENCE_PATTERN = re.compile(
    r"(حتى\s+إ?ن|وهذا|وهذه|المقصود|تعليقي|ملاحظتي|فائدة|تنبيه|not:|note:)",
    re.IGNORECASE,
)
SECTION_MARKERS = ["FINAL_TRANSLATION", "EK NOT", "BRIEF_REASON", "WARNINGS"]
INCOMPLETE_WARNING_ENDINGS = (
    "kesinlikle",
    "bu yüzden",
    "çünkü",
    "ancak",
    "fakat",
    "ama",
)
def split_sacred_text(source_text: str) -> tuple[str, str]:
    text = source_text.strip()
    marker_match = EXTRA_NOTE_MARKER_PATTERN.search(text)
    if marker_match:
        sacred_source_text = text[: marker_match.start()].strip()
        user_extra_note = text[marker_match.end() :].strip()
        if sacred_source_text and user_extra_note and RELIGIOUS_SOURCE_PATTERN.search(sacred_source_text):
            return sacred_source_text, user_extra_note

    parts = re.split(r"\n\s*\n+", text, maxsplit=1)
    if len(parts) == 2:
        sacred_source_text = parts[0].strip()
        user_extra_note = parts[1].strip()
        if sacred_source_text and user_extra_note and RELIGIOUS_SOURCE_PATTERN.search(sacred_source_text):
            return sacred_source_text, user_extra_note

    if RELIGIOUS_SOURCE_PATTERN.search(text):
        sentence_parts = re.split(r"(?<=[.!؟?。])\s+", text, maxsplit=1)
        if len(sentence_parts) == 2 and EXTRA_NOTE_SENTENCE_PATTERN.search(sentence_parts[1]):
            return sentence_parts[0].strip(), sentence_parts[1].strip()

    return text, ""


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

    sacred_source_text = ""
    user_extra_note = ""
    if effective_mode == TRANSLATION_MODE_SACRED or has_sacred_segment:
        sacred_source_text, user_extra_note = split_sacred_text(source_text)

    sacred_split_instruction = ""
    if sacred_source_text and user_extra_note:
        sacred_split_instruction = (
            "تقسيم النص الديني قبل الترجمة:\n"
            f"sacred_source_text:\n{sacred_source_text}\n\n"
            f"user_extra_note:\n{user_extra_note}\n\n"
            "في SACRED mode: ترجم sacred_source_text فقط داخل FINAL_TRANSLATION، "
            "وترجم user_extra_note فقط داخل EK NOT، ولا تحذف user_extra_note ولا تدمجه في متن الحديث أو الآية أو الدعاء.\n\n"
        )
    elif effective_mode == TRANSLATION_MODE_SACRED or has_sacred_segment:
        sacred_split_instruction = (
            "تقسيم النص الديني قبل الترجمة:\n"
            "لم يتم اكتشاف تعليق خارجي منفصل بثقة. لا تضف EK NOT إلا إذا ظهر تعليق خارجي فعلي في النص أو في تحليلات الطبقات.\n\n"
        )

    return (
        f"اتجاه الترجمة: {direction_label(direction)}\n\n"
        f"وضع الترجمة الذي اختاره المستخدم: {mode_label(requested_mode)} ({requested_mode})\n"
        f"وضع الترجمة المستخدم في هذه الطبقة: {mode_label(effective_mode)} ({effective_mode})\n"
        f"هل يوجد جزء ديني/شرعي حساس داخل النص: {'نعم' if has_sacred_segment else 'لا'}\n\n"
        f"{sacred_split_instruction}"
        f"النص الأصلي:\n{source_text}\n\n"
        f"تحليلات/نتائج الطبقات السابقة:\n{previous}\n\n"
        "التزم بدورك فقط، واكتب مخرجا واضحا قابلا للمراجعة في لوحة التحكم."
    )


def extract_section(text: str, marker: str) -> str:
    marker_index = text.find(marker)
    if marker_index == -1:
        return ""
    after = text[marker_index + len(marker) :]
    after = after.lstrip(" :\n\r\t-")
    end = len(after)
    for next_marker in SECTION_MARKERS:
        if next_marker == marker:
            continue
        idx = after.find(next_marker)
        if idx != -1:
            end = min(end, idx)
    return after[:end].strip(" \n\r\t:-")


def is_empty_section(text: str) -> bool:
    normalized = (text or "").strip().strip("-:،.").lower()
    return normalized in {"", "لا يوجد", "none", "n/a", "yok", "yoktur"}


def is_complete_warning(text: str) -> bool:
    warning = (text or "").strip()
    if is_empty_section(warning):
        return False
    normalized = warning.rstrip(" \n\r\t،,;:").lower()
    if not normalized:
        return False
    if normalized.endswith(INCOMPLETE_WARNING_ENDINGS):
        return False
    if not re.search(r"[.!?؟。]|(?:dir|dır|dur|dür|tir|tır|tur|tür)$", normalized):
        return False
    return True


def extract_final_translation(text: str, strict: bool = False) -> str:
    final_translation = extract_section(text, "FINAL_TRANSLATION")
    if strict and not final_translation:
        return ""
    return final_translation or text.strip()


def requires_strict_final_translation(effective_mode: str, has_sacred_segment: bool) -> bool:
    return effective_mode in {TRANSLATION_MODE_SACRED, TRANSLATION_MODE_LEGAL} or has_sacred_segment


def extract_ek_not(text: str) -> str:
    ek_not = extract_section(text, "EK NOT")
    if is_empty_section(ek_not):
        return ""
    return ek_not


def extract_warnings(text: str) -> str:
    warnings = extract_section(text, "WARNINGS")
    if not is_complete_warning(warnings):
        return ""
    return warnings


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
                        strict_final = requires_strict_final_translation(effective_mode, has_sacred_segment)
                        request.final_translation = extract_final_translation(output, strict=strict_final)
                        if strict_final and not request.final_translation:
                            result.status = "failed"
                            result.error = "Missing FINAL_TRANSLATION section in final layer output"
                            request.status = "failed"
                            request.error = "Missing FINAL_TRANSLATION section in final layer output"
                            await session.commit()
                            if on_progress:
                                await on_progress(request, [*completed_layers, result])
                            return request
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

            request = await self.apply_quality_gate(
                session=session,
                request=request,
                completed_layers=completed_layers,
                selected_model=selected_model,
                requested_mode=requested_mode,
                effective_mode=effective_mode,
                has_sacred_segment=has_sacred_segment,
                on_progress=on_progress,
            )
            if request.status == QUALITY_FAILED_STATUS:
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

    async def apply_quality_gate(
        self,
        session: AsyncSession,
        request: TranslationRequest,
        completed_layers: list[TranslationLayerResult],
        selected_model: str,
        requested_mode: str,
        effective_mode: str,
        has_sacred_segment: bool,
        on_progress: ProgressCallback | None = None,
    ) -> TranslationRequest:
        policy = get_translation_policy(effective_mode)
        gate_required = policy.quality_gate_required or has_sacred_segment
        if not gate_required:
            return request

        final_layer = max(completed_layers, key=lambda layer: layer.position, default=None)
        if final_layer is None or not request.final_translation:
            return request

        gate_result = await self.run_quality_gate(
            source_text=request.source_text,
            final_translation=request.final_translation,
            direction=request.direction,
            requested_mode=requested_mode,
            effective_mode=effective_mode,
            has_sacred_segment=has_sacred_segment,
            model=selected_model,
        )

        if gate_result.severity == QUALITY_CRITICAL:
            retry_output = await self.openrouter.complete(
                system_prompt=f"{build_system_prompt(effective_mode, has_sacred_segment)}\n\n{LAYER_DEFINITIONS[-1].system_prompt}",
                user_prompt=self.build_quality_retry_prompt(
                    request=request,
                    completed_layers=completed_layers[:-1],
                    previous_final_output=final_layer.output_text or "",
                    quality_feedback=gate_result.feedback,
                    requested_mode=requested_mode,
                    effective_mode=effective_mode,
                    has_sacred_segment=has_sacred_segment,
                ),
                model=selected_model,
            )
            final_layer.output_text = retry_output
            request.final_translation = extract_final_translation(
                retry_output,
                strict=requires_strict_final_translation(effective_mode, has_sacred_segment),
            )
            if not request.final_translation:
                request.status = QUALITY_FAILED_STATUS
                request.error = "quality_gate retry output missing FINAL_TRANSLATION section"
                await session.commit()
                if on_progress:
                    await on_progress(request, completed_layers)
                return request
            await session.commit()
            await session.refresh(request)

            second_gate_result = await self.run_quality_gate(
                source_text=request.source_text,
                final_translation=request.final_translation or "",
                direction=request.direction,
                requested_mode=requested_mode,
                effective_mode=effective_mode,
                has_sacred_segment=has_sacred_segment,
                model=selected_model,
            )
            if second_gate_result.severity == QUALITY_CRITICAL:
                request.status = QUALITY_FAILED_STATUS
                request.error = second_gate_result.feedback or "quality_gate critical semantic drift"
                await session.commit()
                if on_progress:
                    await on_progress(request, completed_layers)
                return request

            if second_gate_result.severity == QUALITY_WARNING:
                final_layer.output_text = self.append_quality_warning(final_layer.output_text or "", second_gate_result.feedback)
                await session.commit()
            return request

        if gate_result.severity == QUALITY_WARNING:
            final_layer.output_text = self.append_quality_warning(final_layer.output_text or "", gate_result.feedback)
            await session.commit()

        return request

    async def run_quality_gate(
        self,
        source_text: str,
        final_translation: str,
        direction: str,
        requested_mode: str,
        effective_mode: str,
        has_sacred_segment: bool,
        model: str,
    ) -> QualityGateResult:
        policy = get_translation_policy(effective_mode)
        checklist = "\n".join(f"- {item}" for item in policy.review_checklist)
        forbidden = "\n".join(f"- {item}" for item in policy.forbidden_transformations)
        prompt = (
            "راجع أمانة المعنى فقط وفق Translation Policy Engine.\n"
            "أرجع JSON صالحًا فقط، بدون Markdown وبدون أي شرح خارج JSON.\n"
            "الشكل المطلوب بالضبط:\n"
            '{"issue_type":"semantic_drift","severity":"warning","feedback":"ملاحظة مختصرة ومكتملة"}'
            "\n\n"
            "issue_type يجب أن تكون واحدة فقط من هذه القائمة:\n"
            "- semantic_drift\n"
            "- interpretive_expansion\n"
            "- register_issue\n"
            "- fluency_issue\n"
            "- language_contamination\n"
            "- structural_violation\n\n"
            "severity يجب أن تكون واحدة فقط: pass أو warning أو critical.\n\n"
            "Severity calibration:\n"
            "CRITICAL فقط عند: language_contamination، تغيير المعنى الأساسي، حذف قيد أو استثناء، تحويل النفي إلى إثبات، "
            "تحويل السؤال إلى تقرير، تغيير اليقين إلى احتمال أو العكس، دمج تعليق المستخدم داخل المتن الأصلي، "
            "أو semantic_drift/structural_violation واضح في sacred/legal.\n"
            "WARNING عند: fluency_issue، register_issue، لغة عثمانية ثقيلة، naturalness issues، صياغة ثقيلة، "
            "أو interpretive_expansion بسيط مثل إضافة تفسير بين أقواس بدون تغيير جوهري للمعنى مثل (şehit olan) أو (daha sevaplı).\n"
            "PASS عند اختلافات أسلوبية بسيطة لا تغيّر المعنى.\n"
            "لا ترفض الترجمة بالكامل إذا كانت المشكلة فقط fluency_issue أو register_issue أو interpretive_expansion بسيط.\n\n"
            "Do not mark as critical for fluency, register, or awkward Turkish unless the meaning is clearly changed.\n"
            "إذا كان issue_type غير واضح أو كانت feedback عامة مثل: قد تغيّر المعنى، فاجعل severity=warning وليس critical.\n"
            "إذا لم توجد مشكلة، استخدم: "
            '{"issue_type":"fluency_issue","severity":"pass","feedback":"لا يوجد"}'
            "\n\n"
            f"اتجاه الترجمة: {direction_label(direction)}\n"
            f"mode requested: {requested_mode}\n"
            f"mode effective: {effective_mode}\n"
            f"has_sacred_segment: {has_sacred_segment}\n\n"
            f"Forbidden transformations:\n{forbidden}\n\n"
            f"Review checklist:\n{checklist}\n\n"
            f"النص الأصلي:\n{source_text}\n\n"
            f"الترجمة النهائية:\n{final_translation}\n\n"
            "أرجع JSON فقط بهذه المفاتيح الثلاثة: issue_type, severity, feedback."
        )
        output = await self.openrouter.complete(
            system_prompt=(
                "أنت quality_gate لفحص أمانة المعنى بين العربية والتركية. "
                "أرجع JSON صالحًا فقط بالمفاتيح: issue_type, severity, feedback. "
                "عاير severity بدقة: لا تجعل مشكلات الأسلوب أو التفسير الزائد البسيط critical. "
                "Do not mark as critical for fluency, register, or awkward Turkish unless the meaning is clearly changed."
            ),
            user_prompt=prompt,
            model=model,
        )
        return parse_quality_gate_output(output)

    def build_quality_retry_prompt(
        self,
        request: TranslationRequest,
        completed_layers: list[TranslationLayerResult],
        previous_final_output: str,
        quality_feedback: str,
        requested_mode: str,
        effective_mode: str,
        has_sacred_segment: bool,
    ) -> str:
        previous = "\n\n".join(
            f"## {layer.position}. {layer.name}\n{layer.output_text or layer.error or ''}" for layer in completed_layers
        )
        return (
            f"اتجاه الترجمة: {direction_label(request.direction)}\n"
            f"وضع الترجمة الذي اختاره المستخدم: {mode_label(requested_mode)} ({requested_mode})\n"
            f"وضع الترجمة المستخدم: {mode_label(effective_mode)} ({effective_mode})\n"
            f"هل يوجد جزء ديني/شرعي حساس داخل النص: {'نعم' if has_sacred_segment else 'لا'}\n\n"
            f"النص الأصلي:\n{request.source_text}\n\n"
            f"تحليلات/نتائج الطبقات السابقة:\n{previous}\n\n"
            f"مخرج الحكم النهائي السابق:\n{previous_final_output}\n\n"
            f"ملاحظات quality_gate التي يجب تصحيحها:\n{quality_feedback}\n\n"
            "أعد إصدار الحكم النهائي فقط، ولا تضف أي تفسير داخل FINAL_TRANSLATION. "
            "اجعل BRIEF_REASON قصيرًا ومحايدًا ووصفيًا، ولا تذكر أن ترجمة سابقة كانت خاطئة."
        )

    def append_quality_warning(self, final_output: str, feedback: str) -> str:
        warning = (feedback or "").strip()
        if not is_complete_warning(warning):
            return final_output
        existing = extract_warnings(final_output)
        if existing:
            return final_output
        return f"{final_output.rstrip()}\n\nWARNINGS:\n{warning}"
