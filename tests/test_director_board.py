import io
import json
import zipfile

from test_exercise_flow import post, publish, response_payload, world


def board(world):
    response = world['admin'].get(f"/api/exercises/{world['exercise']['id']}/board")
    assert response.status_code == 200
    return response.json()


def cell(world, inject_id=None, target=None):
    row = next(row for row in board(world)['injects'] if row['id'] == (inject_id or world['inject']['id']))
    return next(item for item in row['cells'] if item['target_key'] == (target or f"group:{world['group']['id']}"))


def add_red_team(world):
    eid = world['exercise']['id']
    group = post(world['admin'], f'/api/exercises/{eid}/groups', {'name': 'Red team'}).json()
    assert post(world['admin'], f'/api/exercises/{eid}/members', {
        'user_id': world['users'][2]['id'], 'role': 'participant', 'group_id': group['id'],
    }).status_code == 200
    return group


def test_director_can_release_to_one_team_without_exposing_it_to_another(world):
    red = add_red_team(world)
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    assert cell(world)['status'] == 'unpublished'
    upload = admin.post(f'/api/injects/{iid}/attachments', files={'file': ('brief.txt', b'Private brief', 'text/plain')},
                        headers={'X-CSRF-Token': admin.get('/api/session').json()['csrf_token']})
    assert upload.status_code == 201
    with world['app'].state.store.connect() as db:
        db.execute('UPDATE attachments SET variant_key=? WHERE id=?', ('default', upload.json()['id']))
    assert post(admin, f'/api/injects/{iid}/publication', {
        'published': True, 'group_id': world['group']['id'],
    }).status_code == 200
    assert world['alice'].get(f'/api/exercises/{eid}').json()['injects'] == []  # Draft exercise.
    assert post(admin, f'/api/exercises/{eid}/status', {'status': 'live'}).status_code == 200
    assert world['alice'].get(f'/api/exercises/{eid}').json()['injects'][0]['published'] is True
    assert world['alice'].get(f"/api/attachments/{upload.json()['id']}").status_code == 200
    assert world['bob'].get(f'/api/injects/{iid}/responses').status_code == 200
    assert world['outsider'].get(f'/api/exercises/{eid}').json()['injects'] == []
    assert world['outsider'].get(f'/api/injects/{iid}/responses').status_code == 404
    assert world['outsider'].get(f"/api/attachments/{upload.json()['id']}").status_code == 404
    assert post(world['outsider'], f'/api/injects/{iid}/responses', response_payload()).status_code == 404
    assert cell(world)['status'] == 'published'
    assert cell(world, target=f"group:{red['id']}")['status'] == 'unpublished'
    detail = admin.get(f'/api/exercises/{eid}').json()['injects'][0]
    assert detail['published'] is True
    assert detail['publication_scope'] == 'some'
    assert 'publication_scope' not in world['alice'].get(f'/api/exercises/{eid}').json()['injects'][0]


def test_unpublication_only_hides_the_selected_team_and_global_release_resets_overrides(world):
    red = add_red_team(world)
    publish(world)
    admin, iid = world['admin'], world['inject']['id']
    assert post(admin, f'/api/injects/{iid}/publication', {
        'published': False, 'group_id': world['group']['id'],
    }).status_code == 200
    assert world['alice'].get(f'/api/injects/{iid}/responses').status_code == 404
    assert world['outsider'].get(f'/api/injects/{iid}/responses').status_code == 200
    assert cell(world)['status'] == 'unpublished'
    assert cell(world, target=f"group:{red['id']}")['status'] == 'published'
    assert post(admin, f'/api/injects/{iid}/publication', {'published': True}).status_code == 200
    assert world['alice'].get(f'/api/injects/{iid}/responses').status_code == 200
    assert post(admin, f'/api/injects/{iid}/publication', {'published': False}).status_code == 200
    assert all(item['status'] == 'unpublished' for item in board(world)['injects'][0]['cells'])


def test_completion_requires_every_question_across_current_team_replies(world):
    publish(world)
    admin, iid = world['admin'], world['inject']['id']
    required = {'assessment': 'Investigate', 'severity': 'High'}
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(answers=required)).status_code == 200
    progress = cell(world)
    assert progress['status'] == 'published'
    assert progress['answered_count'] == 2
    assert progress['question_count'] == 6
    assert post(world['bob'], f'/api/injects/{iid}/responses', response_payload(
        answers={**response_payload()['answers'], 'count': 0, 'confirmed': False},
    )).status_code == 200
    progress = cell(world)
    assert progress['status'] == 'answered'
    assert progress['answered_count'] == 6
    assert progress['response_count'] == 2
    assert post(world['bob'], f'/api/injects/{iid}/responses', response_payload(
        revision=1, answers=required,
    )).status_code == 200
    assert cell(world)['status'] == 'published'  # Latest replies, not response history.
    assert post(world['bob'], f'/api/injects/{iid}/responses', response_payload(revision=2)).status_code == 200
    assert post(admin, f'/api/injects/{iid}/publication', {
        'published': False, 'group_id': world['group']['id'],
    }).status_code == 200
    assert cell(world)['status'] == 'unpublished'  # Grey takes precedence over completed replies.
    assert cell(world)['answered_count'] == 6


def test_structured_answers_need_every_cell_and_ranking_explanation_for_completion(world):
    admin, eid = world['admin'], world['exercise']['id']
    inject = post(admin, f'/api/exercises/{eid}/injects', {
        'name': 'Decisions', 'questions': [
            {'id': 'table', 'label': 'Decision table', 'type': 'matrix', 'rows': ['Plan'], 'columns': ['Action', 'Owner']},
            {'id': 'rank', 'label': 'Order', 'type': 'ranking', 'options': ['Contain', 'Recover']},
            {'id': 'list', 'label': 'Actions', 'type': 'list'},
        ],
    }).json()
    publish(world)
    iid = inject['id']
    assert post(admin, f'/api/injects/{iid}/publication', {'published': True}).status_code == 200
    answers = {'table': {'Plan': {'Action': 'Isolate', 'Owner': ''}},
               'rank': {'order': ['Contain', 'Recover'], 'explanation': ''}, 'list': []}
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(answers=answers)).status_code == 200
    assert cell(world, iid)['answered_count'] == 0
    answers['table']['Plan']['Owner'] = 'Operations'
    answers['rank']['explanation'] = 'Contain the issue before restoring service.'
    answers['list'] = ['Call the coordinator']
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(
        revision=1, answers=answers,
    )).status_code == 200
    assert cell(world, iid)['status'] == 'answered'
    assert cell(world, iid)['answered_count'] == 3


def test_individual_board_excludes_directors_and_releases_only_to_selected_participant(world):
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    for user in world['users'][:2]:
        assert post(admin, f'/api/exercises/{eid}/members', {
            'user_id': user['id'], 'role': 'participant', 'group_id': None,
        }).status_code == 200
    data = board(world)
    individual = [target for target in data['targets'] if target['kind'] == 'user']
    assert [target['name'] for target in individual] == ['Alice', 'Bob']
    assert post(admin, f'/api/injects/{iid}/publication', {
        'published': True, 'user_id': world['users'][0]['id'],
    }).status_code == 200
    assert post(admin, f'/api/exercises/{eid}/status', {'status': 'live'}).status_code == 200
    assert world['alice'].get(f'/api/injects/{iid}/responses').status_code == 200
    assert world['bob'].get(f'/api/injects/{iid}/responses').status_code == 404
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    assert cell(world, target=f"user:{world['users'][0]['id']}")['status'] == 'answered'
    assert cell(world, target=f"group:{world['group']['id']}")['answered_count'] == 0


def test_board_uses_submission_group_even_after_membership_changes(world):
    red = add_red_team(world)
    publish(world)
    iid, eid = world['inject']['id'], world['exercise']['id']
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    assert post(world['admin'], f'/api/exercises/{eid}/members', {
        'user_id': world['users'][0]['id'], 'role': 'participant', 'group_id': red['id'],
    }).status_code == 200
    assert cell(world)['status'] == 'answered'
    assert cell(world, target=f"group:{red['id']}")['answered_count'] == 0


def test_information_updates_stay_published_and_never_count_as_answered(world):
    eid, admin = world['exercise']['id'], world['admin']
    inject = post(admin, f'/api/exercises/{eid}/injects', {
        'name': 'Situation update', 'information_only': True,
    }).json()
    assert post(admin, f"/api/injects/{inject['id']}/publication", {'published': True}).status_code == 200
    assert cell(world, inject['id'])['status'] == 'published'
    assert cell(world, inject['id'])['question_count'] == 0


def test_board_is_director_only_and_publication_targets_must_belong_to_the_exercise(world):
    admin, iid, eid = world['admin'], world['inject']['id'], world['exercise']['id']
    assert world['alice'].get(f'/api/exercises/{eid}/board').status_code == 403
    assert world['outsider'].get(f'/api/exercises/{eid}/board').status_code == 404
    assert post(world['alice'], f'/api/injects/{iid}/publication', {
        'published': True, 'group_id': world['group']['id'],
    }).status_code == 403
    other = post(admin, '/api/exercises', {'name': 'Other exercise'}).json()
    other_group = post(admin, f"/api/exercises/{other['id']}/groups", {'name': 'Other team'}).json()
    for target in ({'group_id': other_group['id']}, {'user_id': world['users'][2]['id']},
                   {'user_id': admin.get('/api/session').json()['user']['id']},
                   {'user_id': world['users'][0]['id']},  # Team members use their team cell.
                   {'group_id': world['group']['id'], 'user_id': world['users'][0]['id']}):
        assert post(admin, f'/api/injects/{iid}/publication', {'published': True, **target}).status_code == 422


def test_partial_publication_locks_variants_and_is_included_in_export(world):
    admin, iid, eid = world['admin'], world['inject']['id'], world['exercise']['id']
    with world['app'].state.store.connect() as db:
        db.execute('UPDATE injects SET variants=? WHERE id=?',
                   (json.dumps({'default': {'name': 'Default', 'description': 'Original'}}), iid))
    assert post(admin, f'/api/injects/{iid}/publication', {
        'published': True, 'group_id': world['group']['id'],
    }).status_code == 200
    assert post(admin, f'/api/injects/{iid}/variant', {'key': 'default'}).status_code == 409
    with zipfile.ZipFile(io.BytesIO(admin.get(f'/api/exercises/{eid}/export.zip').content)) as archive:
        data = json.loads(archive.read('exercise.json'))
    assert data['publications'][0]['inject_id'] == iid
    assert data['publications'][0]['group_id'] == world['group']['id']
    assert data['publications'][0]['published'] is True


def test_bulk_publication_is_exercise_scoped_and_preserves_every_saved_input(world):
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    red = add_red_team(world)
    second = post(admin, f'/api/exercises/{eid}/injects', {'name': 'Situation update', 'information_only': True}).json()
    other = post(admin, '/api/exercises', {'name': 'Separate exercise'}).json()
    other_inject = post(admin, f"/api/exercises/{other['id']}/injects", {'name': 'Separate inject'}).json()
    publish(world)
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(revision=1)).status_code == 200
    response = world['alice'].get(f'/api/injects/{iid}/responses').json()[0]
    evidence = world['alice'].post(f"/api/responses/{response['id']}/attachments",
        files={'file': ('evidence.txt', b'Saved evidence', 'text/plain')},
        headers={'X-CSRF-Token': world['alice'].get('/api/session').json()['csrf_token']})
    assert evidence.status_code == 201
    assert post(admin, f'/api/injects/{iid}/publication', {'published': False, 'group_id': red['id']}).status_code == 200
    with world['app'].state.store.connect() as db:
        saved = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')]
                 for table in ('responses', 'response_history', 'attachments')}
    unpublished = post(admin, f'/api/exercises/{eid}/publication', {'published': False})
    assert unpublished.status_code == 200
    assert unpublished.json()['inject_count'] == 2
    assert all(not item['published'] for row in board(world)['injects'] for item in row['cells'])
    assert cell(world)['answered_count'] == 6
    assert world['alice'].get(f'/api/exercises/{eid}').json()['injects'] == []
    assert post(admin, f'/api/exercises/{eid}/publication', {'published': True}).status_code == 200
    assert cell(world)['status'] == 'answered'
    assert cell(world, second['id'])['status'] == 'published'
    assert world['alice'].get(f'/api/injects/{iid}/responses').json()[0]['revision'] == 2
    assert world['alice'].get(f"/api/attachments/{evidence.json()['id']}").content == b'Saved evidence'
    assert admin.get(f"/api/exercises/{other['id']}").json()['injects'][0]['id'] == other_inject['id']
    assert admin.get(f"/api/exercises/{other['id']}").json()['injects'][0]['published'] is False
    with world['app'].state.store.connect() as db:
        for table, original in saved.items():
            assert [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')] == original


def test_bulk_publication_requires_director_and_does_not_start_the_exercise(world):
    eid, admin = world['exercise']['id'], world['admin']
    assert post(world['alice'], f'/api/exercises/{eid}/publication', {'published': True}).status_code == 403
    assert post(world['outsider'], f'/api/exercises/{eid}/publication', {'published': True}).status_code == 404
    assert admin.post(f'/api/exercises/{eid}/publication', json={'published': True}).status_code == 403
    assert post(admin, f'/api/exercises/{eid}/publication', {
        'published': True, 'group_id': world['group']['id'],
    }).status_code == 422
    assert post(admin, f'/api/exercises/{eid}/publication', {'published': True}).status_code == 200
    assert admin.get(f'/api/exercises/{eid}').json()['status'] == 'draft'
    assert world['alice'].get(f'/api/exercises/{eid}').json()['injects'] == []


def test_participant_progress_only_counts_accessible_team_replies(world):
    red = add_red_team(world)
    publish(world)
    eid, iid = world['exercise']['id'], world['inject']['id']
    assert post(world['outsider'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    inject = world['alice'].get(f'/api/exercises/{eid}').json()['injects'][0]
    assert inject['team_progress']['response_count'] == 0
    assert inject['team_progress']['complete'] is False
    required = {'assessment': 'Coordinate the investigation', 'severity': 'High'}
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(answers=required)).status_code == 200
    assert post(world['bob'], f'/api/injects/{iid}/responses', response_payload()).status_code == 200
    inject = world['alice'].get(f'/api/exercises/{eid}').json()['injects'][0]
    assert inject['my_progress']['answered_count'] == 2
    assert inject['my_progress']['revision'] == 1
    assert inject['my_progress']['complete'] is False
    assert inject['team_progress']['answered_count'] == 6
    assert inject['team_progress']['response_count'] == 2
    assert inject['team_progress']['complete'] is True
    assert 'publications' not in inject
    assert post(world['admin'], f'/api/exercises/{eid}/members', {
        'user_id': world['users'][0]['id'], 'role': 'participant', 'group_id': None,
    }).status_code == 200
    individual = world['alice'].get(f'/api/exercises/{eid}').json()['injects'][0]
    assert individual['team_progress']['response_count'] == 1
    assert individual['team_progress']['complete'] is False


def test_acknowledgement_inject_is_complete_after_a_saved_reply(world):
    admin, eid = world['admin'], world['exercise']['id']
    inject = post(admin, f'/api/exercises/{eid}/injects', {'name': 'Acknowledge briefing'}).json()
    publish(world)
    iid = inject['id']
    assert post(admin, f'/api/injects/{iid}/publication', {'published': True}).status_code == 200
    assert post(world['alice'], f'/api/injects/{iid}/responses', response_payload(answers={})).status_code == 200
    detail = next(row for row in world['alice'].get(f'/api/exercises/{eid}').json()['injects'] if row['id'] == iid)
    assert detail['team_progress']['complete'] is True
    assert cell(world, iid)['status'] == 'answered'
