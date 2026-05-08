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

BASE_KNOWLEDGE_KINDS = (PATTERN, ANTI_PATTERN, RULE, CONTEXT, CHECKLIST)
LOCAL_PRIOR_KINDS = (PAST_WIN, PREFERENCE, CONSTRAINT, DECISION_MEMORY, DONT_REPEAT, CODEMAP)
HIGH_PRIORITY_PRIOR_KINDS = (DONT_REPEAT, CONSTRAINT, PREFERENCE)
CANONICAL_KNOWLEDGE_KINDS = (*BASE_KNOWLEDGE_KINDS, *LOCAL_PRIOR_KINDS)

KIND_RANKING_WEIGHT = {
    DONT_REPEAT: 0.32,
    CONSTRAINT: 0.3,
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
}

TITLE_LABELS = {
    PAST_WIN: "历史成功路径",
    PREFERENCE: "用户偏好",
    CONSTRAINT: "项目约束",
    DECISION_MEMORY: "历史决策",
    DONT_REPEAT: "长期指令",
    CODEMAP: "代码地图",
}

_KIND_SIGNAL_PHRASES = (
    (DONT_REPEAT, ("不要重复", "不想重复", "以后别再", "不用每次问", "不要让我再解释", "别让我再解释", "don't ask again", "do not ask again")),
    (PREFERENCE, ("我喜欢", "我习惯", "默认用", "默认就", "不要用", "不要这样", "我偏好", "prefer", "preference")),
    (CONSTRAINT, ("必须", "不能", "线上风险", "兼容", "团队顾虑", "不要提交", "不提交", "public api", "api 契约", "guardrail")),
    (DECISION_MEMORY, ("历史原因", "当时是因为", "之前设计", "设计成这样", "迁移遗留", "远古设计", "historical reason", "legacy")),
)

_PAST_WIN_SIGNALS = ("以前", "上次", "验证过", "成功", "之前就是这样修", "worked before", "previously worked")


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


def activation_label_for_kind(knowledge_kind: str) -> str | None:
    return ACTIVATION_LABELS.get(knowledge_kind)


def title_label_for_kind(knowledge_kind: str) -> str | None:
    return TITLE_LABELS.get(knowledge_kind)


def ranking_weight_for_kind(knowledge_kind: str, fallback_kind: str = "") -> float:
    return KIND_RANKING_WEIGHT.get(knowledge_kind, KIND_RANKING_WEIGHT.get(fallback_kind, 0.0))
