from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime

from bs4 import BeautifulSoup

SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"\S+")

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

SUMMARY_FILLER = (
    "Consulta la fuente oficial para confirmar alcance fechas de vigencia obligaciones "
    "autoridades responsables requisitos excepciones procedimientos y efectos jurídicos "
    "aplicables al caso concreto"
)


def clean_text(value: str) -> str:
    decoded = html.unescape(value or "")
    text = (
        BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)
        if "<" in decoded
        else decoded
    )
    return SPACE_RE.sub(" ", text).strip()


def normalized(value: str) -> str:
    value = clean_text(value).lower()
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def words(value: str) -> list[str]:
    return WORD_RE.findall(clean_text(value))


def exactly_30_words(value: str) -> str:
    selected = words(value)
    filler = words(SUMMARY_FILLER)
    while len(selected) < 30:
        selected.extend(filler)
    selected = selected[:30]
    return " ".join(selected).rstrip(".,;:") + "."


def concise_title(value: str, maximum_words: int = 12) -> str:
    selected = words(value)[:maximum_words]
    title = " ".join(selected).strip(" .,:;-")
    return title[:1].upper() + title[1:] if title else "Actualización regulatoria"


def parse_date(value: str, default: date | None = None) -> date:
    raw = clean_text(value).lower().replace(",", "")
    for pattern in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            pass

    match = re.search(
        r"(\d{1,2})\s+de\s+(" + "|".join(SPANISH_MONTHS) + r")\s+de\s+(\d{4})",
        raw,
    )
    if match:
        day, month, year = match.groups()
        return date(int(year), SPANISH_MONTHS[month], int(day))

    if default is not None:
        return default
    raise ValueError(f"Fecha no reconocida: {value!r}")
