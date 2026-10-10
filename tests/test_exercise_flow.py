import csv
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def post(client, url, payload=None, **kwargs):
    session = client.get('/api/session').json()
    headers = {'X-CSRF-Token': session.get('csrf_token') or ''}
    return client.post(url, json=payload, headers=headers, **kwargs)


@pytest.fixture
def world(tmp_path):
    app = create_app(tmp_path)
    admin = TestClient(app)
    setup = post(admin, '/api/setup', {'username': 'admin', 'name': 'Director', 'password': 'director-password'})
    assert setup.status_code == 201
    users = []
    clients = []
    for username in ['alice', 'bob', 'outsider']:
        user = post(admin, '/api/users', {'username': username, 'name': username.title(), 'password': 'participant-password'})
        assert user.status_code == 201
        users.append(user.json())
        client = TestClient(app)
        assert post(client, '/api/login', {'username': username, 'password': 'participant-password'}).status_code == 200
        clients.append(client)
    exercise = post(admin, '/api/exercises', {'name': 'Harbour incident', 'description': 'A crisis exercise'}).json()
    group = post(admin, f"/api/exercises/{exercise['id']}/groups", {'name': 'Blue team'}).json()
    for user in users[:2]:
        assert post(admin, f"/api/exercises/{exercise['id']}/members", {
            'user_id': user['id'], 'role': 'participant', 'group_id': group['id']
        }).status_code == 200
    inject = post(admin, f"/api/exercises/{exercise['id']}/injects", {
        'name': 'Initial alert', 'description': 'Investigate the alert',
        'questions': [
            {'id': 'assessment', 'label': 'Your assessment', 'type': 'long_text', 'required': True},
            {'id': 'severity', 'label': 'Severity', 'type': 'single_choice', 'options': ['Low', 'High'], 'required': True},
            {'id': 'actions', 'label': 'Actions', 'type': 'multiple_choice', 'options': ['Contain', 'Notify']},
            {'id': 'count', 'label': 'Affected systems', 'type': 'number'},
            {'id': 'confirmed', 'label': 'Confirmed?', 'type': 'boolean'},
            {'id': 'date', 'label': 'Date', 'type': 'date'},
        ]
    }).json()
    return {'app': app, 'admin': admin, 'alice': clients[0], 'bob': clients[1], 'outsider': clients[2],
            'exercise': exercise, 'inject': inject, 'group': group, 'users': users, 'data_dir': tmp_path}


def publish(world):
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    assert post(admin, f'/api/exercises/{eid}/status', {'status': 'live'}).status_code == 200
    assert post(admin, f'/api/injects/{iid}/publication', {'published': True}).status_code == 200


def response_payload(**updates):
    return {'revision': 0, 'answers': {
        'assessment': 'Isolate the affected system', 'severity': 'High', 'actions': ['Contain'],
        'count': 2, 'confirmed': False, 'date': '2026-10-09'
    }, **updates}


def test_setup_is_once_only_and_authentication_uses_private_session(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    assert client.get('/api/session').json()['needs_setup'] is True
    assert post(client, '/api/setup', {'username': 'admin', 'name': 'Admin', 'password': 'long-password'}).status_code == 201
    cookie = client.cookies.get('range42_session')
    assert cookie
    assert client.get('/api/session').json()['user']['is_admin'] is True
    assert post(TestClient(app), '/api/setup', {'username': 'other', 'name': 'Other', 'password': 'long-password'}).status_code == 409
    assert post(client, '/api/logout').status_code == 200
    assert client.get('/api/session').json()['user'] is None
    assert post(client, '/api/login', {'username': 'admin', 'password': 'wrong-password'}).status_code == 401
    assert post(client, '/api/login', {'username': 'admin', 'password': 'long-password'}).status_code == 200


def test_participants_only_see_published_injects_and_cannot_direct(world):
    eid, iid = world['exercise']['id'], world['inject']['id']
    assert world['alice'].get(f'/api/exercises/{eid}').json()['injects'] == []
    assert post(world['alice'], f'/api/injects/{iid}/publication', {'published': True}).status_code == 403
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 404
    publish(world)
    assert len(world['alice'].get(f'/api/exercises/{eid}').json()['injects']) == 1
    assert post(world['admin'], f'/api/injects/{iid}/publication', {'published': False}).status_code == 200
    assert world['alice'].get(f'/api/exercises/{eid}').json()['injects'] == []


def test_membership_and_csrf_are_enforced_server_side(world):
    eid = world['exercise']['id']
    assert world['outsider'].get(f'/api/exercises/{eid}').status_code == 404
    assert world['outsider'].get('/api/exercises').json() == []
    assert post(world['alice'], '/api/users', {'username': 'evil', 'name': 'Evil', 'password': 'long-password'}).status_code == 403
    assert world['admin'].post('/api/exercises', json={'name': 'No token'}).status_code == 403
    assert world['admin'].post('/api/login', json={'username': 'admin', 'password': 'director-password'},
                               headers={'Origin': 'https://evil.example'}).status_code == 403


def test_each_member_authors_a_separate_response_counted_for_their_group(world):
    publish(world)
    iid = world['inject']['id']
    first = post(world['alice'], f'/api/injects/{iid}/responses', response_payload())
    assert first.status_code == 200
    assert first.json()['revision'] == 1
    assert first.json()['author_name'] == 'Alice'
    assert first.json()['group_name'] == 'Blue team'
    assert world['bob'].get(f'/api/injects/{iid}/responses').json()[0]['answers']['severity'] == 'High'
    second = post(world['bob'], f'/api/injects/{iid}/responses', response_payload())
    assert second.status_code == 200
    assert second.json()['id'] != first.json()['id']
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 409
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(revision=1)).status_code == 200
    assert len(world['bob'].get(f'/api/injects/{iid}/responses').json()) == 2
    assert len(world['admin'].get(f'/api/injects/{iid}/responses').json()) == 2
    details = world['admin'].get(f"/api/exercises/{world['exercise']['id']}").json()
    assert details['groups'][0]['response_count'] == 2


def test_first_setup_rejects_an_empty_display_name(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert post(client, '/api/setup', {'username': 'admin', 'name': '   ', 'password': 'long-password'}).status_code == 422


def test_password_whitespace_is_preserved(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert post(client, '/api/setup', {'username': 'admin', 'name': 'Admin', 'password': '  long-password  '}).status_code == 201
    assert post(client, '/api/logout').status_code == 200
    assert post(client, '/api/login', {'username': 'admin', 'password': 'long-password'}).status_code == 401
    assert post(client, '/api/login', {'username': 'admin', 'password': '  long-password  '}).status_code == 200


@pytest.mark.parametrize('answers', [
    {'assessment': '', 'severity': 'High'}, {'assessment': 'OK', 'severity': 'Critical'},
    {'assessment': 'OK', 'severity': 'High', 'count': 'two'},
    {'assessment': 'OK', 'severity': 'High', 'actions': ['Delete']},
    {'assessment': 'OK', 'severity': 'High', 'confirmed': 'yes'},
    {'assessment': 'OK', 'severity': 'High', 'date': '2026-02-30'},
    {'assessment': 'OK', 'severity': 'High', 'unknown-question': 'answer'},
])
def test_question_answers_are_validated(world, answers):
    publish(world)
    assert post(world['alice'], f"/api/injects/{world['inject']['id']}/responses",
                response_payload(answers=answers)).status_code == 422


def test_closing_preserves_responses_and_blocks_edits(world):
    publish(world)
    iid, eid = world['inject']['id'], world['exercise']['id']
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    assert post(world['admin'], f'/api/exercises/{eid}/status', {'status': 'closed'}).status_code == 200
    assert len(world['alice'].get(f'/api/injects/{iid}/responses').json()) == 1
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(revision=1)).status_code == 409


def test_attachments_follow_inject_visibility_and_group_access_without_shared_editing(world):
    iid = world['inject']['id']
    admin = world['admin']
    token = admin.get('/api/session').json()['csrf_token']
    upload = admin.post(f'/api/injects/{iid}/attachments', files={'file': ('brief.txt', b'Scenario facts', 'text/plain')},
                        headers={'X-CSRF-Token': token})
    assert upload.status_code == 201
    aid = upload.json()['id']
    assert world['alice'].get(f'/api/attachments/{aid}').status_code == 404
    publish(world)
    assert world['alice'].get(f'/api/attachments/{aid}').content == b'Scenario facts'
    assert world['outsider'].get(f'/api/attachments/{aid}').status_code == 404
    response = post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).json()
    alice = world['alice']
    token = alice.get('/api/session').json()['csrf_token']
    evidence = alice.post(f"/api/responses/{response['id']}/attachments",
                         files={'file': ('evidence.txt', b'Private evidence', 'text/plain')}, headers={'X-CSRF-Token': token})
    assert evidence.status_code == 201
    assert world['bob'].get(f"/api/attachments/{evidence.json()['id']}").content == b'Private evidence'
    bob_token = world['bob'].get('/api/session').json()['csrf_token']
    assert world['bob'].post(f"/api/responses/{response['id']}/attachments",
                             files={'file': ('other.txt', b'Other evidence', 'text/plain')},
                             headers={'X-CSRF-Token': bob_token}).status_code == 403


def test_export_includes_answers_attribution_and_attachment_files(world):
    publish(world)
    iid, eid = world['inject']['id'], world['exercise']['id']
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    token = world['admin'].get('/api/session').json()['csrf_token']
    assert world['admin'].post(f'/api/injects/{iid}/attachments', files={'file': ('brief.txt', b'Facts', 'text/plain')},
                               headers={'X-CSRF-Token': token}).status_code == 201
    assert world['alice'].get(f'/api/exercises/{eid}/export.zip').status_code == 403
    archive = world['admin'].get(f'/api/exercises/{eid}/export.zip')
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        data = json.loads(bundle.read('exercise.json'))
        rows = list(csv.DictReader(io.StringIO(bundle.read('responses.csv').decode('utf-8-sig'))))
        assert data['exercise']['name'] == 'Harbour incident'
        assert data['responses'][0]['author_name'] == 'Alice'
        assert data['responses'][0]['group_name'] == 'Blue team'
        assert any(row['answer'] == 'High' for row in rows)
        assert any(bundle.read(name) == b'Facts' for name in bundle.namelist() if name.startswith('attachments/'))
        assert 'password' not in bundle.read('exercise.json').decode()


def test_original_group_attribution_survives_membership_changes(world):
    publish(world)
    iid, eid = world['inject']['id'], world['exercise']['id']
    reply = post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).json()
    group = post(world['admin'], f'/api/exercises/{eid}/groups', {'name': 'Red team'}).json()
    assert post(world['admin'], f'/api/exercises/{eid}/members', {
        'user_id': world['users'][0]['id'], 'role': 'participant', 'group_id': group['id']
    }).status_code == 200
    assert world['alice'].get(f'/api/injects/{iid}/responses').json()[0]['group_name'] == 'Blue team'
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(revision=reply['revision'])).status_code == 200
    assert world['admin'].get(f'/api/injects/{iid}/responses').json()[0]['group_name'] == 'Blue team'


def test_data_survives_restart(world):
    publish(world)
    other = TestClient(create_app(world['data_dir']))
    assert post(other, '/api/login', {'username': 'alice', 'password': 'participant-password'}).status_code == 200
    assert other.get(f"/api/exercises/{world['exercise']['id']}").json()['injects'][0]['name'] == 'Initial alert'


def test_browser_shell_is_served_locally(tmp_path):
    client = TestClient(create_app(tmp_path))
    page = client.get('/')
    assert page.status_code == 200
    assert 'Range42' in page.text
    assert client.get('/static/app.js').status_code == 200


def test_director_role_is_exercise_scoped(world):
    eid, iid = world['exercise']['id'], world['inject']['id']
    assert post(world['admin'], f'/api/exercises/{eid}/members', {
        'user_id': world['users'][1]['id'], 'role': 'director', 'group_id': world['group']['id']
    }).status_code == 200
    assert world['bob'].get(f'/api/exercises/{eid}').json()['is_director'] is True
    assert post(world['bob'], f'/api/injects/{iid}/publication', {'published': True}).status_code == 200
    assert world['bob'].get('/api/users').status_code == 403
    other = post(world['admin'], '/api/exercises', {'name': 'Other exercise'}).json()
    assert world['bob'].get(f"/api/exercises/{other['id']}").status_code == 404


def test_unassigned_participants_do_not_share_private_replies(world):
    publish(world)
    eid, iid = world['exercise']['id'], world['inject']['id']
    for user in [world['users'][0], world['users'][2]]:
        assert post(world['admin'], f'/api/exercises/{eid}/members', {
            'user_id': user['id'], 'role': 'participant', 'group_id': None
        }).status_code == 200
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    assert world['outsider'].get(f'/api/injects/{iid}/responses').json() == []
    assert world['bob'].get(f'/api/injects/{iid}/responses').json() == []


def test_question_schema_locks_after_a_response_but_description_can_be_edited(world):
    publish(world)
    iid = world['inject']['id']
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    payload = {k: world['inject'][k] for k in ('name', 'description', 'questions')}
    payload['description'] = 'Updated clarification'
    assert post(world['admin'], f'/api/injects/{iid}/details', payload).status_code == 200
    payload['questions'] = []
    assert post(world['admin'], f'/api/injects/{iid}/details', payload).status_code == 409


def test_password_reset_revokes_sessions_and_cookie_is_http_only(world):
    uid = world['users'][0]['id']
    result = post(world['admin'], f'/api/users/{uid}/password', {'password': 'replacement-password'})
    assert result.status_code == 200
    assert world['alice'].get('/api/session').json()['user'] is None
    result = post(world['alice'], '/api/login', {'username': 'alice', 'password': 'replacement-password'})
    assert result.status_code == 200
    assert 'httponly' in result.headers['set-cookie'].lower()
    assert 'samesite=strict' in result.headers['set-cookie'].lower()
    assert post(world['alice'], '/api/password', {
        'current_password': 'replacement-password', 'new_password': 'another-password'
    }).status_code == 200
    assert post(TestClient(world['app']), '/api/login', {
        'username': 'alice', 'password': 'another-password'
    }).status_code == 200


def test_login_rate_limit_persists_between_requests(world):
    client = TestClient(world['app'])
    for _ in range(10):
        assert post(client, '/api/login', {'username': 'alice', 'password': 'incorrect-password'}).status_code == 401
    assert post(client, '/api/login', {'username': 'alice', 'password': 'participant-password'}).status_code == 429


def test_removing_members_revokes_exercise_and_attachment_access(world):
    publish(world)
    eid, iid = world['exercise']['id'], world['inject']['id']
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    assert post(world['admin'], f"/api/exercises/{eid}/members/{world['users'][0]['id']}/remove").status_code == 200
    assert world['alice'].get(f'/api/exercises/{eid}').status_code == 404
    assert world['alice'].get(f'/api/injects/{iid}/responses').status_code == 404
    assert len(world['admin'].get(f'/api/injects/{iid}/responses').json()) == 1


def test_media_uploads_and_rejections(world, monkeypatch):
    monkeypatch.setenv('RANGE42_MAX_UPLOAD_MB', '1')
    admin = TestClient(create_app(world['data_dir']))
    assert post(admin, '/api/login', {'username': 'admin', 'password': 'director-password'}).status_code == 200
    token = admin.get('/api/session').json()['csrf_token']
    iid = world['inject']['id']
    for name, media, content in [('alert.png', 'image/png', b'\x89PNG\r\n\x1a\n'),
                                 ('news.mp4', 'video/mp4', b'\x00\x00\x00\x18ftypmp42')]:
        result = admin.post(f'/api/injects/{iid}/attachments', files={'file': (name, content, media)},
                            headers={'X-CSRF-Token': token})
        assert result.status_code == 201
        result = admin.get(f"/api/attachments/{result.json()['id']}")
        assert result.headers['content-type'] == media
        assert result.headers['content-disposition'].startswith('inline;')
    for name, content, code in [('bad.html', b'<script>alert(1)</script>', 422),
                                 ('empty.txt', b'', 422), ('large.txt', b'x' * (1024 * 1024 + 1), 413)]:
        result = admin.post(f'/api/injects/{iid}/attachments', files={'file': (name, content, 'text/plain')},
                            headers={'X-CSRF-Token': token})
        assert result.status_code == code
    assert len(list((world['data_dir'] / 'uploads').iterdir())) == 2


def test_extreme_numbers_are_rejected_instead_of_crashing(world):
    publish(world)
    payload = response_payload()
    payload['answers']['count'] = 10 ** 400
    assert post(world['alice'], f"/api/injects/{world['inject']['id']}/responses", payload).status_code == 422


def test_directors_can_preview_draft_inject_answers_without_creating_responses(world):
    iid, eid = world['inject']['id'], world['exercise']['id']
    result = post(world['admin'], f'/api/injects/{iid}/preview', {'answers': response_payload()['answers']})
    assert result.status_code == 200
    assert result.json()['ok'] is True
    assert world['admin'].get(f'/api/injects/{iid}/responses').json() == []
    detail = world['admin'].get(f'/api/exercises/{eid}').json()
    assert detail['status'] == 'draft'
    assert detail['injects'][0]['published'] is False
    assert world['alice'].get(f'/api/exercises/{eid}').json()['injects'] == []
    with world['app'].state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM response_history').fetchone()[0] == 0


def test_preview_validates_questions_and_requires_exercise_director(world):
    iid = world['inject']['id']
    invalid = post(world['admin'], f'/api/injects/{iid}/preview', {'answers': {}})
    assert invalid.status_code == 422
    publish(world)
    assert post(world['alice'], f'/api/injects/{iid}/preview', {'answers': response_payload()['answers']}).status_code == 403
    assert post(world['outsider'], f'/api/injects/{iid}/preview', {'answers': response_payload()['answers']}).status_code == 404
    assert TestClient(world['app']).post(f'/api/injects/{iid}/preview', json={'answers': {}}).status_code == 401
    assert world['admin'].get(f'/api/injects/{iid}/responses').json() == []


def test_structured_questions_validate_lists_rankings_matrices_and_word_limits(world):
    questions = [
        {'id': 'facts', 'label': 'Three facts', 'type': 'list', 'required': True, 'max_items': 3},
        {'id': 'order', 'label': 'Priorities', 'type': 'ranking', 'required': True, 'options': ['Urgent', 'Routine']},
        {'id': 'table', 'label': 'Situation', 'type': 'matrix', 'required': True,
         'rows': ['Service'], 'columns': ['Facts', 'Owner']},
        {'id': 'message', 'label': 'Statement', 'type': 'long_text', 'required': True, 'max_words': 3},
    ]
    result = post(world['admin'], f"/api/exercises/{world['exercise']['id']}/injects", {'name': 'Structured', 'questions': questions})
    assert result.status_code == 201
    iid = result.json()['id']
    answers = {'facts': ['Check access', 'Verify contact'], 'order': {'order': ['Urgent', 'Routine'], 'explanation': 'Time sensitive'},
               'table': {'Service': {'Facts': 'Unavailable', 'Owner': 'Coordinator'}}, 'message': 'We are investigating'}
    assert post(world['admin'], f'/api/injects/{iid}/preview', {'answers': answers}).status_code == 200
    for key, value in [('facts', ['a','b','c','d']), ('order', {'order': ['Urgent','Urgent'], 'explanation': 'Reason'}),
                       ('table', {'Service': {'Facts': 'Unavailable'}}), ('message', 'This statement is too long')]:
        assert post(world['admin'], f'/api/injects/{iid}/preview', {'answers': {**answers, key: value}}).status_code == 422
    publish(world)
    assert post(world['admin'], f'/api/injects/{iid}/publication', {'published': True}).status_code == 200
    assert post(world['alice'], f'/api/injects/{iid}/responses', {'revision': 0, 'answers': answers}).status_code == 200
    assert world['bob'].get(f'/api/injects/{iid}/responses').json()[0]['answers'] == answers


def test_information_updates_do_not_require_or_accept_a_response(world):
    eid = world['exercise']['id']
    result = post(world['admin'], f'/api/exercises/{eid}/injects', {'name': 'Supplier update', 'information_only': True})
    assert result.status_code == 201
    iid = result.json()['id']
    publish(world)
    assert post(world['admin'], f'/api/injects/{iid}/publication', {'published': True}).status_code == 200
    assert post(world['alice'], f'/api/injects/{iid}/responses', {'revision': 0, 'answers': {}}).status_code == 409
    assert world['admin'].get(f'/api/injects/{iid}/responses').json() == []
