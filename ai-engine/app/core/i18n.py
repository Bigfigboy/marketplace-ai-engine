"""User-facing system messages in Russian, Kazakh and English."""

from __future__ import annotations

SUPPORTED_LANGUAGES: tuple[str, ...] = ("ru", "kk", "en")

MESSAGES: dict[str, dict[str, str]] = {
    "mock_banner": {
        "ru": "⚠️ MOCK-РЕЖИМ: ответ сформирован локально без обращения к языковой модели и не является результатом работы ИИ.",
        "kk": "⚠️ MOCK-РЕЖИМІ: жауап тілдік модельге жүгінбей, жергілікті түрде жасалды және ЖИ жұмысының нәтижесі емес.",
        "en": "⚠️ MOCK MODE: this response was produced locally without calling any language model and is not AI output.",
    },
    "blocked_injection": {
        "ru": "Запрос отклонён: он содержит попытку изменить системные правила агента. "
              "Пожалуйста, сформулируйте обычный вопрос.",
        "kk": "Сұрау қабылданбады: онда агенттің жүйелік ережелерін өзгертуге әрекет бар. "
              "Қарапайым сұрақ қойыңыз.",
        "en": "Request rejected: it attempts to override the agent's system rules. Please ask a regular question.",
    },
    "hr_policy_block": {
        "ru": "HR-ассистент только помогает рекрутеру и не может принимать решения о найме или отказе, "
              "ранжировать или оценивать кандидатов, а также учитывать возраст, пол, национальность, "
              "религию, семейное положение и другие личные характеристики. "
              "Я могу подготовить сводку резюме, сопоставить требования вакансии с подтверждёнными навыками, "
              "предложить вопросы для собеседования или черновик вакансии.",
        "kk": "HR-ассистент тек рекрутерге көмектеседі және жұмысқа алу немесе бас тарту туралы шешім "
              "қабылдай алмайды, үміткерлерді саралай немесе бағалай алмайды, сондай-ақ жасын, жынысын, "
              "ұлтын, дінін, отбасылық жағдайын және басқа жеке сипаттамаларын ескермейді. "
              "Мен түйіндеменің қысқаша мазмұнын дайындай аламын, вакансия талаптарын расталған "
              "дағдылармен салыстыра аламын, сұхбат сұрақтарын немесе вакансия жобасын ұсына аламын.",
        "en": "The HR assistant only supports the recruiter. It cannot make hiring or rejection decisions, "
              "rank or score candidates, or consider age, gender, nationality, religion, marital status "
              "or other personal characteristics. I can summarise a resume, map job requirements to "
              "confirmed skills, suggest interview questions or draft a job description.",
    },
    "support_no_info": {
        "ru": "В базе знаний компании нет информации для ответа на этот вопрос. "
              "Чтобы не ввести вас в заблуждение, я не буду предполагать. "
              "Подготовлен черновик обращения для менеджера компании.",
        "kk": "Компанияның білім қорында бұл сұраққа жауап беретін ақпарат жоқ. "
              "Сізді жаңылыстырмау үшін болжам жасамаймын. "
              "Компания менеджеріне арналған өтініш жобасы дайындалды.",
        "en": "The company knowledge base does not contain information to answer this question. "
              "To avoid misleading you, I will not guess. A draft request for a company manager has been prepared.",
    },
    "support_no_kb": {
        "ru": "Для компании ещё не загружена база знаний. Загрузите документы через POST /files/upload "
              "с purpose=knowledge_base.",
        "kk": "Компания үшін білім қоры әлі жүктелмеген. Құжаттарды POST /files/upload арқылы "
              "purpose=knowledge_base мәнімен жүктеңіз.",
        "en": "No knowledge base has been uploaded for this company yet. Upload documents via "
              "POST /files/upload with purpose=knowledge_base.",
    },
    "handoff_not_delivered": {
        "ru": "Интеграция с менеджером не настроена: обращение НЕ отправлено автоматически. "
              "Передайте его менеджеру через ваш CRM/helpdesk.",
        "kk": "Менеджермен интеграция бапталмаған: өтініш автоматты түрде ЖІБЕРІЛМЕДІ. "
              "Оны CRM/helpdesk арқылы менеджерге жіберіңіз.",
        "en": "No manager integration is configured: the request was NOT sent automatically. "
              "Forward it to a manager through your CRM/helpdesk.",
    },
    "marketing_draft_note": {
        "ru": "Это черновик. Агент ничего не публикует и не отправляет. Проверьте цифры, цены и факты перед использованием.",
        "kk": "Бұл жоба. Агент ештеңе жарияламайды және жібермейді. Пайдаланар алдында сандарды, бағаларды және фактілерді тексеріңіз.",
        "en": "This is a draft. The agent does not publish or send anything. Verify numbers, prices and facts before use.",
    },
    "hr_disclaimer": {
        "ru": "Материал подготовлен для помощи рекрутеру. Решение о кандидате принимает человек; "
              "оценка по личным характеристикам не проводится.",
        "kk": "Материал рекрутерге көмек ретінде дайындалды. Үміткер туралы шешімді адам қабылдайды; "
              "жеке сипаттамалар бойынша бағалау жүргізілмейді.",
        "en": "Prepared to assist the recruiter. Decisions about candidates are made by a human; "
              "no assessment based on personal characteristics is performed.",
    },
    "sources_heading": {"ru": "Источники", "kk": "Дереккөздер", "en": "Sources"},
}


def t(key: str, language: str) -> str:
    entry = MESSAGES[key]
    return entry.get(language) or entry["en"]
