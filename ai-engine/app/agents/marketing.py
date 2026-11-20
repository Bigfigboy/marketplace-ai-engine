"""AI Marketing Agent (slug: marketing).

Produces marketing drafts adapted to Kazakhstan. It never publishes or sends anything.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.core.i18n import t
from app.prompts.marketing import build_system_prompt
from app.security.prompt_guard import sanitize_document_text, wrap_untrusted

MarketingTask = Literal["auto", "audience_analysis", "marketing_strategy", "ad_copy", "content_plan",
                        "social_posts", "commercial_offer", "email_campaign", "smb_recommendations"]
Platform = Literal["instagram", "tiktok", "telegram"]


class MarketingOptions(BaseModel):
    task: MarketingTask = Field(default="auto", description="Marketing task; 'auto' detects it from the message")
    business_name: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    target_audience: str | None = Field(default=None, max_length=1000)
    platforms: list[Platform] = Field(default_factory=list, max_length=3)
    tone: str | None = Field(default=None, max_length=200)
    budget_kzt: int | None = Field(default=None, ge=0, le=10_000_000_000, description="Budget in tenge")
    period_days: int | None = Field(default=None, ge=1, le=365)


class MarketingOutput(BaseModel):
    task: str
    platforms: list[str]
    draft: bool = True
    published: bool = False
    contains_numbers_to_verify: bool


# Order matters: more specific intents first.
_TASK_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("content_plan", ("контент-план", "контент план", "content plan", "content calendar",
                      "контент-жоспар", "контент жоспар", "контент-кесте")),
    ("commercial_offer", ("коммерческ", "commercial offer", "commercial proposal", "sales proposal",
                          "коммерциялық ұсыныс", "кп для", "кп на")),
    ("email_campaign", ("email", "e-mail", "емейл", "имейл", "рассылк", "письм", "электрондық хат",
                        "newsletter")),
    ("audience_analysis", ("целев", "аудитори", "audience", "persona", "персон", "мақсатты")),
    ("marketing_strategy", ("стратеги", "strategy", "маркетинг жоспар", "marketing plan")),
    ("social_posts", ("пост", "instagram", "инстаграм", "tiktok", "тикток", "telegram", "телеграм",
                      "reels", "сторис", "stories", "post", "жазба", "жарияланым")),
    ("ad_copy", ("реклам", "ad copy", "advert", "слоган", "slogan", "жарнам", "баннер", "banner")),
    ("smb_recommendations", ("продвиж", "продвин", "раскрут", "привлечь клиент", "promot",
                             "get more customers", "ілгерілет", "клиент тарт")),
]
_PLATFORM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram", "инстаграм", "инста", "reels"),
    "tiktok": ("tiktok", "тикток", "тик-ток"),
    "telegram": ("telegram", "телеграм", "телеграмм"),
}
_NUMERIC_CLAIM_RE = re.compile(r"\d+([.,]\d+)?\s?%|\b\d+([.,]\d+)?\s?(млн|млрд|million|billion|тыс\.)", re.I)

_TASK_LABELS = {
    "audience_analysis": {"ru": "Анализ целевой аудитории", "kk": "Мақсатты аудиторияны талдау",
                          "en": "Target audience analysis"},
    "marketing_strategy": {"ru": "Маркетинговая стратегия", "kk": "Маркетингтік стратегия",
                           "en": "Marketing strategy"},
    "ad_copy": {"ru": "Рекламные тексты", "kk": "Жарнама мәтіндері", "en": "Advertising copy"},
    "content_plan": {"ru": "Контент-план", "kk": "Контент-жоспар", "en": "Content plan"},
    "social_posts": {"ru": "Публикации для соцсетей", "kk": "Әлеуметтік желілерге жарияланымдар",
                     "en": "Social media posts"},
    "commercial_offer": {"ru": "Коммерческое предложение", "kk": "Коммерциялық ұсыныс",
                         "en": "Commercial proposal"},
    "email_campaign": {"ru": "Email-кампания", "kk": "Email-науқан", "en": "Email campaign"},
    "smb_recommendations": {"ru": "Рекомендации по продвижению МСБ", "kk": "ШОБ-ты ілгерілету бойынша ұсыныстар",
                            "en": "SMB promotion recommendations"},
    "general": {"ru": "Маркетинговый запрос", "kk": "Маркетингтік сұрау", "en": "Marketing request"},
}
_MOCK_SECTIONS = {
    "ru": ["Цель и контекст (по данным из запроса)", "Целевая аудитория — гипотезы для проверки",
           "Основной черновик", "Призыв к действию", "Что уточнить: [укажите данные]"],
    "kk": ["Мақсат және контекст (сұраудағы деректер бойынша)", "Мақсатты аудитория — тексерілетін болжамдар",
           "Негізгі жоба", "Әрекетке шақыру", "Нақтылау қажет: [дерек енгізіңіз]"],
    "en": ["Goal and context (from the request)", "Target audience - hypotheses to validate",
           "Main draft", "Call to action", "To clarify: [insert data]"],
}


def detect_task(message: str) -> str:
    lowered = message.lower()
    for task, keywords in _TASK_KEYWORDS:
        if any(k in lowered for k in keywords):
            return task
    return "general"


def detect_platforms(message: str) -> list[str]:
    lowered = message.lower()
    return [p for p, kws in _PLATFORM_KEYWORDS.items() if any(k in lowered for k in kws)]


class MarketingAgent(BaseAgent):
    id = "agent-marketing-001"
    slug = "marketing"
    name = "AI Marketing Agent"
    description = ("Marketing drafts for Kazakhstan SMBs: audience analysis, strategy, ad copy, content plans, "
                   "Instagram/TikTok/Telegram posts, commercial proposals, email campaigns. Drafts only.")
    version = "1.0.0"
    category = "marketing"
    capabilities = ("audience_analysis", "marketing_strategy", "ad_copy", "content_plan", "social_posts",
                    "commercial_offer", "email_campaign", "smb_recommendations")
    accepted_file_purposes = ("brief", "knowledge_base")
    max_files = 3
    options_model = MarketingOptions
    output_model = MarketingOutput

    def run(self, message: str, options: BaseModel, ctx: AgentContext) -> AgentResult:
        assert isinstance(options, MarketingOptions)
        task = options.task if options.task != "auto" else detect_task(message)
        platforms = list(options.platforms) or detect_platforms(message)
        if task == "social_posts" and not platforms:
            platforms = ["instagram", "tiktok", "telegram"]

        brief = self._brief(options, platforms)
        blocks: list[tuple[str, str]] = []
        if brief:
            blocks.append(("business_brief", brief))
        budget = max(1000, ctx.settings.max_input_tokens // 2) // max(1, len(ctx.files) or 1)
        for f in ctx.files:
            text, trimmed = ctx.fit_text(sanitize_document_text(f.text).text, budget)
            if trimmed:
                ctx.warnings.append("attached_file_truncated")
            blocks.append((f"file:{f.filename}", text))

        user_content = message if not blocks else f"{wrap_untrusted(blocks)}\n\nREQUEST:\n{message}"
        messages = [{"role": "system", "content": build_system_prompt(ctx.language, task)},
                    *ctx.history_messages(), {"role": "user", "content": user_content}]

        result = ctx.call_llm(messages, mock_fn=lambda: self._mock(task, platforms, ctx.language))
        text = result.text.strip()
        warnings: list[str] = []
        numbers = bool(_NUMERIC_CLAIM_RE.search(text))
        if numbers:
            warnings.append("contains_numbers_verify_before_use")
        if not text:
            warnings.append("empty_model_output")
        note = t("marketing_draft_note", ctx.language)
        response = f"{text}\n\n---\n_{note}_" if result.mode != "mock" else text
        data = MarketingOutput(task=task, platforms=platforms, contains_numbers_to_verify=numbers).model_dump()
        return AgentResult(response=response, data=data, warnings=warnings)

    @staticmethod
    def _brief(o: MarketingOptions, platforms: list[str]) -> str:
        fields = [("Business name", o.business_name), ("Industry", o.industry), ("City", o.city),
                  ("Target audience", o.target_audience), ("Tone of voice", o.tone),
                  ("Budget (KZT)", f"{o.budget_kzt:,}".replace(",", " ") if o.budget_kzt is not None else None),
                  ("Period (days)", o.period_days), ("Platforms", ", ".join(platforms) or None)]
        return "\n".join(f"{k}: {v}" for k, v in fields if v not in (None, ""))

    @staticmethod
    def _mock(task: str, platforms: list[str], language: str) -> str:
        label = _TASK_LABELS.get(task, _TASK_LABELS["general"])[language]
        lines = [t("mock_banner", language), "", f"## {label}"]
        if platforms:
            lines.append(f"**Platforms:** {', '.join(platforms)}")
        lines.append("")
        lines += [f"{i}. {section}" for i, section in enumerate(_MOCK_SECTIONS[language], start=1)]
        lines += ["", f"_{t('marketing_draft_note', language)}_"]
        return "\n".join(lines)


AGENT = MarketingAgent()
