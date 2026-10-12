"""Director bulk controls and participant draft/review flow in a real browser."""

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


project = Path(__file__).resolve().parent.parent
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]

with tempfile.TemporaryDirectory(prefix='table42-ux-') as directory:
    process = subprocess.Popen([str(project / 'start.sh')], cwd=project,
        env={**os.environ, 'RANGE42_DATA_DIR': directory, 'RANGE42_PORT': str(port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        base = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                with urlopen(base):
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError(process.stderr.read().decode())
                time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            director = browser.new_context(viewport={'width': 1440, 'height': 900})
            setup = director.request.post(f'{base}/api/setup', data={
                'username': 'director', 'name': 'Exercise director', 'password': 'director-password',
            }).json()
            headers = {'X-CSRF-Token': setup['csrf_token']}

            def post(path, data):
                response = director.request.post(f'{base}/api{path}', data=data, headers=headers)
                assert response.ok, response.text()
                return response.json()

            ex = post('/exercises', {'name': 'Coordination exercise'})
            eid = ex['id']
            blue = post(f'/exercises/{eid}/groups', {'name': 'Blue team'})
            red = post(f'/exercises/{eid}/groups', {'name': 'Red team'})
            user = post('/users', {'username': 'alice', 'name': 'Alice', 'password': 'participant-password'})
            post(f'/exercises/{eid}/members', {'user_id': user['id'], 'role': 'participant', 'group_id': blue['id']})
            first = post(f'/exercises/{eid}/injects', {'name': 'Initial alert', 'questions': [
                {'id': 'assessment', 'label': 'Your assessment', 'type': 'long_text', 'required': True},
                {'id': 'confirmed', 'label': 'Confirmed?', 'type': 'boolean', 'required': True},
            ]})
            for index in range(20):
                post(f'/exercises/{eid}/injects', {'name': f'Follow-up {index + 1:02}', 'questions': [
                    {'id': 'decision', 'label': 'Your decision', 'type': 'long_text', 'required': True},
                ]})
            page = director.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'{base}/#exercise/{eid}/board')
            expect(page.get_by_role('heading', name='Release board', exact=True)).to_be_visible()
            expect(page.get_by_role('link', name='Export PDF report', exact=True)).to_be_visible()
            page.get_by_role('button', name='Publish all injects', exact=True).click()
            expect(page.locator('.release-cell[data-status="published"]')).to_have_count(42)
            page.get_by_role('button', name='Start exercise', exact=True).click()
            expect(page.get_by_role('button', name='Close exercise', exact=True)).to_be_visible()
            held = []
            def hold_first(route):
                if held:
                    route.continue_()
                else:
                    held.append(route)
            page.route(f'{base}/api/exercises/{eid}', hold_first)
            with page.expect_request(lambda request: request.url == f'{base}/api/exercises/{eid}'):
                page.evaluate('window.dispatchEvent(new HashChangeEvent("hashchange"))')
            page.get_by_role('tab', name='Injects & responses', exact=True).click()
            assert held
            held[0].continue_()
            page.unroute(f'{base}/api/exercises/{eid}')
            expect(page.get_by_role('tab', name='Injects & responses', exact=True)).to_have_attribute('aria-selected', 'true')
            page.get_by_role('tab', name='Director board', exact=True).click()
            expect(page.get_by_role('heading', name='Release board', exact=True)).to_be_visible()
            first_row = page.locator('.release-table tbody tr').first
            first_row.get_by_role('button', name='Unpublish for all', exact=True).click()
            expect(first_row.locator('.release-cell[data-status="unpublished"]')).to_have_count(2)
            first_row.get_by_role('button', name='Publish for all', exact=True).click()
            expect(first_row.locator('.release-cell[data-status="published"]')).to_have_count(2)
            page.get_by_label('Show team', exact=True).select_option(f'group:{blue["id"]}')
            expect(page.locator('.release-table thead th')).to_have_count(2)
            page.get_by_label('Search injects', exact=True).fill('Follow-up 03')
            expect(page.locator('.release-table tbody tr')).to_have_count(1)
            expect(page.locator('.release-table tbody')).to_contain_text('Follow-up 03')
            page.get_by_label('Search injects', exact=True).fill('')
            page.get_by_label('Show team', exact=True).select_option('')
            expect(page.locator('.release-table tbody tr')).to_have_count(21)
            assert page.locator('.release-table-wrap').evaluate('el => el.scrollHeight <= el.clientHeight + 1')
            last = page.locator('.release-table tbody tr').last.locator('.release-cell').first
            last.scroll_into_view_if_needed()
            assert page.evaluate('scrollY') > 0
            last.focus()
            old = page.locator('.release-table-wrap').element_handle()
            scroll_y = page.evaluate('scrollY')
            page.evaluate('window.dispatchEvent(new HashChangeEvent("hashchange"))')
            page.wait_for_function('el => !el.isConnected', arg=old)
            expect(last).to_be_focused()
            assert abs(page.evaluate('scrollY') - scroll_y) < 2
            page.get_by_role('link', name='Open inject: Initial alert', exact=True).click()
            expect(page.get_by_role('heading', name='Initial alert', exact=True)).to_be_visible()
            page.get_by_role('button', name='Unpublish for all', exact=True).click()
            expect(page.get_by_role('button', name='Publish for all', exact=True)).to_be_enabled()
            page.get_by_role('button', name='Publish for all', exact=True).click()
            second_id = director.request.get(f'{base}/api/exercises/{eid}').json()['injects'][1]['id']
            page.get_by_label('Choose inject', exact=True).select_option(str(second_id))
            expect(page.get_by_role('heading', name='Follow-up 01', exact=True)).to_be_visible()
            assert page.url.endswith(f'/injects/{second_id}')
            page.reload()
            expect(page.get_by_role('heading', name='Follow-up 01', exact=True)).to_be_visible()
            page.get_by_role('link', name='Preview exercise', exact=True).click()
            page.locator(f'[data-action="select-inject"][data-id="{second_id}"]').click()
            page.reload()
            expect(page.get_by_role('heading', name='Follow-up 01', exact=True)).to_be_visible()
            page.get_by_role('link', name='Back to exercise', exact=True).click()
            page.get_by_role('tab', name='Director board', exact=True).click()

            participant = browser.new_context(viewport={'width': 1280, 'height': 900})
            login = participant.request.post(f'{base}/api/login', data={
                'username': 'alice', 'password': 'participant-password',
            }).json()
            alice = participant.new_page()
            alice.on('pageerror', lambda error: errors.append(str(error)))
            alice.goto(f'{base}/#exercise/{eid}')
            expect(alice.get_by_role('heading', name='Your workspace', exact=True)).to_be_visible()
            expect(alice.locator('.participant-summary')).to_contain_text('Blue team')
            alice.get_by_role('button', name='Go to questions', exact=True).click()
            expect(alice.get_by_label('Your assessment', exact=True)).to_be_focused()
            alice.get_by_label('Your assessment', exact=True).fill('Draft coordination plan')
            alice.get_by_label('Confirmed?').select_option('false')
            expect(alice.locator('[data-draft-status]')).to_contain_text('Not submitted')
            expect(alice.locator('[data-question-progress]')).to_have_text('2 / 2 questions filled')
            alice.reload()
            expect(alice.get_by_label('Your assessment', exact=True)).to_have_value('Draft coordination plan')
            expect(alice.get_by_label('Confirmed?')).to_have_value('false')
            page.get_by_role('button', name='Unpublish all injects', exact=True).click()
            expect(page.locator('.release-cell[data-status="unpublished"]')).to_have_count(42)
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            expect(alice.get_by_role('heading', name='Waiting for the next inject', exact=True)).to_be_visible()
            page.get_by_role('button', name='Publish all injects', exact=True).click()
            expect(page.locator('.release-cell[data-status="published"]')).to_have_count(42)
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            expect(alice.get_by_label('Your assessment', exact=True)).to_have_value('Draft coordination plan')
            assert alice.request.get(f'{base}/api/injects/{first["id"]}/responses').json() == []
            alice.get_by_role('button', name='Submit response', exact=True).click()
            expect(alice.get_by_role('button', name='Save changes', exact=True)).to_be_visible()
            expect(alice.locator('[data-team-completion]')).to_contain_text('1 / 21')
            assert alice.evaluate('Object.keys(localStorage).filter(key => key.startsWith("table42:draft:")).length') == 0
            upload = alice.locator('input[type=file]')
            upload.set_input_files({'name': 'pending-evidence.txt', 'mimeType': 'text/plain', 'buffer': b'Pending evidence'})
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            expect(alice.get_by_text('Upload or clear selected files before leaving this view.', exact=True)).to_be_visible()
            assert upload.evaluate('el => el.files.length') == 1
            alice.get_by_role('button', name='Save changes', exact=True).click()
            expect(alice.locator('#reply-form [data-error]')).to_contain_text('Upload or clear selected files')
            assert upload.evaluate('el => el.files.length') == 1
            assert alice.request.get(f'{base}/api/injects/{first["id"]}/responses').json()[0]['revision'] == 1
            alice.get_by_label('Show injects', exact=True).select_option('pending')
            expect(alice.get_by_label('Show injects', exact=True)).to_have_value('all')
            alice.get_by_role('button', name='Clear selected files', exact=True).click()
            assert upload.evaluate('el => el.files.length') == 0
            alice.get_by_label('Your assessment', exact=True).fill('Unsaved edit to preserve')
            page.get_by_role('button', name='Unpublish all injects', exact=True).click()
            expect(page.locator('.release-cell[data-status="unpublished"]')).to_have_count(42)
            alice.reload()
            expect(alice.get_by_role('heading', name='Waiting for the next inject', exact=True)).to_be_visible()
            page.get_by_role('button', name='Publish all injects', exact=True).click()
            expect(page.locator('.release-cell[data-status="answered"]')).to_have_count(1)
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            expect(alice.get_by_label('Your assessment', exact=True)).to_have_value('Unsaved edit to preserve')
            saved = alice.request.get(f'{base}/api/injects/{first["id"]}/responses').json()[0]
            assert saved['answers']['assessment'] == 'Draft coordination plan'
            assert saved['revision'] == 1
            external = participant.request.post(f'{base}/api/injects/{first["id"]}/responses', data={
                'revision': 1, 'answers': {'assessment': 'Saved in another session', 'confirmed': False},
            }, headers={'X-CSRF-Token': login['csrf_token']})
            assert external.ok
            alice.reload()
            expect(alice.get_by_label('Your assessment', exact=True)).to_have_value('Unsaved edit to preserve')
            expect(alice.locator('[data-draft-conflict]')).to_be_visible()
            alice.get_by_role('button', name='Save changes', exact=True).click()
            expect(alice.locator('[data-error]').filter(visible=True)).to_contain_text('Someone updated this reply')
            alice.once('dialog', lambda dialog: dialog.accept())
            alice.get_by_role('button', name='Discard local draft', exact=True).click()
            expect(alice.get_by_label('Your assessment', exact=True)).to_have_value('Saved in another session')
            alice.get_by_label('Your assessment', exact=True).fill('Draft before closing')
            page.once('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='Close exercise', exact=True).click()
            expect(page.get_by_role('button', name='Reopen exercise', exact=True)).to_be_visible()
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            expect(alice.get_by_label('Your assessment', exact=True)).to_be_disabled()
            expect(alice.get_by_label('Your assessment', exact=True)).to_have_value('Draft before closing')
            closed_form = alice.locator('#reply-form').element_handle()
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            alice.wait_for_function('form => !form.isConnected', arg=closed_form)
            expect(alice.get_by_label('Your assessment', exact=True)).to_have_value('Draft before closing')
            page.get_by_role('button', name='Reopen exercise', exact=True).click()
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            alice.once('dialog', lambda dialog: dialog.accept())
            alice.get_by_role('button', name='Discard local draft', exact=True).click()
            alice.get_by_role('button', name='Next awaiting response', exact=True).click()
            expect(alice.get_by_role('heading', name='Follow-up 01', exact=True)).to_be_visible()
            alice.get_by_label('Your decision', exact=True).fill('Draft on the second inject')
            alice.reload()
            expect(alice.get_by_label('Your decision', exact=True)).to_have_value('Draft on the second inject')
            alice.get_by_role('button', name='Previous inject', exact=True).click()
            expect(alice.get_by_role('heading', name='Initial alert', exact=True)).to_be_visible()
            alice.get_by_label('Show injects', exact=True).select_option('pending')
            alice.get_by_role('button', name='Browse injects', exact=True).click()
            expect(alice.locator('.participant-inject-list [data-action="select-inject"][data-id="1"]')).to_have_count(0)
            alice.get_by_label('Show injects', exact=True).select_option('all')
            alice.get_by_role('button', name='Close inject browser', exact=True).click()
            page.get_by_role('button', name='Clone exercise', exact=True).click()
            expect(page.get_by_label('Copy groups', exact=True)).to_be_checked()
            expect(page.get_by_label('Copy user assignments', exact=True)).not_to_be_checked()
            page.get_by_label('Clone exercise name', exact=True).fill('Coordination exercise rehearsal')
            page.get_by_label('Copy user assignments', exact=True).check()
            with page.expect_response(lambda response: response.url.endswith(f'/api/exercises/{eid}/clone')) as cloning:
                page.get_by_role('dialog').get_by_role('button', name='Save', exact=True).click()
            assert cloning.value.status == 201
            clone_id = cloning.value.json()['id']
            expect(page.get_by_role('heading', name='Coordination exercise rehearsal', exact=True)).to_be_visible()
            expect(page.locator('.release-cell[data-status="unpublished"]')).to_have_count(42)
            expect(page.get_by_role('button', name='Start exercise', exact=True)).to_be_visible()
            copied = director.request.get(f'{base}/api/exercises/{clone_id}').json()
            assert copied['status'] == 'draft'
            assert len(copied['injects']) == 21
            assert len(copied['groups']) == 2
            copied_alice = next(member for member in copied['members'] if member['user_id'] == user['id'])
            assert copied_alice['group_id'] != blue['id']
            assert copied_alice['group_name'] == 'Blue team'
            assert all(inject['response_count'] == 0 for inject in copied['injects'])
            assert alice.request.get(f'{base}/api/injects/{first["id"]}/responses').json()[0]['revision'] == 2
            with page.expect_download() as download:
                page.get_by_role('link', name='Export PDF report', exact=True).click()
            assert download.value.suggested_filename == f'exercise-{clone_id}-report.pdf'
            assert download.value.failure() is None
            assert alice.get_by_role('link', name='Export PDF report', exact=True).count() == 0
            page.goto(f'{base}/#exercise/{eid}/board')
            expect(page.locator('.release-cell[data-status="answered"]')).to_have_count(1)
            if destination := os.getenv('TABLE42_SCREENSHOT_DIR'):
                shots = Path(destination)
                shots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shots / 'director-flat-board.png'), full_page=True)
                alice.screenshot(path=str(shots / 'participant-workspace.png'), full_page=True)
            for current in (page, alice):
                current.set_viewport_size({'width': 390, 'height': 844})
                assert current.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if destination:
                page.screenshot(path=str(shots / 'director-flat-board-mobile.png'), full_page=True)
                alice.screenshot(path=str(shots / 'participant-workspace-mobile.png'), full_page=True)
            assert not errors, errors
            browser.close()
            print('UX browser flow passed: bulk/per-inject releases, one vertical scroll, team filtering, participant progress, local drafts, unpublication preservation, revision conflict protection, navigation, exercise cloning, mobile.')
    finally:
        process.terminate()
        process.wait(timeout=10)
