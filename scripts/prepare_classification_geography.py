"""Подготовить открытый справочник названий стран (не сбор Telegram)."""

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import requests
    from src.file_io import atomic_write_text

    locales = {}
    sources = []
    for language in ("ru", "en"):
        url = f"https://raw.githubusercontent.com/unicode-org/cldr-json/48.0.0/cldr-json/cldr-localenames-full/main/{language}/territories.json"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        if len(response.content) > 100000:
            raise ValueError("Неожиданный размер справочника")
        locales[language] = response.json()["main"][language]["localeDisplayNames"][
            "territories"
        ]
        sources.append(
            dict(url=url, sha256=hashlib.sha256(response.content).hexdigest())
        )
    countries = []
    for code, name in sorted(locales["ru"].items()):
        if (
            len(code) != 2
            or not code.isalpha()
            or code in {"ZZ", "EU", "EZ", "UN", "QO", "XA", "XB"}
        ):
            continue
        names = {name, locales["en"].get(code, name)}
        if name.endswith("ия") and len(name[:-2]) < 4:
            names.update(name[:-2] + ending for ending in ("ии", "ию", "ией"))
        if code == "RU":
            names.update(["РФ", "Российская Федерация", "Российской Федерации"])
        if code == "US":
            names.update(
                ["США", "Соединенные Штаты", "Соединенные Штаты Америки", "USA"]
            )
        if code == "GB":
            names.update(["Великобритания", "Британия", "United Kingdom", "UK"])
        if code == "AE":
            names.add("ОАЭ")
        if code == "CN":
            names.add("КНР")
        countries.append(
            dict(id="country:" + code, country=code, label=name, aliases=sorted(names))
        )
    if len(countries) < 240:
        raise ValueError("Справочник стран неполон")
    result = dict(
        format="atlas-country-reference-v11",
        version="cldr-48",
        countries=countries,
        sources=sources,
        license="Unicode-3.0",
        license_url="https://www.unicode.org/license.txt",
        note="Названия стран и территорий по CLDR. Это справочник идентификаторов, не гарантия распознавания или утверждение о границах.",
    )
    atomic_write_text(
        ROOT / "config/countries_v11.json",
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    )
    print("Countries/territories:", len(countries))


if __name__ == "__main__":
    main()
