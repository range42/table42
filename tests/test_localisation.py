import io
import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from test_exercise_flow import post, publish, response_payload, world


def test_translation_catalogues_have_matching_keys_and_placeholders():
    directory = Path(__file__).resolve().parent.parent / 'app/static/locales'
    catalogues = {}
    for language in ('en', 'fr', 'de'):
        path = directory / f'{language}.json'
        assert path.exists(), f'Missing {language} catalogue'
        catalogues[language] = json.loads(path.read_text())
    for language, entries in catalogues.items():
        assert entries.keys() == catalogues['en'].keys(), language
        for key, value in entries.items():
            reference = catalogues['en'][key]
            assert isinstance(value, type(reference)), (language, key)
            variants = value.values() if isinstance(value, dict) else [value]
            originals = reference.values() if isinstance(reference, dict) else [reference]
            assert all(isinstance(text, str) and text.strip() for text in variants)
            assert {tuple(sorted(re.findall(r'\{(\w+)\}', text))) for text in variants} == {
                tuple(sorted(re.findall(r'\{(\w+)\}', text))) for text in originals
            }, (language, key)
    for source in ('app/static/app.js', 'app/static/i18n.js', 'app/reports.py'):
        text = (directory.parents[2] / source).read_text()
        referenced = set(re.findall(r"\b(?:t|tr|message)\(['\"]([\w.]+)['\"]", text))
        assert not {key for key in referenced if not key.endswith('.') and key not in catalogues['en']}, source


@pytest.mark.parametrize('language,heading,latest,summary', [
    ('fr', 'Rapport de réponses', 'Dernières réponses', 'min=0; max=5,5; moyenne=2,75'),
    ('de', 'Antwortbericht', 'Aktuelle Antworten', 'min=0; max=5,5; Mittelwert=2,75'),
])
def test_pdf_language_changes_labels_and_number_formats_without_translating_answers(
        world, language, heading, latest, summary):
    publish(world)
    eid, iid = world['exercise']['id'], world['inject']['id']
    for user, count in [('alice', 0), ('bob', 5.5)]:
        post(world[user], f'/api/injects/{iid}/responses', response_payload(answers={
            **response_payload()['answers'], 'count': count, 'assessment': 'Keep this answer exactly as authored.',
        }))
    result = world['admin'].get(f'/api/exercises/{eid}/report.pdf?lang={language}')
    assert result.status_code == 200
    text = re.sub(r'\s+', ' ', ' '.join(page.extract_text() for page in PdfReader(io.BytesIO(result.content)).pages))
    assert heading in text
    assert latest in text
    assert summary in text
    assert 'Harbour incident' in text
    assert 'Your assessment' in text
    assert 'Keep this answer exactly as authored.' in text
    assert world['alice'].get(f'/api/exercises/{eid}/report.pdf?lang={language}').status_code == 403


def test_pdf_rejects_an_unsupported_language(world):
    eid = world['exercise']['id']
    assert world['admin'].get(f'/api/exercises/{eid}/report.pdf?lang=xx').status_code == 422
