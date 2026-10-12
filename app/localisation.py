import json
from pathlib import Path

from babel import Locale
from babel.numbers import format_decimal


LANGUAGES = ('en', 'fr', 'de')
CATALOGUES = {language: json.loads((Path(__file__).parent / 'static/locales' / f'{language}.json').read_text())
              for language in LANGUAGES}


def translate(key, language='en', **values):
    language = language if language in LANGUAGES else 'en'
    pattern = CATALOGUES[language].get(key, CATALOGUES['en'].get(key, key))
    if isinstance(pattern, dict):
        pattern = pattern.get(Locale.parse(language).plural_form(values.get('count', 0)), pattern['other'])
    formatted = {name: format_decimal(value, locale=language, decimal_quantization=False, group_separator=False)
                 if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
                 for name, value in values.items()}
    return pattern.format_map(formatted)
