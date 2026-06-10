"""Centralized language support for extraction heuristics and templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List
import re


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    label: str
    correction_markers: tuple[str, ...]
    constraint_markers: tuple[str, ...]
    plan_markers: tuple[str, ...]
    confirmation_markers: tuple[str, ...]
    concise_markers: tuple[str, ...]
    no_modification_markers: tuple[str, ...]
    template: Dict[str, str]


TEMPLATE_EN = {
    "when_header": "## When to Use",
    "checklist_header": "## Checklist",
    "procedure_header": "## Procedure",
    "verification_header": "## Verification",
    "pitfalls_header": "## Pitfalls",
    "apply_when": "Apply when:",
    "assistant_about_to": "- The assistant is about to {action}...",
    "user_prefers": "- User shows preference for {preference}",
    "default_when": "Apply in relevant situations.",
    "verify_action": "- [ ] Verify: am I about to {action}...? If yes, stop and reassess.",
    "confirm_preference": "- [ ] Confirm: user's preference '{preference}' is being followed.",
    "default_check": "- [ ] Review user preferences before responding.",
    "instead": "1. Instead of: {wrong}...",
    "do_this": "2. Do this: {right}...",
    "default_procedure": "Follow user's demonstrated preferences.",
    "verification": "- Check: Does my response align with the user's historical preferences?\n- Check: If the user were to review my response, would they need to correct me?\n- If uncertain about any preference, ask for confirmation rather than proceeding.",
    "pitfall_intro": "- Ignoring user corrections from previous conversations.",
    "repeating": "- Repeating: {action}...",
}

TEMPLATE_ZH = {
    "when_header": "## 使用场景",
    "checklist_header": "## 检查清单",
    "procedure_header": "## 执行流程",
    "verification_header": "## 验证",
    "pitfalls_header": "## 常见错误",
    "apply_when": "在这些场景使用：",
    "assistant_about_to": "- 助手准备这样做：{action}...",
    "user_prefers": "- 用户偏好：{preference}",
    "default_when": "在相关场景中使用。",
    "verify_action": "- [ ] 检查：我是否准备这样做：{action}...？如果是，先停下来重新判断。",
    "confirm_preference": "- [ ] 确认：已经遵守用户偏好 `{preference}`。",
    "default_check": "- [ ] 回复前先检查用户偏好。",
    "instead": "1. 不要这样做：{wrong}...",
    "do_this": "2. 应该这样做：{right}...",
    "default_procedure": "遵循用户已经表达过的偏好。",
    "verification": "- 检查：回复是否符合用户历史偏好？\n- 检查：如果用户审阅这次回复，是否还需要纠正我？\n- 如果不确定偏好，先询问确认，不要直接行动。",
    "pitfall_intro": "- 忽略用户在历史对话中的纠正。",
    "repeating": "- 重复错误做法：{action}...",
}


LANGUAGES: Dict[str, LanguageProfile] = {
    "en": LanguageProfile(
        code="en",
        label="English",
        correction_markers=("no,", "actually", "you should", "wrong", "incorrect", "instead", "not like that"),
        constraint_markers=("must", "always", "never", "only", "must not", "do not", "don't"),
        plan_markers=("plan", "outline", "approach", "proposal", "first"),
        confirmation_markers=("confirm", "approve", "approval", "before you", "wait for"),
        concise_markers=("concise", "shorter", "brief", "too long"),
        no_modification_markers=("do not modify", "don't modify", "read-only", "do not edit", "don't edit"),
        template=TEMPLATE_EN,
    ),
    "zh-Hans": LanguageProfile(
        code="zh-Hans",
        label="简体中文",
        correction_markers=("不对", "错了", "应该", "不要直接", "请先", "不要", "重新", "不是", "改正", "修改", "调整", "等一下"),
        constraint_markers=("必须", "一定要", "只能", "不要", "禁止", "务必", "一定", "不能", "别"),
        plan_markers=("计划", "方案", "规划", "先", "步骤", "思路"),
        confirmation_markers=("确认", "同意", "批准", "我确认", "等我确认"),
        concise_markers=("简洁", "简短", "太长", "短一点"),
        no_modification_markers=("不要改", "别改", "不要修改", "只读", "别动文件"),
        template=TEMPLATE_ZH,
    ),
    "ja": LanguageProfile(
        code="ja",
        label="日本語",
        correction_markers=("違う", "間違い", "ではなく", "先に", "やめて", "修正して", "待って"),
        constraint_markers=("必ず", "絶対", "しないで", "禁止", "だけ", "のみ"),
        plan_markers=("計画", "方針", "手順", "先に", "プラン"),
        confirmation_markers=("確認", "承認", "許可", "同意"),
        concise_markers=("簡潔", "短く", "長すぎ"),
        no_modification_markers=("変更しないで", "編集しないで", "読み取り専用"),
        template=TEMPLATE_EN,
    ),
    "ko": LanguageProfile(
        code="ko",
        label="한국어",
        correction_markers=("아니", "틀렸", "먼저", "하지 마", "수정", "기다려"),
        constraint_markers=("반드시", "항상", "절대", "하지 마", "금지", "만"),
        plan_markers=("계획", "방안", "절차", "먼저", "플랜"),
        confirmation_markers=("확인", "승인", "동의", "허락"),
        concise_markers=("간결", "짧게", "너무 길"),
        no_modification_markers=("수정하지 마", "편집하지 마", "읽기 전용"),
        template=TEMPLATE_EN,
    ),
    "fr": LanguageProfile(
        code="fr",
        label="Français",
        correction_markers=("non", "faux", "tu devrais", "plutôt", "attends", "corrige"),
        constraint_markers=("dois", "toujours", "jamais", "seulement", "ne pas", "interdit"),
        plan_markers=("plan", "approche", "d'abord", "étapes"),
        confirmation_markers=("confirmer", "confirmation", "approuver", "accord"),
        concise_markers=("concis", "court", "trop long"),
        no_modification_markers=("ne modifie pas", "ne change pas", "lecture seule"),
        template=TEMPLATE_EN,
    ),
    "ar": LanguageProfile(
        code="ar",
        label="العربية",
        correction_markers=("لا", "خطأ", "يجب", "بدلاً", "انتظر", "صحح"),
        constraint_markers=("يجب", "دائماً", "أبداً", "فقط", "لا تفعل", "ممنوع"),
        plan_markers=("خطة", "نهج", "أولاً", "خطوات"),
        confirmation_markers=("تأكيد", "وافق", "موافقة", "إذن"),
        concise_markers=("مختصر", "قصير", "طويل جداً"),
        no_modification_markers=("لا تعدل", "لا تغير", "للقراءة فقط"),
        template=TEMPLATE_EN,
    ),
    "de": LanguageProfile(
        code="de",
        label="Deutsch",
        correction_markers=("nein", "falsch", "du solltest", "stattdessen", "warte", "korrigiere"),
        constraint_markers=("muss", "immer", "niemals", "nur", "nicht", "verboten"),
        plan_markers=("plan", "ansatz", "zuerst", "schritte"),
        confirmation_markers=("bestätigen", "genehmigen", "zustimmen", "freigabe"),
        concise_markers=("knapp", "kurz", "zu lang"),
        no_modification_markers=("nicht ändern", "nicht bearbeiten", "nur lesen"),
        template=TEMPLATE_EN,
    ),
    "ru": LanguageProfile(
        code="ru",
        label="Русский",
        correction_markers=("нет", "неправильно", "следует", "лучше", "подожди", "исправь"),
        constraint_markers=("должен", "всегда", "никогда", "только", "нельзя", "запрещено"),
        plan_markers=("план", "подход", "сначала", "шаги"),
        confirmation_markers=("подтверди", "подтверждение", "одобрение", "согласие"),
        concise_markers=("кратко", "короче", "слишком длинно"),
        no_modification_markers=("не изменяй", "не редактируй", "только чтение"),
        template=TEMPLATE_EN,
    ),
}


def get_profile(code: str | None) -> LanguageProfile:
    return LANGUAGES.get(code or "", LANGUAGES["en"])


def detect_language(text: str) -> str:
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u0600-\u06ff]", text):
        return "ar"
    if re.search(r"[\u0400-\u04ff]", text):
        return "ru"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh-Hans"
    lower = text.lower()
    if any(token in lower for token in (" le ", " la ", " les ", " des ", " tu devrais ", " d'abord ")):
        return "fr"
    if any(token in lower for token in (" der ", " die ", " das ", " nicht ", " zuerst ", " solltest ")):
        return "de"
    return "en"


def detect_messages_language(messages: Iterable[dict]) -> str:
    user_text = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "user"
    )
    return detect_language(user_text)


def all_markers(attr: str) -> List[str]:
    markers: List[str] = []
    for profile in LANGUAGES.values():
        markers.extend(getattr(profile, attr))
    return sorted(set(markers), key=len, reverse=True)
