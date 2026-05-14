from __future__ import annotations

from typing import Any


PATTERN = "pattern"
ANTI_PATTERN = "anti_pattern"
RULE = "rule"
CONTEXT = "context"
CHECKLIST = "checklist"
PAST_WIN = "past_win"
PREFERENCE = "preference"
CONSTRAINT = "constraint"
DECISION_MEMORY = "decision_memory"
DONT_REPEAT = "dont_repeat"
CODEMAP = "codemap"
EMOTIONAL_FEEDBACK = "emotional_feedback"
ORG_CONVENTION = "org_convention"

BASE_KNOWLEDGE_KINDS = (PATTERN, ANTI_PATTERN, RULE, CONTEXT, CHECKLIST)
LOCAL_PRIOR_KINDS = (
    PAST_WIN,
    PREFERENCE,
    CONSTRAINT,
    DECISION_MEMORY,
    DONT_REPEAT,
    CODEMAP,
    EMOTIONAL_FEEDBACK,
    ORG_CONVENTION,
)
HIGH_PRIORITY_PRIOR_KINDS = (DONT_REPEAT, CONSTRAINT, ORG_CONVENTION, EMOTIONAL_FEEDBACK, PREFERENCE)
GOVERNANCE_FOCUS_PRIOR_KINDS = (EMOTIONAL_FEEDBACK, ORG_CONVENTION)
CANONICAL_KNOWLEDGE_KINDS = (*BASE_KNOWLEDGE_KINDS, *LOCAL_PRIOR_KINDS)

KIND_RANKING_WEIGHT = {
    DONT_REPEAT: 0.32,
    CONSTRAINT: 0.3,
    ORG_CONVENTION: 0.29,
    EMOTIONAL_FEEDBACK: 0.29,
    PREFERENCE: 0.28,
    DECISION_MEMORY: 0.24,
    CODEMAP: 0.22,
    RULE: 0.2,
    CONTEXT: 0.16,
    PAST_WIN: 0.16,
    PATTERN: 0.14,
    CHECKLIST: 0.1,
    ANTI_PATTERN: 0.08,
}

ACTIVATION_LABELS = {
    PAST_WIN: "历史成功路径",
    PREFERENCE: "用户偏好",
    CONSTRAINT: "项目约束",
    DECISION_MEMORY: "历史决策",
    DONT_REPEAT: "长期指令",
    CODEMAP: "代码地图",
    EMOTIONAL_FEEDBACK: "情绪反馈",
    ORG_CONVENTION: "组织约定",
}

TITLE_LABELS = {
    PAST_WIN: "历史成功路径",
    PREFERENCE: "用户偏好",
    CONSTRAINT: "项目约束",
    DECISION_MEMORY: "历史决策",
    DONT_REPEAT: "长期指令",
    CODEMAP: "代码地图",
    EMOTIONAL_FEEDBACK: "情绪反馈",
    ORG_CONVENTION: "组织约定",
}

_KIND_SIGNAL_PHRASES = (
    (DONT_REPEAT, ("不要重复", "不想重复", "以后别再", "不用每次问", "不要让我再解释", "别让我再解释", "don't ask again", "do not ask again")),
    (
        EMOTIONAL_FEEDBACK,
        (
            "骂 ai",
            "骂ai",
            "夸 ai",
            "夸ai",
            "用户骂",
            "用户夸",
            "用户很生气",
            "用户情绪",
            "情绪化",
            "吐槽 ai",
            "吐槽ai",
            "很烦",
            "太烦",
            "崩溃",
            "做得很好",
            "干得漂亮",
            "非常好用",
            "很省心",
        ),
    ),
    (
        ORG_CONVENTION,
        (
            "项目约定",
            "项目习惯",
            "公司约定",
            "公司内部",
            "内部组件",
            "自有资源",
            "兄弟项目",
            "demo",
            "约定俗成",
            "内部用法",
            "公司套路",
            "项目套路",
            "团队习惯",
            "团队约定",
            "组织约定",
            "organization convention",
            "internal component",
        ),
    ),
    (PREFERENCE, ("我喜欢", "我习惯", "默认用", "默认就", "不要用", "不要这样", "我偏好", "prefer", "preference")),
    (CONSTRAINT, ("必须", "不能", "线上风险", "兼容", "团队顾虑", "不要提交", "不提交", "public api", "api 契约", "guardrail")),
    (DECISION_MEMORY, ("历史原因", "当时是因为", "之前设计", "设计成这样", "迁移遗留", "远古设计", "historical reason", "legacy")),
)

_PAST_WIN_SIGNALS = ("以前", "上次", "验证过", "成功", "之前就是这样修", "worked before", "previously worked")

_NEGATIVE_EMOTIONAL_SIGNALS = ("骂", "生气", "情绪化", "吐槽", "很烦", "太烦", "崩溃", "不爽", "火大")
_POSITIVE_EMOTIONAL_SIGNALS = ("夸", "做得很好", "干得漂亮", "非常好用", "很省心", "舒服", "满意")
_PROFANITY_FRAGMENTS = (
    "傻逼",
    "煞笔",
    "sb",
    "shit",
    "fuck",
    "垃圾",
)

_ORG_SOURCE_CONTEXT_SIGNALS = (
    ("sibling_project", ("兄弟项目", "sibling project")),
    ("demo", ("demo", "示例项目", "样例项目")),
    ("internal_component", ("内部组件", "自有组件", "公司组件", "internal component")),
    ("company_convention", ("公司内部", "公司约定", "公司套路", "自有资源", "company convention")),
    ("team_convention", ("团队习惯", "团队约定", "team convention")),
    ("project_convention", ("项目约定", "项目习惯", "项目套路", "约定俗成", "内部用法")),
)


def build_prior_signal_text(episode: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("goal", "lesson", "verification", "user_feedback", "scope_hint"):
        value = episode.get(key)
        if value:
            parts.append(str(value))
    for key in ("constraints", "turning_points", "attempted_paths", "abandoned_paths", "decision_rationale"):
        values = episode.get(key) or []
        if isinstance(values, list):
            parts.extend(str(value) for value in values if value)
    result = episode.get("result")
    if isinstance(result, dict):
        parts.extend(str(value) for value in result.values() if value)
    elif result:
        parts.append(str(result))
    return "\n".join(parts)


def infer_local_prior_kind(episode: dict[str, Any]) -> str | None:
    text = build_prior_signal_text(episode).lower()
    for kind, phrases in _KIND_SIGNAL_PHRASES:
        if any(phrase.lower() in text for phrase in phrases):
            return kind
    if episode.get("result") == "success" and any(phrase.lower() in text for phrase in _PAST_WIN_SIGNALS):
        return PAST_WIN
    return None


def sanitize_emotional_feedback_content(content: str) -> str:
    text = " ".join(str(content).split())
    lower_text = text.lower()
    polarity = "mixed"
    if any(signal.lower() in lower_text for signal in _NEGATIVE_EMOTIONAL_SIGNALS):
        polarity = "negative"
    elif any(signal.lower() in lower_text for signal in _POSITIVE_EMOTIONAL_SIGNALS):
        polarity = "positive"

    sanitized = text
    for fragment in _PROFANITY_FRAGMENTS:
        sanitized = sanitized.replace(fragment, "[redacted]")
        sanitized = sanitized.replace(fragment.upper(), "[redacted]")

    if polarity == "negative":
        prefix = "用户强烈负面反馈：将原始情绪归纳为协作边界，后续应避免重复触发。"
    elif polarity == "positive":
        prefix = "用户强烈正向反馈：将原始夸赞归纳为可复用协作偏好，后续应优先复用。"
    else:
        prefix = "用户高情绪反馈：应归纳为偏好、边界或 dont_repeat 规则，而不是原样保存情绪表达。"

    if sanitized:
        return f"{prefix} 归纳依据：{sanitized}"
    return prefix


def infer_org_source_context(content: str, *, explicit_kind: str | None = None, explicit_ref: str | None = None) -> dict[str, Any]:
    text = str(content)
    lower_text = text.lower()
    matched: list[str] = []
    inferred_kind = explicit_kind
    for kind, signals in _ORG_SOURCE_CONTEXT_SIGNALS:
        hits = [signal for signal in signals if signal.lower() in lower_text]
        if hits:
            matched.extend(hits)
            if inferred_kind is None:
                inferred_kind = kind
    return {
        "kind": inferred_kind or "unspecified",
        "ref": explicit_ref,
        "matched_signals": matched,
    }


def activation_label_for_kind(knowledge_kind: str) -> str | None:
    return ACTIVATION_LABELS.get(knowledge_kind)


def title_label_for_kind(knowledge_kind: str) -> str | None:
    return TITLE_LABELS.get(knowledge_kind)


def ranking_weight_for_kind(knowledge_kind: str, fallback_kind: str = "") -> float:
    return KIND_RANKING_WEIGHT.get(knowledge_kind, KIND_RANKING_WEIGHT.get(fallback_kind, 0.0))
