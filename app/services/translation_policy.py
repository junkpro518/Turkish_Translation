from dataclasses import dataclass

from app.services.layers import (
    TRANSLATION_MODE_COMIC,
    TRANSLATION_MODE_GENERAL,
    TRANSLATION_MODE_LEGAL,
    TRANSLATION_MODE_LITERARY,
    TRANSLATION_MODE_MARKETING,
    TRANSLATION_MODE_SACRED,
    normalize_translation_mode,
)


@dataclass(frozen=True)
class TranslationPolicy:
    mode: str
    allowed_freedom_level: str
    allow_paraphrase: bool
    preserve_structure: bool
    preserve_sentence_type: bool
    preserve_negation: bool
    preserve_conditionals: bool
    preserve_exceptions: bool
    preserve_certainty_level: bool
    allow_interpretive_expansion: bool
    register_style: str
    forbidden_transformations: tuple[str, ...]
    review_checklist: tuple[str, ...]
    quality_gate_required: bool = False

    def to_prompt(self) -> str:
        bool_label = {True: "نعم", False: "لا"}
        forbidden = "\n".join(f"- {item}" for item in self.forbidden_transformations)
        checklist = "\n".join(f"- {item}" for item in self.review_checklist)
        return (
            f"TRANSLATION POLICY ENGINE\n"
            f"mode: {self.mode}\n"
            f"allowed_freedom_level: {self.allowed_freedom_level}\n"
            f"allow_paraphrase: {bool_label[self.allow_paraphrase]}\n"
            f"preserve_structure: {bool_label[self.preserve_structure]}\n"
            f"preserve_sentence_type: {bool_label[self.preserve_sentence_type]}\n"
            f"preserve_negation: {bool_label[self.preserve_negation]}\n"
            f"preserve_conditionals: {bool_label[self.preserve_conditionals]}\n"
            f"preserve_exceptions: {bool_label[self.preserve_exceptions]}\n"
            f"preserve_certainty_level: {bool_label[self.preserve_certainty_level]}\n"
            f"allow_interpretive_expansion: {bool_label[self.allow_interpretive_expansion]}\n"
            f"register_style: {self.register_style}\n\n"
            f"Forbidden transformations:\n{forbidden}\n\n"
            f"Review checklist:\n{checklist}"
        )


GENERAL_POLICY = TranslationPolicy(
    mode=TRANSLATION_MODE_GENERAL,
    allowed_freedom_level="medium",
    allow_paraphrase=True,
    preserve_structure=False,
    preserve_sentence_type=True,
    preserve_negation=True,
    preserve_conditionals=True,
    preserve_exceptions=True,
    preserve_certainty_level=True,
    allow_interpretive_expansion=False,
    register_style="طبيعي ومباشر حسب السياق",
    forbidden_transformations=(
        "إضافة معنى غير موجود في النص الأصلي.",
        "حذف قيد أو استثناء مهم.",
        "تغيير النبرة أو درجة الرسمية دون سبب سياقي.",
    ),
    review_checklist=(
        "هل المعنى الأساسي محفوظ؟",
        "هل النفي والشرط والاستثناء محفوظة؟",
        "هل درجة اليقين أو الاحتمال لم تتغير؟",
        "هل الأسلوب مناسب لاستخدام النص؟",
    ),
)

COMIC_POLICY = TranslationPolicy(
    mode=TRANSLATION_MODE_COMIC,
    allowed_freedom_level="medium-high",
    allow_paraphrase=True,
    preserve_structure=False,
    preserve_sentence_type=True,
    preserve_negation=True,
    preserve_conditionals=True,
    preserve_exceptions=True,
    preserve_certainty_level=True,
    allow_interpretive_expansion=False,
    register_style="حوار طبيعي قصير مناسب لفقاعات الكلام",
    forbidden_transformations=(
        "شرح النكتة أو التعبير داخل الترجمة.",
        "تحويل الحوار إلى لغة أكاديمية أو رسمية إلا إذا كانت الشخصية كذلك.",
        "إطالة الجمل بما يجعلها غير مناسبة لفقاعة الكلام.",
        "تغيير شخصية المتكلم أو نبرة الغضب أو الخوف أو السخرية أو التهديد أو المزاح أو التردد.",
        "معاملة جزء ديني حساس داخل الحوار كأنه حوار عادي.",
    ),
    review_checklist=(
        "هل تبدو الجملة كحوار حقيقي؟",
        "هل الجملة قصيرة ومناسبة للفقاعة؟",
        "هل بقيت شخصية المتكلم ونبرته واضحة؟",
        "هل بقي المعنى دون شرح زائد؟",
        "إذا ظهر جزء sacred، هل عومل بسياسة sacred لذلك الجزء؟",
    ),
)

SACRED_POLICY = TranslationPolicy(
    mode=TRANSLATION_MODE_SACRED,
    allowed_freedom_level="low",
    allow_paraphrase=False,
    preserve_structure=True,
    preserve_sentence_type=True,
    preserve_negation=True,
    preserve_conditionals=True,
    preserve_exceptions=True,
    preserve_certainty_level=True,
    allow_interpretive_expansion=False,
    register_style="تركية دينية معاصرة ومفهومة بدون ثقل عثماني",
    forbidden_transformations=(
        "إعادة الصياغة الحرة أو إدخال تفسير داخل FINAL_TRANSLATION.",
        "إضافة معنى غير موجود أو حذف قيد أو شرط أو استثناء.",
        "اختصار العبارات التشريفية والدعائية مثل عز وجل، سبحانه وتعالى، صلى الله عليه وسلم، رضي الله عنه، رضي الله عنهما، رحمه الله.",
        "استخدام اختصارات مثل s.a.v. أو r.a. أو cc أو rh.a. إلا إذا طلب المستخدم ذلك صراحة.",
        "تحويل السؤال إلى تقرير أو الجواب إلى سؤال.",
        "تحويل النفي إلى احتمال أو الشرط إلى تقرير أو الاستثناء إلى تعميم.",
        "تحويل اليقين إلى احتمال أو الاحتمال إلى يقين.",
        "تحويل الأمر إلى نصيحة أو النهي إلى تفضيل.",
        "تحويل العموم إلى تخصيص أو التخصيص إلى عموم.",
        "تلطيف الترجمة إذا غيّر ذلك المعنى.",
        "إضافة أقواس تفسيرية داخل FINAL_TRANSLATION؛ إذا احتاج النص توضيحًا فليكن في WARNINGS أو EK NOT وليس داخل الترجمة الأساسية.",
        "استخدام مصطلحات ثقيلة مثل amel-i salih, cihad-ı fîsebilillah, ziyade notu, mezkûr, mezbûr, işbu, binaenaleyh.",
        "دمج تعليق المستخدم داخل متن حديث أو آية أو دعاء.",
        "حذف تعليق المستخدم بدل وضعه في EK NOT عند وجوده.",
    ),
    review_checklist=(
        "هل FINAL_TRANSLATION يحتوي المتن الأصلي فقط؟",
        "هل EK NOT يحتوي تعليق المستخدم أو الفائدة الإضافية فقط، ويبدأ عند وجوده بـ Bu açıklama metnin aslından değil, ek bir nottur؟",
        "هل السؤال بقي سؤالًا والجواب بقي جوابًا؟",
        "هل النفي والشرط والاستثناء محفوظة؟",
        "هل درجة اليقين أو الاحتمال محفوظة؟",
        "هل العبارات التشريفية والدعائية لم تختصر؟",
        "هل لا توجد صياغة تفسيرية داخل FINAL_TRANSLATION؟",
        "هل التعليق الخارجي، إن وجد، موجود في EK NOT فقط؟",
        "هل المصطلحات الثابتة محفوظة عند الحاجة: عز وجل = azze ve celle، سبحانه وتعالى = sübhânehu ve teâlâ، صلى الله عليه وسلم = sallallahu aleyhi ve sellem، رضي الله عنه = radıyallahu anh، رضي الله عنهما = radıyallahu anhüma، رحمه الله = rahimehullah؟",
        "هل استُخدم salih amel وAllah yolunda cihad وek not بدل المصطلحات الثقيلة عند الحاجة؟",
        "هل استُخدم Allah katında بدل التركيب غير الطبيعي Allah'a nezdinde؟",
        "هل استُخدمت تركية دينية معاصرة ومفهومة وتجنب النص تراكيب ثقيلة مثل salih amelden daha sevimli bir amel yoktur؟",
        "عند معنى تفضيل العمل الصالح في أيام معينة، فضّل صياغة طبيعية مثل bu günlerde yapılan salih amellerden daha sevimli hiçbir amel yoktur عند ملاءمتها للسياق.",
        "هل BRIEF_REASON قصير ومحايد ووصفي، مثل: تم الحفاظ على البنية الشرعية للنص ومنع التفسير داخل الترجمة الأساسية؟",
    ),
    quality_gate_required=True,
)

LEGAL_POLICY = TranslationPolicy(
    mode=TRANSLATION_MODE_LEGAL,
    allowed_freedom_level="low",
    allow_paraphrase=False,
    preserve_structure=True,
    preserve_sentence_type=True,
    preserve_negation=True,
    preserve_conditionals=True,
    preserve_exceptions=True,
    preserve_certainty_level=True,
    allow_interpretive_expansion=False,
    register_style="قانوني دقيق وواضح",
    forbidden_transformations=(
        "اختصار النص القانوني أو حذف شرط أو قيد أو استثناء.",
        "تغيير قوة الالتزام القانوني.",
        "تحويل الالتزام القانوني إلى كلام عام.",
        "إضافة تفسير قانوني غير موجود في النص.",
        "تغيير النفي أو الشرط أو الاستثناء أو درجة اليقين.",
    ),
    review_checklist=(
        "هل الالتزامات والقيود والاستثناءات محفوظة؟",
        "هل قوة الإلزام لم تتغير؟",
        "هل النفي والشرط والاستثناء بقيت كما هي؟",
        "هل لا يوجد تفسير قانوني مضاف؟",
    ),
    quality_gate_required=True,
)

MARKETING_POLICY = TranslationPolicy(
    mode=TRANSLATION_MODE_MARKETING,
    allowed_freedom_level="medium-high",
    allow_paraphrase=True,
    preserve_structure=False,
    preserve_sentence_type=False,
    preserve_negation=True,
    preserve_conditionals=True,
    preserve_exceptions=True,
    preserve_certainty_level=True,
    allow_interpretive_expansion=False,
    register_style="طبيعي ومقنع دون إضافة وعود",
    forbidden_transformations=(
        "إضافة وعد أو ميزة أو سعر أو قيد غير موجود.",
        "تغيير قوة الادعاء التسويقي.",
        "حذف قيود العرض أو شروطه.",
    ),
    review_checklist=(
        "هل بقي الوعد التسويقي كما هو دون مبالغة؟",
        "هل لم يتغير السعر أو المزايا أو القيود؟",
        "هل الأسلوب مقنع وطبيعي في اللغة الهدف؟",
    ),
)

LITERARY_POLICY = TranslationPolicy(
    mode=TRANSLATION_MODE_LITERARY,
    allowed_freedom_level="medium",
    allow_paraphrase=True,
    preserve_structure=False,
    preserve_sentence_type=True,
    preserve_negation=True,
    preserve_conditionals=True,
    preserve_exceptions=True,
    preserve_certainty_level=True,
    allow_interpretive_expansion=False,
    register_style="أدبي يحافظ على الأثر دون اختراع معنى",
    forbidden_transformations=(
        "اختراع صورة أو معنى غير موجود.",
        "شرح الغموض المقصود داخل الترجمة.",
        "التضحية بالمعنى الأساسي من أجل البلاغة.",
    ),
    review_checklist=(
        "هل المعنى والنبرة محفوظان؟",
        "هل الأثر الأدبي طبيعي في اللغة الهدف؟",
        "هل لم تُخترع صور أو معان جديدة؟",
    ),
)

POLICIES = {
    TRANSLATION_MODE_GENERAL: GENERAL_POLICY,
    TRANSLATION_MODE_COMIC: COMIC_POLICY,
    TRANSLATION_MODE_SACRED: SACRED_POLICY,
    TRANSLATION_MODE_LEGAL: LEGAL_POLICY,
    TRANSLATION_MODE_MARKETING: MARKETING_POLICY,
    TRANSLATION_MODE_LITERARY: LITERARY_POLICY,
}


def get_translation_policy(mode: str | None) -> TranslationPolicy:
    return POLICIES[normalize_translation_mode(mode)]


def build_policy_prompt(mode: str | None, has_sacred_segment: bool = False) -> str:
    normalized_mode = normalize_translation_mode(mode)
    prompts = [get_translation_policy(normalized_mode).to_prompt()]
    if has_sacred_segment and normalized_mode != TRANSLATION_MODE_SACRED:
        prompts.append(
            "Sacred segment guard:\n"
            "طبّق السياسة التالية على الجزء الديني/الشرعي الحساس فقط دون تحويل النص كله إلى sacred إذا كان سياقه العام مختلفًا.\n\n"
            f"{SACRED_POLICY.to_prompt()}"
        )
    return "\n\n".join(prompts)
