import re


SPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")


RULES = [
    (
        "Прайс размещён только в публикациях",
        (
            r"\bпрайс\w*\b|\bцен\w*\b",
            r"\bпост\w*\b|\bпубликац\w*\b|\bсообществ\w*\b|\bгрупп\w*\b|\bзакреп\w*\b",
        ),
    ),
    (
        "Запись ведётся вручную через сообщения",
        (
            r"\bзапис\w*\b|\bпри[её]м\w*\b",
            r"\bлс\b|\bличн\w*\s+сообщ\w*\b|\bдирект\w*\b|\bdirect\b|\bпишите\b|\bwhatsapp\b|\bватсап\w*\b|\btelegram\b|\bтелеграм\w*\b",
        ),
    ),
    (
        "Нет отдельного сайта",
        (
            r"\bсайт\w*\s+нет\b|\bнет\s+(?:своего\s+)?сайт\w*\b|\bбез\s+сайт\w*\b",
        ),
    ),
    (
        "Услуги представлены только в соцсетях",
        (
            r"\bуслуг\w*\b|\bпроцедур\w*\b",
            r"\bсоцсет\w*\b|\bсоциальн\w*\b|\bсообществ\w*\b|\bгрупп\w*\b|\bпрофил\w*\b",
        ),
    ),
    (
        "Сайт или страница услуг устарели",
        (
            r"\bсайт\w*\b|\bстраниц\w*\b|\bлендинг\w*\b",
            r"\bустарел\w*\b|\bнеактуальн\w*\b",
        ),
    ),
]


STOP_WORDS = {
    "нет",
    "есть",
    "лида",
    "лиде",
    "какую",
    "какая",
    "какой",
    "искать",
    "через",
    "только",
    "для",
    "или",
}


def clean_text(value: str | None) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def split_sentences(text: str) -> list[str]:
    return [
        clean_text(part)
        for part in SENTENCE_RE.split(text)
        if clean_text(part)
    ]


def evidence_text(sentence: str, limit: int = 240) -> str:
    sentence = clean_text(sentence).strip(' "\'«»')

    if len(sentence) <= limit:
        return sentence

    return sentence[: limit - 1].rstrip() + "…"


def detect_pain(
    text: str | None,
    target_pain: str = "",
) -> str:
    cleaned = clean_text(text)

    if not cleaned:
        return "не найден"

    sentences = split_sentences(cleaned)

    for sentence in sentences:
        lowered = sentence.lower()

        for label, required_patterns in RULES:
            if all(
                re.search(pattern, lowered)
                for pattern in required_patterns
            ):
                return (
                    f"{label}\n"
                    f"Подтверждение: «{evidence_text(sentence)}»"
                )

    keywords = [
        word
        for word in re.findall(
            r"[a-zа-яё0-9]+",
            target_pain.lower(),
        )
        if len(word) >= 4
        and word not in STOP_WORDS
    ]

    for sentence in sentences:
        lowered = sentence.lower()

        matched = [
            word
            for word in keywords
            if word in lowered
        ]

        if len(matched) >= 2:
            label = clean_text(target_pain).rstrip(" .")

            if label:
                label = label[0].upper() + label[1:]

            return (
                f"{label}\n"
                f"Подтверждение: «{evidence_text(sentence)}»"
            )

    return "не найден"
