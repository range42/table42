"""Optional shared-platform OpenID Connect integration."""

import hashlib
import os
import secrets
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request
from joserfc.errors import JoseError
from starlette.middleware.sessions import SessionMiddleware

from .db import one
from .security import hash_password, token_hash


def https_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('OIDC endpoints require explicit HTTPS URLs')
    return value


def protected_secret(path):
    source = Path(path)
    metadata = source.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_size > 4096:
        raise ValueError('OIDC secret files must be private regular files')
    value = source.read_text().strip()
    if not value:
        raise ValueError('OIDC secret file is empty')
    return value


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    session_secret: str = field(repr=False)
    redirect_uri: str
    required_group: str = "range42-users"
    admin_group: str = ''

    def __post_init__(self):
        https_url(self.issuer)
        https_url(self.redirect_uri)
        if (not self.client_id or not self.client_secret or len(self.session_secret) < 32
                or not self.required_group or self.issuer.endswith('/')):
            raise ValueError('Invalid OIDC configuration')

    @classmethod
    def from_environment(cls):
        names = ('RANGE42_OIDC_ISSUER', 'RANGE42_OIDC_CLIENT_ID', 'RANGE42_OIDC_CLIENT_SECRET_FILE',
                 'RANGE42_OIDC_SESSION_SECRET_FILE', 'RANGE42_OIDC_REDIRECT_URI')
        if not any(os.getenv(name) for name in names):
            return None
        if not all(os.getenv(name) for name in names):
            raise ValueError('All OIDC endpoint, client and secret-file settings are required')
        return cls(issuer=os.environ[names[0]], client_id=os.environ[names[1]],
                   client_secret=protected_secret(os.environ[names[2]]),
                   session_secret=protected_secret(os.environ[names[3]]), redirect_uri=os.environ[names[4]],
                   required_group=os.getenv('RANGE42_OIDC_REQUIRED_GROUP', 'range42-users'),
                   admin_group=os.getenv('RANGE42_OIDC_ADMIN_GROUP', ''))


def external_account(db, issuer, subject, name):
    identity = one(db, 'SELECT * FROM oidc_accounts WHERE issuer=? AND subject=?', (issuer, subject))
    if identity:
        return one(db, 'SELECT * FROM users WHERE id=?', (identity['user_id'],)), identity
    username = 'oidc-' + hashlib.sha256((issuer + '\0' + subject).encode()).hexdigest()[:40]
    uid = db.execute('INSERT INTO users(username,name,password_hash) VALUES (?,?,?)',
                     (username, name[:120] or 'Participant', hash_password(secrets.token_urlsafe(48)))).lastrowid
    db.execute('INSERT INTO oidc_accounts(issuer,subject,user_id) VALUES (?,?,?)', (issuer, subject, uid))
    return one(db, 'SELECT * FROM users WHERE id=?', (uid,)), {'user_id': uid, 'enabled': 1}


def install_oidc(app, settings, store, login_response, transport=None):
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie='range42_oidc',
                       max_age=600, same_site='lax', https_only=True)
    clients = OAuth()
    options = {'scope': 'openid profile email groups', 'code_challenge_method': 'S256', 'timeout': 10,
               'follow_redirects': False}
    if transport is not None:
        options['transport'] = transport
    remote = clients.register('exercise', client_id=settings.client_id, client_secret=settings.client_secret,
                              server_metadata_url=settings.issuer + '/.well-known/openid-configuration',
                              client_kwargs=options)

    async def verified_metadata():
        metadata = await remote.load_server_metadata()
        if metadata.get('issuer') != settings.issuer:
            raise ValueError('Discovery issuer mismatch')
        for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            https_url(metadata[key])
        # Only asymmetric signed ID tokens are accepted, regardless of provider advertisement.
        remote.server_metadata['id_token_signing_alg_values_supported'] = ['RS256']

    @app.get('/api/oidc/login')
    async def oidc_login(request: Request):
        try:
            await verified_metadata()
            response = await remote.authorize_redirect(request, settings.redirect_uri)
            state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
            with store.connect() as db:
                db.execute('DELETE FROM oidc_flows WHERE expires_at<=?', (time.time(),))
                db.execute('INSERT INTO oidc_flows VALUES (?,?)', (token_hash(state), time.time() + 600))
            return response
        except (OAuthError, JoseError, httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(503, 'Exercise sign-in is unavailable.') from None

    @app.get('/api/oidc/callback')
    async def oidc_callback(request: Request):
        state = request.query_params.get('state', '')
        try:
            state_data = await remote.framework.get_state_data(request.session, state) if state else None
            if not state_data:
                raise ValueError('Missing browser-bound state')
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                flow = db.execute('DELETE FROM oidc_flows WHERE state_hash=? AND expires_at>? RETURNING state_hash',
                                  (token_hash(state), time.time())).fetchone()
                if not flow:
                    raise ValueError('Expired or replayed state')
            await verified_metadata()
            token = await remote.authorize_access_token(request)
            claims = token.get('userinfo')
            if (not claims or claims.get('iss') != settings.issuer or not isinstance(claims.get('sub'), str)
                    or not claims['sub'] or len(claims['sub']) > 256):
                raise ValueError('Invalid subject')
            nonce = claims.get('nonce')
            if (not isinstance(nonce, str) or not isinstance(state_data.get('nonce'), str)
                    or not secrets.compare_digest(nonce, state_data['nonce'])):
                raise ValueError('Nonce verification is required')
            groups = claims.get('groups', [])
            if not isinstance(groups, list) or settings.required_group not in groups:
                raise ValueError('Unapproved identity')
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not one(db, 'SELECT id FROM users WHERE is_admin=1 LIMIT 1'):
                    raise ValueError('Local administrator setup is required')
                name = str(claims.get('name') or claims.get('preferred_username') or 'Participant')
                user, identity = external_account(db, settings.issuer, claims['sub'], name)
                if not identity['enabled']:
                    raise ValueError('Disabled account')
                administrator = bool(settings.admin_group and settings.admin_group in groups)
                db.execute('UPDATE users SET name=?,is_admin=? WHERE id=?', (name[:120], int(administrator), user['id']))
                user.update(name=name[:120], is_admin=administrator)
                old_token = request.cookies.get('range42_session', '')
                db.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(old_token),))
                response = login_response(db, user)
            request.session.clear()
            response.status_code = 303
            response.headers['location'] = '/'
            return response
        except (OAuthError, JoseError, httpx.HTTPError, KeyError, TypeError, ValueError):
            request.session.clear()
            raise HTTPException(401, 'Exercise sign-in could not be verified.') from None
