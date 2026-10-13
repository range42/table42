"""Real HTTPS browser flow with a local, signed-token fixture identity provider.

Run: uv run --locked --with playwright==1.58.0 python -m tests.browser_oidc_flow
Only the fixture provider is intercepted; Table42 runs as an actual TLS server.
"""

import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from playwright.sync_api import expect, sync_playwright

from app.main import create_app
from app.oidc import OIDCSettings
from tests import test_oidc as fixtures


with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
base = f'https://127.0.0.1:{port}'
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    provider_port = listener.getsockname()[1]
issuer = f'https://localhost:{provider_port}/realms/exercises'
fixtures.ISSUER = issuer

with tempfile.TemporaryDirectory(prefix='table42-oidc-browser-') as directory:
    directory = Path(directory)
    cert, key = directory / 'tls.crt', directory / 'tls.key'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-keyout', str(key), '-out', str(cert)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    provider = fixtures.Provider()
    settings = OIDCSettings(issuer=issuer, client_id='table42', client_secret='fixture-secret',
                            session_secret='fixture-cookie-secret-at-least-32-characters',
                            redirect_uri=base + '/api/oidc/callback')
    app = create_app(directory / 'data', oidc_settings=settings,
                     oidc_transport=httpx.MockTransport(provider.transport))
    identity_app = FastAPI()

    @identity_app.get('/realms/exercises/authorize')
    def authorize(request: Request):
        query = request.query_params
        provider.nonce, provider.challenge = query['nonce'], query['code_challenge']
        return RedirectResponse(base + '/api/oidc/callback?' + urlencode({
            'code': 'fixture-code', 'state': query['state']}))

    identity_server = uvicorn.Server(uvicorn.Config(identity_app, host='127.0.0.1', port=provider_port,
                                                    ssl_certfile=str(cert), ssl_keyfile=str(key), log_level='error'))
    identity_thread = threading.Thread(target=identity_server.run, daemon=True)
    identity_thread.start()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, ssl_certfile=str(cert),
                                           ssl_keyfile=str(key), log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started and identity_server.started:
                break
            time.sleep(.1)
        assert server.started and identity_server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            admin = browser.new_context(ignore_https_errors=True)
            setup = admin.request.post(base + '/api/setup', data={
                'username': 'admin', 'name': 'Admin', 'password': 'administrator-password'}).json()
            alice = browser.new_context(ignore_https_errors=True)

            participant = alice.new_page()
            errors = []
            participant.on('pageerror', lambda error: errors.append(str(error)))
            participant.goto(base)
            participant.get_by_role('link', name='Sign in with exercise account').click()
            expect(participant.get_by_role('heading', name='Your exercises')).to_be_visible()
            uid = alice.request.get(base + '/api/session').json()['user']['id']
            participant.get_by_role('link', name='Account', exact=True).click()
            expect(participant.get_by_text('Your password is managed by your exercise identity provider.')).to_be_visible()
            expect(participant.get_by_role('button', name='Update password')).to_have_count(0)
            administrator = admin.new_page()
            administrator.goto(base + '/#users')
            row = administrator.get_by_role('row').filter(has=administrator.get_by_role('cell', name='Alice', exact=True))
            expect(row.get_by_role('button', name='Reset password')).to_have_count(0)
            row.get_by_role('button', name='Disable access').click()
            expect(row.get_by_role('button', name='Enable access')).to_be_visible()
            assert alice.request.get(base + '/api/session').json()['user'] is None
            row.get_by_role('button', name='Enable access').click()
            expect(row.get_by_role('button', name='Disable access')).to_be_visible()
            assert admin.request.get(base + '/api/session').json()['user']['id'] == setup['user']['id']
            assert uid != setup['user']['id']
            assert not errors, errors
            browser.close()
    finally:
        server.should_exit = True
        identity_server.should_exit = True
        thread.join(timeout=10)
        identity_thread.join(timeout=10)
print('OIDC HTTPS browser and access controls passed.')
