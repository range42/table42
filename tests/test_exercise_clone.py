import json

import pytest

from test_exercise_flow import post, publish, response_payload, world


@pytest.mark.parametrize('include_groups,include_users', [(False, False), (True, False), (False, True), (True, True)])
def test_clone_starts_fresh_with_optional_groups_and_existing_user_assignments(world, include_groups, include_users):
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    publish(world)
    reply = post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).json()
    assert post(admin, f'/api/injects/{iid}/publication', {
        'published': False, 'group_id': world['group']['id'],
    }).status_code == 200
    result = post(admin, f'/api/exercises/{eid}/clone', {
        'name': 'Next exercise', 'include_groups': include_groups, 'include_users': include_users,
    })
    assert result.status_code == 201, result.text
    clone = admin.get(f"/api/exercises/{result.json()['id']}").json()
    assert clone['id'] != eid
    assert clone['name'] == 'Next exercise'
    assert clone['description'] == world['exercise']['description']
    assert clone['status'] == 'draft'
    assert len(clone['groups']) == int(include_groups)
    if include_groups:
        assert clone['groups'][0]['name'] == 'Blue team'
        assert clone['groups'][0]['id'] != world['group']['id']
    expected_users = {admin.get('/api/session').json()['user']['id']}
    if include_users:
        expected_users.update(user['id'] for user in world['users'][:2])
    assert {member['user_id'] for member in clone['members']} == expected_users
    for member in clone['members']:
        if member['user_id'] in {user['id'] for user in world['users'][:2]}:
            assert member['role'] == 'participant'
            assert member['group_id'] == (clone['groups'][0]['id'] if include_groups else None)
    inject = clone['injects'][0]
    assert inject['id'] != iid
    assert inject['questions'] == world['inject']['questions']
    assert inject['publication_scope'] == 'none'
    assert inject['publications'] == []
    assert admin.get(f"/api/injects/{inject['id']}/responses").json() == []
    assert admin.get(f'/api/injects/{iid}/responses').json()[0]['id'] == reply['id']
    with world['app'].state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 4
        assert db.execute('SELECT COUNT(*) FROM response_history').fetchone()[0] == 1


def upload(world, kind, item_id, name, content):
    client = world['admin'] if kind == 'injects' else world['alice']
    result = client.post(f'/api/{kind}/{item_id}/attachments', files={'file': (name, content, 'text/plain')},
                        headers={'X-CSRF-Token': client.get('/api/session').json()['csrf_token']})
    assert result.status_code == 201
    return result.json()


def test_clone_copies_independent_material_and_all_variants_without_response_evidence(world):
    admin, eid, iid = world['admin'], world['exercise']['id'], world['inject']['id']
    first = upload(world, 'injects', iid, 'brief.txt', b'Initial brief')
    second = upload(world, 'injects', iid, 'alternate.txt', b'Alternate brief')
    variants = {'default': {'name': 'Primary', 'description': 'Primary situation'},
                'alternate': {'name': 'Alternate', 'description': 'Alternate situation'}}
    with world['app'].state.store.connect() as db:
        db.execute('''UPDATE injects SET source_key='alert-1', sender='Control', session_time='09:00',
                      facilitator_notes='Director notes', optional=1, variants=?, active_variant='alternate',
                      description='Alternate situation' WHERE id=?''', (json.dumps(variants), iid))
        db.execute("UPDATE attachments SET variant_key='default',source_path='assets/brief.txt' WHERE id=?", (first['id'],))
        db.execute("UPDATE attachments SET variant_key='alternate' WHERE id=?", (second['id'],))
    publish(world)
    reply = post(world['alice'], f'/api/injects/{iid}/responses', response_payload()).json()
    evidence = upload(world, 'responses', reply['id'], 'reply.txt', b'Reply evidence')
    post(admin, f'/api/exercises/{eid}/status', {'status': 'closed'})
    result = post(admin, f'/api/exercises/{eid}/clone', {})
    assert result.status_code == 201, result.text
    clone = admin.get(f"/api/exercises/{result.json()['id']}").json()
    assert clone['name'] == 'Harbour incident (copy)'
    assert len(clone['groups']) == 1
    assert len(clone['members']) == 1  # User assignments are opt-in.
    inject = clone['injects'][0]
    for key in ('source_key', 'sender', 'session_time', 'facilitator_notes', 'optional', 'active_variant'):
        assert inject[key] == admin.get(f'/api/exercises/{eid}').json()['injects'][0][key]
    copied_variants = {key: {name: item[name] for name in ('name', 'description')}
                       for key, item in inject['variants'].items()}
    assert copied_variants == variants
    assert inject['description'] == 'Alternate situation'
    with world['app'].state.store.connect() as db:
        source = db.execute('SELECT * FROM attachments WHERE inject_id=? AND response_id IS NULL ORDER BY id', (iid,)).fetchall()
        copies = db.execute('SELECT * FROM attachments WHERE inject_id=? ORDER BY id', (inject['id'],)).fetchall()
        assert len(copies) == 2
        for original, copied in zip(source, copies):
            assert copied['id'] != original['id']
            assert copied['storage_name'] != original['storage_name']
            assert copied['response_id'] is None
            for key in ('filename', 'media_type', 'size', 'variant_key', 'source_path'):
                assert copied[key] == original[key]
            source_path = world['app'].state.store.uploads / original['storage_name']
            clone_path = world['app'].state.store.uploads / copied['storage_name']
            assert clone_path.read_bytes() == source_path.read_bytes()
            source_path.write_bytes(b'Changed source')
            assert clone_path.read_bytes() != source_path.read_bytes()
    assert evidence['id'] not in {item['id'] for item in inject['attachments']}
    assert post(admin, f"/api/injects/{inject['id']}/variant", {'key': 'default'}).status_code == 200


def test_clone_rolls_back_database_and_copied_files_when_material_is_missing(world):
    eid, iid = world['exercise']['id'], world['inject']['id']
    upload(world, 'injects', iid, 'first.txt', b'First material')
    second = upload(world, 'injects', iid, 'second.txt', b'Second material')
    store = world['app'].state.store
    with store.connect() as db:
        missing = db.execute('SELECT storage_name FROM attachments WHERE id=?', (second['id'],)).fetchone()[0]
        tables = ['exercises', 'groups', 'members', 'injects', 'attachments']
        before = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables}
    (store.uploads / missing).unlink()
    files = set(store.uploads.iterdir())
    result = post(world['admin'], f'/api/exercises/{eid}/clone', {})
    assert result.status_code == 409, result.text
    assert 'material' in result.json()['detail'].lower()
    with store.connect() as db:
        assert before == {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables}
    assert set(store.uploads.iterdir()) == files


def test_clone_requires_source_director_access_and_csrf(world):
    eid = world['exercise']['id']
    assert post(world['alice'], f'/api/exercises/{eid}/clone', {}).status_code == 403
    assert post(world['outsider'], f'/api/exercises/{eid}/clone', {}).status_code == 404
    assert world['admin'].post(f'/api/exercises/{eid}/clone', json={}).status_code == 403
    assert post(world['admin'], f'/api/exercises/{eid}/clone', {'include_users': 'yes'}).status_code == 422


def test_non_admin_director_owns_the_clone_without_gaining_global_privileges(world):
    admin, eid = world['admin'], world['exercise']['id']
    bob_id = world['users'][1]['id']
    post(admin, f'/api/exercises/{eid}/members', {'user_id': bob_id, 'role': 'director'})
    result = post(world['bob'], f'/api/exercises/{eid}/clone', {'include_users': False})
    assert result.status_code == 201, result.text
    clone_id = result.json()['id']
    clone = world['bob'].get(f'/api/exercises/{clone_id}').json()
    assert clone['is_director'] is True
    assert clone['members'][0]['user_id'] == bob_id
    assert clone['members'][0]['role'] == 'director'
    assert world['alice'].get(f'/api/exercises/{clone_id}').status_code == 404
    assert post(world['bob'], '/api/exercises', {'name': 'Unrelated'}).status_code == 403
