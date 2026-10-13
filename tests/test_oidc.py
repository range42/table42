import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.main import create_app
from app.oidc import OIDCSettings
from tests.test_exercise_flow import post


ISSUER = 'https://login.example/realms/exercises'


class Provider:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.nonce = ''
        self.challenge = ''
        self.claims = {'sub': 'stable-alice', 'email': 'alice@example.test', 'name': 'Alice',
                       'groups': ['range42-users']}
        self.changed = {}

    def transport(self, request):
        if request.url.path.endswith('/.well-known/openid-configuration'):
            return httpx.Response(200, json={'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
                                            'token_endpoint': ISSUER + '/token', 'jwks_uri': ISSUER + '/jwks',
                                            'id_token_signing_alg_values_supported': ['RS256']})
        if request.url.path.endswith('/jwks'):
            key = RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            key.update(kid='fixture-key', use='sig')
            return httpx.Response(200, json={'keys': [key]})
        if request.url.path.endswith('/token'):
            body = parse_qs(request.content.decode())
            verifier = body['code_verifier'][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            assert challenge == self.challenge
            claims = {'iss': ISSUER, 'aud': 'table42', 'iat': int(time.time()), 'exp': int(time.time()) + 300,
                      'nonce': self.nonce, **self.claims, **self.changed}
            token = jwt.encode(claims, self.key, algorithm='RS256', headers={'kid': 'fixture-key'})
            return httpx.Response(200, json={'access_token': 'fixture-access', 'token_type': 'Bearer', 'id_token': token})
        raise AssertionError(f'Unexpected provider request: {request.url.path}')

    def begin(self, client):
        response = client.get('/api/oidc/login', follow_redirects=False)
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers['location']).query)
        assert query['code_challenge_method'] == ['S256']
        self.nonce, self.challenge = query['nonce'][0], query['code_challenge'][0]
        return query['state'][0]

    def finish(self, client, state):
        return client.get('/api/oidc/callback?' + urlencode({'code': 'fixture-code', 'state': state}),
                          follow_redirects=False)


def configured(tmp_path, provider):
    settings = OIDCSettings(issuer=ISSUER, client_id='table42', client_secret='fixture-client-secret',
                            session_secret='fixture-session-secret-at-least-32-characters',
                            redirect_uri='https://table42.example/api/oidc/callback')
    app = create_app(tmp_path, oidc_settings=settings, oidc_transport=httpx.MockTransport(provider.transport))
    client = TestClient(app, base_url='https://table42.example')
    assert post(client, '/api/setup', {'username': 'admin', 'name': 'Admin', 'password': 'administrator-password'}).status_code == 201
    return app, client


def test_oidc_login_preserves_local_recovery_and_does_not_grant_exercise_membership(tmp_path):
    provider = Provider()
    app, admin = configured(tmp_path, provider)
    participant = TestClient(app, base_url='https://table42.example')
    session = participant.get('/api/session').json()
    assert session['oidc_enabled'] is True
    response = provider.finish(participant, provider.begin(participant))
    assert response.status_code == 303
    session_cookie = next(value for value in response.headers.get_list('set-cookie') if value.startswith('range42_session='))
    assert '; Secure' in session_cookie
    user = participant.get('/api/session').json()['user']
    assert user['name'] == 'Alice' and user['is_admin'] is False
    assert participant.get('/api/exercises').json() == []
    recovery = TestClient(app, base_url='https://table42.example')
    assert post(recovery, '/api/login', {'username': 'admin', 'password': 'administrator-password'}).status_code == 200


@pytest.mark.parametrize('changed', [
    {'iss': 'https://attacker.example'}, {'aud': 'different-client'}, {'nonce': 'wrong'}, {'exp': 1},
    {'groups': []}, {'sub': ''},
    {'nonce': 'wrong', 'nonce_supported': False},
])
def test_invalid_identity_or_unapproved_group_does_not_create_a_session(tmp_path, changed):
    provider = Provider()
    app, _ = configured(tmp_path, provider)
    client = TestClient(app, base_url='https://table42.example')
    state = provider.begin(client)
    provider.changed = changed
    assert provider.finish(client, state).status_code == 401
    assert client.get('/api/session').json()['user'] is None


def test_state_cookie_and_callback_replay_are_rejected(tmp_path):
    provider = Provider()
    app, _ = configured(tmp_path, provider)
    client = TestClient(app, base_url='https://table42.example')
    state = provider.begin(client)
    copied = dict(client.cookies)
    assert provider.finish(TestClient(app, base_url='https://table42.example'), state).status_code == 401
    # A missing browser cookie must not consume the original valid flow.
    assert provider.finish(client, state).status_code == 303
    attacker = TestClient(app, base_url='https://table42.example')
    attacker.cookies.update(copied)
    assert provider.finish(attacker, state).status_code == 401


def test_email_changes_and_reuse_do_not_link_two_subjects(tmp_path):
    provider = Provider()
    app, _ = configured(tmp_path, provider)
    first = TestClient(app, base_url='https://table42.example')
    assert provider.finish(first, provider.begin(first)).status_code == 303
    uid = first.get('/api/session').json()['user']['id']
    provider.claims['email'] = 'changed@example.test'
    assert provider.finish(first, provider.begin(first)).status_code == 303
    assert first.get('/api/session').json()['user']['id'] == uid
    provider.claims['sub'] = 'different-subject'
    second = TestClient(app, base_url='https://table42.example')
    assert provider.finish(second, provider.begin(second)).status_code == 303
    assert second.get('/api/session').json()['user']['id'] != uid


def test_disabling_external_account_revokes_sessions_without_deleting_authorship(tmp_path):
    provider = Provider()
    app, admin = configured(tmp_path, provider)
    client = TestClient(app, base_url='https://table42.example')
    assert provider.finish(client, provider.begin(client)).status_code == 303
    uid = client.get('/api/session').json()['user']['id']
    assert post(admin, f'/api/users/{uid}/access', {'enabled': False}).status_code == 200
    assert client.get('/api/session').json()['user'] is None
    assert provider.finish(client, provider.begin(client)).status_code == 401
    with app.state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users WHERE id=?', (uid,)).fetchone()[0] == 1


@pytest.mark.parametrize('field,value', [('issuer', 'http://login.example'),
                                         ('redirect_uri', 'http://table42.example/callback'),
                                         ('session_secret', 'short')])
def test_invalid_configuration_fails_before_the_app_starts(field, value):
    settings = {'issuer': ISSUER, 'client_id': 'table42', 'client_secret': 'fixture-client-secret',
                'session_secret': 'fixture-session-secret-at-least-32-characters',
                'redirect_uri': 'https://table42.example/api/oidc/callback'}
    settings[field] = value
    with pytest.raises(ValueError):
        OIDCSettings(**settings)


def test_external_accounts_can_be_preprovisioned_and_assigned_before_first_login(tmp_path):
    provider = Provider()
    app, admin = configured(tmp_path, provider)
    response = post(admin, '/api/oidc/accounts', {'issuer': ISSUER, 'subject': 'stable-alice', 'name': 'Alice'})
    assert response.status_code == 200
    uid = response.json()['id']
    eid = post(admin, '/api/exercises', {'name': 'ILR 2026'}).json()['id']
    gid = post(admin, f'/api/exercises/{eid}/groups', {'name': 'Team 01'}).json()['id']
    assert post(admin, f'/api/exercises/{eid}/members', {'user_id': uid, 'group_id': gid,
                                                      'role': 'participant'}).status_code == 200
    client = TestClient(app, base_url='https://table42.example')
    assert provider.finish(client, provider.begin(client)).status_code == 303
    assert client.get('/api/session').json()['user']['id'] == uid
    assert {e['id'] for e in client.get('/api/exercises').json()} == {eid}
    assert post(client, '/api/oidc/accounts', {'issuer': ISSUER, 'subject': 'evil', 'name': 'Evil'}).status_code == 403
    assert post(client, f'/api/users/{uid}/access', {'enabled': False}).status_code == 403


def test_external_accounts_expose_identity_management_and_reject_local_password_reset(tmp_path):
    provider = Provider()
    app, admin = configured(tmp_path, provider)
    client = TestClient(app, base_url='https://table42.example')
    assert provider.finish(client, provider.begin(client)).status_code == 303
    user = client.get('/api/session').json()['user']
    assert user['auth_method'] == 'oidc'
    users = {u['id']: u for u in admin.get('/api/users').json()}
    assert users[user['id']]['auth_method'] == 'oidc'
    assert users[user['id']]['enabled'] is True
    assert post(admin, f"/api/users/{user['id']}/password", {'password': 'new-local-password'}).status_code == 409
    assert client.get('/api/session').json()['user']['id'] == user['id']
    assert post(admin, f"/api/users/{user['id']}/access", {'enabled': False}).status_code == 200
    users = {u['id']: u for u in admin.get('/api/users').json()}
    assert users[user['id']]['enabled'] is False
    assert users[admin.get('/api/session').json()['user']['id']]['auth_method'] == 'local'
