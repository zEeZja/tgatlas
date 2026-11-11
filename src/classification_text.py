"""Remove contact/registration boilerplate before extracting classification evidence."""

import re

CONTACT = re.compile(
    r"(?i)(?:по (?:вопросам )?(?:реклам|сотрудничеств)|разместить реклам|реклам(?:а|ный отдел)\s*[:👉📩]|для связи\s*[:👉]|обратная связь\s*[:👉]|регистраци[яи] в|зарегистрирован|заявление (?:на|ркн)|реестр ркн|\bркн\s*[:№]|\bpr\s*:)"
)
LINKS = re.compile(
    r"https?://\S+|\b(?:t\.me|vk\.com|www\.)/??\S*|[\w.+-]+@[\w.-]+\.\w+|@[\w]+", re.I
)


def informative_description(value):
    lines = []
    for line in str(value or "").splitlines():
        line = CONTACT.split(line, maxsplit=1)[0]
        line = LINKS.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)
