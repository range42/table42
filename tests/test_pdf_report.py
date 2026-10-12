import io
import re

from pypdf import PdfReader

from test_exercise_flow import post, publish, response_payload, world


def report(world, client=None):
    result = (client or world['admin']).get(f"/api/exercises/{world['exercise']['id']}/report.pdf")
    assert result.status_code == 200, result.text
    assert result.headers['content-type'] == 'application/pdf'
    assert 'attachment;' in result.headers['content-disposition']
    assert result.headers['cache-control'] == 'no-store'
    assert result.content.startswith(b'%PDF-')
    reader = PdfReader(io.BytesIO(result.content))
    return reader, re.sub(r'\s+', ' ', ' '.join(page.extract_text() for page in reader.pages))


def test_pdf_contains_latest_attributed_answers_and_retains_unpublished_work(world):
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    publish(world)
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(
        answers={**response_payload()['answers'], 'assessment': 'Old assessment'},
    )).status_code == 200
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(
        revision=1, answers={**response_payload()['answers'], 'assessment': 'Latest assessment', 'count': 0},
    )).status_code == 200
    reply = world['alice'].get(f'/api/injects/{iid}/responses').json()[0]
    evidence = world['alice'].post(f"/api/responses/{reply['id']}/attachments",
        files={'file': ('evidence.txt', b'Saved evidence', 'text/plain')},
        headers={'X-CSRF-Token': world['alice'].get('/api/session').json()['csrf_token']})
    assert evidence.status_code == 201
    assert post(admin, f'/api/exercises/{eid}/publication', {'published': False}).status_code == 200
    assert post(admin, f'/api/exercises/{eid}/status', {'status': 'closed'}).status_code == 200
    reader, text = report(world)
    assert 'Harbour incident' in reader.metadata.title
    for phrase in ('Latest assessment', 'Alice', 'Blue team', 'Revision 2', 'evidence.txt',
                   'Affected systems', '0', 'Confirmed?', 'No', 'Unpublished', 'UTC'):
        assert phrase in text
    assert 'Old assessment' not in text
    assert 'Retained completed injects: 1' in text
    assert 'Released completion: N/A' in text  # Saved answers are retained but no work is released now.
    assert 'Retained complete' in text
    assert 'Blue team 2 1 1 N/A 1 0 / 0' in text
    with world['app'].state.store.connect() as db:
        assert db.execute('SELECT revision FROM responses').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM response_history').fetchone()[0] == 2


def test_pdf_statistics_distinguish_partial_work_information_and_unreleased_injects(world):
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    info = post(admin, f'/api/exercises/{eid}/injects', {'name': 'Briefing', 'information_only': True}).json()
    unpublished = post(admin, f'/api/exercises/{eid}/injects', {'name': 'Later decision', 'questions': [
        {'id': 'decision', 'label': 'Later decision?', 'type': 'short_text'},
    ]}).json()
    red = post(admin, f'/api/exercises/{eid}/groups', {'name': 'Red team'}).json()
    post(admin, f'/api/exercises/{eid}/members', {
        'user_id': world['users'][2]['id'], 'role': 'participant', 'group_id': red['id'],
    })
    publish(world)
    post(admin, f"/api/injects/{info['id']}/publication", {'published': True})
    post(world['alice'], f'/api/injects/{iid}/responses', response_payload(
        answers={'assessment': 'Investigate', 'severity': 'High'},
    ))
    post(world['bob'], f'/api/injects/{iid}/responses', response_payload())
    post(world['outsider'], f'/api/injects/{iid}/responses', response_payload(
        answers={'assessment': 'Coordinate', 'severity': 'Low'},
    ))
    _, text = report(world)
    for phrase in ('Total injects: 3', 'Response injects: 2', 'Information updates: 1',
                   'Current participants who replied: 3 / 3', 'Saved replies: 3',
                   'Released completion: 1 / 2 (50%)', 'Questions answered: 8 / 12',
                   'Red team', 'Blue team', 'No submitted replies', 'No answer'):
        assert phrase in text
    assert 'Later decision?' in text
    assert unpublished['id'] != info['id']


def test_pdf_keeps_submission_team_when_members_move_or_leave(world):
    publish(world)
    eid, iid, uid = world['exercise']['id'], world['inject']['id'], world['users'][0]['id']
    post(world['alice'], f'/api/injects/{iid}/responses', response_payload())
    post(world['admin'], f'/api/exercises/{eid}/members', {'user_id': uid, 'role': 'participant', 'group_id': None})
    _, text = report(world)
    assert 'Team at submission: Blue team' in text
    post(world['admin'], f'/api/exercises/{eid}/members/{uid}/remove')
    _, text = report(world)
    assert 'Team at submission: Blue team' in text
    assert 'Current participants who replied: 0 / 1' in text
    assert 'Distinct authors: 1' in text


def test_pdf_formats_structured_answers_and_unicode_as_literal_text(world):
    admin, eid = world['admin'], world['exercise']['id']
    inject = post(admin, f'/api/exercises/{eid}/injects', {
        'name': 'Équipe — décisions', 'questions': [
            {'id': 'list', 'label': 'Actions', 'type': 'list'},
            {'id': 'rank', 'label': 'Priorities', 'type': 'ranking', 'options': ['Contain', 'Recover']},
            {'id': 'table', 'label': 'Responsibilities', 'type': 'matrix',
             'rows': ['Water service'], 'columns': ['Action', 'Owner']},
            {'id': 'text', 'label': 'Comments', 'type': 'long_text'},
        ],
    }).json()
    publish(world)
    post(admin, f"/api/injects/{inject['id']}/publication", {'published': True})
    result = post(world['alice'], f"/api/injects/{inject['id']}/responses", {'revision': 0, 'answers': {
        'list': ['Call duty manager', 'Open incident log'],
        'rank': {'order': ['Recover', 'Contain'], 'explanation': 'Prioritise public service continuity'},
        'table': {'Water service': {'Action': 'Isolate pumps', 'Owner': 'Émilie'}},
        'text': '<b>Literal markup</b> & fête – évaluation',
    }})
    assert result.status_code == 200
    _, text = report(world)
    for phrase in ('Équipe — décisions', 'Call duty manager', 'Open incident log', '1. Recover', '2. Contain',
                   'Prioritise public service continuity', 'Water service', 'Isolate pumps', 'Émilie',
                   '<b>Literal markup</b> & fête – évaluation'):
        assert phrase in text
    assert '[object Object]' not in text


def test_pdf_handles_empty_exercises_and_very_long_answers(world):
    admin = world['admin']
    empty = post(admin, '/api/exercises', {'name': 'Empty exercise'}).json()
    result = admin.get(f"/api/exercises/{empty['id']}/report.pdf")
    assert result.status_code == 200
    assert 'No injects' in ''.join(page.extract_text() for page in PdfReader(io.BytesIO(result.content)).pages)
    publish(world)
    iid = world['inject']['id']
    long_text = 'Detailed finding ' * 1000 + 'END OF LONG RESPONSE'
    post(world['alice'], f'/api/injects/{iid}/responses', response_payload(
        answers={**response_payload()['answers'], 'assessment': long_text},
    ))
    reader, text = report(world)
    assert len(reader.pages) > 5
    assert 'END OF LONG RESPONSE' in text


def test_pdf_summarizes_choice_boolean_and_numeric_entries_without_scoring(world):
    publish(world)
    iid = world['inject']['id']
    post(world['alice'], f'/api/injects/{iid}/responses', response_payload(
        answers={**response_payload()['answers'], 'count': 0},
    ))
    post(world['bob'], f'/api/injects/{iid}/responses', response_payload(
        answers={**response_payload()['answers'], 'count': 10, 'confirmed': True},
    ))
    _, text = report(world)
    assert 'Numeric summary: n=2; min=0; max=10; mean=5' in text
    assert 'Choice counts: Low: 0; High: 2' in text
    assert 'Boolean counts: Yes: 1; No: 1' in text
    assert 'does not score answer quality' in text


def test_pdf_is_director_scoped_and_does_not_require_original_attachment_files(world):
    eid = world['exercise']['id']
    url = f'/api/exercises/{eid}/report.pdf'
    assert world['alice'].get(url).status_code == 403
    assert world['outsider'].get(url).status_code == 404
    publish(world)
    post(world['admin'], f'/api/exercises/{eid}/members', {
        'user_id': world['users'][1]['id'], 'role': 'director',
    })
    assert world['bob'].get(url).status_code == 200
    uploaded = world['admin'].post(f"/api/injects/{world['inject']['id']}/attachments",
        files={'file': ('brief.txt', b'Brief', 'text/plain')},
        headers={'X-CSRF-Token': world['admin'].get('/api/session').json()['csrf_token']})
    assert uploaded.status_code == 201
    for path in world['app'].state.store.uploads.iterdir():
        path.unlink()
    _, text = report(world)
    assert 'brief.txt' in text


def test_pdf_only_lists_material_from_the_active_scenario_variant(world):
    admin, iid = world['admin'], world['inject']['id']
    ids = []
    for name in ('unused.txt', 'chosen.txt'):
        response = admin.post(f'/api/injects/{iid}/attachments',
            files={'file': (name, b'Variant material', 'text/plain')},
            headers={'X-CSRF-Token': admin.get('/api/session').json()['csrf_token']})
        assert response.status_code == 201
        ids.append(response.json()['id'])
    with world['app'].state.store.connect() as db:
        db.execute("UPDATE attachments SET variant_key='default' WHERE id=?", (ids[0],))
        db.execute("UPDATE attachments SET variant_key='alternate' WHERE id=?", (ids[1],))
        db.execute("UPDATE injects SET active_variant='alternate' WHERE id=?", (iid,))
    _, text = report(world)
    assert 'chosen.txt' in text
    assert 'unused.txt' not in text
