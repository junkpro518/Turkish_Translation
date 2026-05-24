import json
import re
from dataclasses import dataclass

QUALITY_PASS = "pass"
QUALITY_WARNING = "warning"
QUALITY_CRITICAL = "critical"
QUALITY_FAILED_STATUS = "quality_failed"
QUALITY_ISSUE_SEMANTIC_DRIFT = "semantic_drift"
QUALITY_ISSUE_INTERPRETIVE_EXPANSION = "interpretive_expansion"
QUALITY_ISSUE_REGISTER = "register_issue"
QUALITY_ISSUE_FLUENCY = "fluency_issue"
QUALITY_ISSUE_LANGUAGE_CONTAMINATION = "language_contamination"
QUALITY_ISSUE_STRUCTURAL = "structural_violation"
QUALITY_ISSUE_TYPES = {
    QUALITY_ISSUE_SEMANTIC_DRIFT,
    QUALITY_ISSUE_INTERPRETIVE_EXPANSION,
    QUALITY_ISSUE_REGISTER,
    QUALITY_ISSUE_FLUENCY,
    QUALITY_ISSUE_LANGUAGE_CONTAMINATION,
    QUALITY_ISSUE_STRUCTURAL,
}
QUALITY_WARNING_ONLY_ISSUES = {
    QUALITY_ISSUE_INTERPRETIVE_EXPANSION,
    QUALITY_ISSUE_REGISTER,
    QUALITY_ISSUE_FLUENCY,
}
QUALITY_CRITICAL_ISSUES = {
    QUALITY_ISSUE_SEMANTIC_DRIFT,
    QUALITY_ISSUE_LANGUAGE_CONTAMINATION,
    QUALITY_ISSUE_STRUCTURAL,
}
QUALITY_GATE_FALLBACK_FEEDBACK = "تعذر قراءة نتيجة فحص الجودة بوضوح؛ أُرسلت الترجمة مع تحذير للمراجعة."
GENERIC_QUALITY_FEEDBACK_PATTERN = re.compile(
    r"(قد\s+تغي[ّير]+?\s+المعنى|might\s+change\s+the\s+meaning|may\s+change\s+the\s+meaning|"
    r"meaning\s+may\s+be\s+changed|anlam\s+değişebilir|anlamı\s+değişebilir)",
    re.IGNORECASE,
)
CONCRETE_CRITICAL_FEEDBACK_PATTERN = re.compile(
    r"(language\s+contamination|chinese|中文|صيني|لغة\s+غير\s+متوقعة|"
    r"حذف|محذوف|omitted|missing\s+(?:meaning|condition|exception)|"
    r"قلب\s+النفي|تحويل\s+النفي|النفي\s+إلى\s+إثبات|negation|"
    r"تحويل\s+السؤال|سؤال.*تقرير|soru.*rapor|question.*statement|"
    r"دمج.*(?:ek\s*not|تعليق)|(?:ek\s*not|تعليق).*دمج|merged.*(?:ek\s*not|note)|"
    r"حذف.*(?:تعليق|فائدة)|(?:external\s+note|user\s+note).*omitted|"
    r"تغيير\s+(?:الحكم|المعنى\s+الأساسي)|semantic\s+drift\s+واضح|clear\s+semantic\s+drift|anlam\s+değişmiştir)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QualityGateResult:
    severity: str
    feedback: str = ""
    corrected_translation: str = ""
    issue_type: str = ""


def is_generic_quality_feedback(feedback: str) -> bool:
    return bool(GENERIC_QUALITY_FEEDBACK_PATTERN.search(feedback or ""))


def has_concrete_critical_feedback(feedback: str) -> bool:
    return bool(CONCRETE_CRITICAL_FEEDBACK_PATTERN.search(feedback or ""))


def calibrate_quality_severity(severity: str, issue_type: str, feedback: str) -> str:
    if issue_type in QUALITY_WARNING_ONLY_ISSUES and severity == QUALITY_CRITICAL:
        return QUALITY_WARNING
    if severity != QUALITY_CRITICAL:
        if issue_type in QUALITY_CRITICAL_ISSUES and severity == QUALITY_PASS and has_concrete_critical_feedback(feedback):
            return QUALITY_CRITICAL
        return severity
    if issue_type == QUALITY_ISSUE_LANGUAGE_CONTAMINATION:
        return QUALITY_CRITICAL
    if is_generic_quality_feedback(feedback):
        return QUALITY_WARNING
    if issue_type in {QUALITY_ISSUE_SEMANTIC_DRIFT, QUALITY_ISSUE_STRUCTURAL}:
        return QUALITY_CRITICAL if has_concrete_critical_feedback(feedback) else QUALITY_WARNING
    if not issue_type:
        return QUALITY_CRITICAL if has_concrete_critical_feedback(feedback) else QUALITY_WARNING
    return severity


def parse_quality_gate_output(text: str) -> QualityGateResult:
    try:
        data = json.loads(text or "")
    except (json.JSONDecodeError, TypeError):
        return QualityGateResult(severity=QUALITY_WARNING, feedback=QUALITY_GATE_FALLBACK_FEEDBACK)

    if not isinstance(data, dict):
        return QualityGateResult(severity=QUALITY_WARNING, feedback=QUALITY_GATE_FALLBACK_FEEDBACK)

    issue_type = str(data.get("issue_type") or "").strip().lower()
    severity = str(data.get("severity") or "").strip().lower()
    feedback = str(data.get("feedback") or "").strip()

    if not issue_type or not severity or not feedback:
        return QualityGateResult(severity=QUALITY_WARNING, feedback=QUALITY_GATE_FALLBACK_FEEDBACK)
    if issue_type not in QUALITY_ISSUE_TYPES:
        return QualityGateResult(severity=QUALITY_WARNING, feedback=QUALITY_GATE_FALLBACK_FEEDBACK)
    if severity not in {QUALITY_PASS, QUALITY_WARNING, QUALITY_CRITICAL}:
        return QualityGateResult(severity=QUALITY_WARNING, feedback=QUALITY_GATE_FALLBACK_FEEDBACK)

    severity = calibrate_quality_severity(severity, issue_type, feedback)
    return QualityGateResult(
        severity=severity,
        feedback=feedback,
        corrected_translation="",
        issue_type=issue_type,
    )
