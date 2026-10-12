"""Run with: uv run --with playwright==1.58.0 python tests/browser_flow.py"""

import os
import socket
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


project = Path(__file__).resolve().parent.parent


def preview_image():
    """A synthetic PNG fixture, independent of any exercise or external asset."""
    def chunk(kind, payload):
        return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload))

    header = struct.pack('!2I5B', 96, 48, 8, 2, 0, 0, 0)
    pixels = (b'\x00' + b'\x21\x7e\xaa' * 96) * 48
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]

with tempfile.TemporaryDirectory(prefix='table42-browser-') as directory:
    process = subprocess.Popen(
        [str(project / 'start.sh')],
        cwd=project, env={**os.environ, 'RANGE42_DATA_DIR': directory, 'RANGE42_PORT': str(port)}, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        base = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                with urlopen(base) as response:
                    if response.status == 200:
                        break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError(process.stderr.read().decode())
                time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            expect(page.get_by_role('heading', name='Set up Table42')).to_be_visible()
            expect(page.get_by_role('img', name='Range42 logo')).to_be_visible()
            page.get_by_label('Full name').fill('Exercise director')
            page.get_by_label('Username').fill('admin')
            page.get_by_label('Password', exact=True).fill('director-password')
            page.get_by_role('button', name='Create administrator account').click()
            expect(page.get_by_role('heading', name='Your exercises')).to_be_visible()
            page.get_by_role('button', name='New exercise').first.click()
            page.get_by_label('Exercise name').fill('Harbour incident')
            page.get_by_label('Description', exact=True).fill('Practice coordination during a port disruption.')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.get_by_role('heading', name='Harbour incident', exact=True)).to_be_visible()
            page.get_by_role('link', name='People', exact=False).click()
            page.get_by_role('button', name='Create account').click()
            page.get_by_label('Full name').fill('Alice')
            page.get_by_label('Username').fill('alice')
            page.get_by_label('Initial password').fill('participant-password')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.get_by_role('cell', name='Alice', exact=True)).to_be_visible()
            page.get_by_role('link', name='Exercises', exact=False).first.click()
            page.get_by_role('link', name='Harbour incident', exact=False).click()
            page.get_by_role('tab', name='Groups & members').click()
            page.get_by_role('button', name='Add group').click()
            page.get_by_label('Group name').fill('Blue team')
            page.get_by_role('button', name='Save', exact=True).click()
            page.get_by_role('button', name='Assign member').click()
            page.get_by_label('Person', exact=True).select_option(label='Alice')
            page.get_by_label('Group', exact=True).select_option(label='Blue team')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.get_by_role('cell', name='Blue team', exact=True)).to_be_visible()
            page.get_by_role('tab', name='Injects & responses').click()
            page.get_by_role('button', name='New inject').click()
            page.get_by_label('Inject name').fill('Initial alert')
            page.get_by_label('Description / situation').fill('The port dispatch system is unavailable.')
            page.get_by_label('Question text').fill('What is your assessment?')
            page.get_by_role('button', name='Add question').click()
            page.get_by_label('Question text').nth(1).fill('Priority')
            page.get_by_label('Answer type').nth(1).select_option('single_choice')
            page.get_by_label('Options, one per line').nth(1).fill('Low\nHigh')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.get_by_role('heading', name='Initial alert', exact=True)).to_be_visible()
            page.locator('input[type=file]').set_input_files({'name': 'exercise-image.png', 'mimeType': 'image/png', 'buffer': preview_image()})
            page.get_by_role('button', name='Upload files', exact=True).click()
            expect(page.get_by_role('img', name='exercise-image.png', exact=True)).to_be_visible()
            page.get_by_role('link', name='Preview exercise', exact=True).click()
            expect(page.get_by_role('heading', name='Exercise preview', exact=True)).to_be_visible()
            expect(page.get_by_role('heading', name='Initial alert', exact=True)).to_be_visible()
            image = page.get_by_role('img', name='exercise-image.png', exact=True)
            expect(image).to_be_visible()
            page.wait_for_function('document.querySelector(".attachment-image img").complete && document.querySelector(".attachment-image img").naturalWidth === 96')
            assert image.evaluate('img => img.getBoundingClientRect().width >= img.closest(".card").clientWidth - 75')
            with page.expect_popup() as popup_info:
                page.get_by_role('link', name='View full-size image: exercise-image.png', exact=True).click()
            popup = popup_info.value
            popup.wait_for_load_state()
            expect(popup.locator('img')).to_be_visible()
            popup.close()
            expect(page.get_by_role('button', name='Publish for all', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Start exercise', exact=True)).to_have_count(0)
            expect(page.get_by_role('link', name='Export results', exact=True)).to_have_count(0)
            page.get_by_label('Preview group', exact=True).select_option(label='Blue team')
            page.get_by_label('What is your assessment?').fill('Practice reply only')
            page.get_by_label('Priority', exact=True).select_option('High')
            page.get_by_role('button', name='Check response', exact=True).click()
            expect(page.get_by_text('Preview response is valid. No response was saved.', exact=True)).to_be_visible()
            detail = page.request.get(f'{base}/api/exercises/1').json()
            assert detail['status'] == 'draft'
            assert detail['injects'][0]['published'] is False
            assert detail['injects'][0]['response_count'] == 0
            if destination := os.getenv('TABLE42_SCREENSHOT_DIR'):
                screenshots = Path(destination)
                screenshots.mkdir(parents=True, exist_ok=True)
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(screenshots / 'exercise-preview.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if destination:
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(screenshots / 'exercise-preview-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 720})
            page.get_by_label('Include unpublished injects', exact=True).uncheck()
            expect(page.get_by_role('heading', name='No published injects', exact=True)).to_be_visible()
            page.get_by_role('link', name='Back to exercise', exact=True).click()
            expect(page.get_by_role('heading', name='Harbour incident', exact=True)).to_be_visible()
            page.get_by_role('button', name='Start exercise').click()
            expect(page.get_by_role('button', name='Close exercise')).to_be_visible()
            csrf = page.request.get(f'{base}/api/session').json()['csrf_token']
            red = page.request.post(f'{base}/api/exercises/1/groups', data={'name': 'Red team'},
                                    headers={'X-CSRF-Token': csrf}).json()
            page.get_by_role('tab', name='Director board', exact=True).click()
            expect(page.get_by_role('heading', name='Release board', exact=True)).to_be_visible()
            blue_cell = page.locator('.release-cell[data-id="1"][data-target-key="group:1"]')
            red_cell = page.locator(f'.release-cell[data-id="1"][data-target-key="group:{red["id"]}"]')
            expect(blue_cell).to_have_attribute('data-status', 'unpublished')
            expect(blue_cell).to_contain_text('0 / 2 questions')
            blue_cell.focus()
            blue_cell.press('Enter')
            expect(blue_cell).to_have_attribute('data-status', 'published')
            expect(blue_cell).to_have_accessible_name('Unpublish Initial alert for Blue team. Published. 0 of 2 questions answered.')
            expect(red_cell).to_have_attribute('data-status', 'unpublished')
            page.get_by_role('link', name='Preview exercise', exact=True).click()
            page.get_by_label('Include unpublished injects', exact=True).uncheck()
            page.get_by_label('Preview group', exact=True).select_option(label='Blue team')
            expect(page.get_by_role('heading', name='Initial alert', exact=True)).to_be_visible()
            page.get_by_label('Preview group', exact=True).select_option(label='Red team')
            expect(page.get_by_role('heading', name='No published injects', exact=True)).to_be_visible()
            page.get_by_role('link', name='Back to exercise', exact=True).click()
            blue_cell.click()
            expect(blue_cell).to_have_attribute('data-status', 'unpublished')
            page.get_by_role('tab', name='Injects & responses', exact=True).click()
            expect(page.get_by_role('tab', name='Injects & responses', exact=True)).to_have_attribute('aria-selected', 'true')
            page.get_by_role('button', name='Publish for all', exact=True).click()
            expect(page.get_by_role('button', name='Unpublish for all', exact=True)).to_be_visible()
            expect(page.locator('.inject-head .badge').first).to_have_text('published')
            page.locator('input[type=file]').set_input_files({'name': 'brief.txt', 'mimeType': 'text/plain', 'buffer': b'Scenario facts'})
            page.get_by_role('button', name='Upload files').click()
            expect(page.get_by_role('link', name='brief.txt')).to_be_visible()

            participant = browser.new_context()
            alice = participant.new_page()
            alice.on('pageerror', lambda error: errors.append(str(error)))
            alice.goto(base)
            alice.get_by_label('Username').fill('alice')
            alice.get_by_label('Password', exact=True).fill('participant-password')
            alice.get_by_role('button', name='Sign in', exact=True).click()
            alice.get_by_role('link', name='Harbour incident', exact=False).click()
            expect(alice.get_by_role('heading', name='Initial alert', exact=True)).to_be_visible()
            expect(alice.get_by_role('img', name='exercise-image.png', exact=True)).to_be_visible()
            expect(alice.get_by_role('link', name='Preview exercise', exact=True)).to_have_count(0)
            alice.get_by_label('What is your assessment?').fill('Coordinate with the port operations team.')
            alice.get_by_label('Priority', exact=True).select_option('High')
            alice.get_by_role('button', name='Submit response').click()
            expect(alice.get_by_role('button', name='Save changes')).to_be_visible()
            alice.get_by_text('Team replies (1)', exact=True).click()
            expect(alice.get_by_text('Alice', exact=True).last).to_be_visible()
            alice.locator('input[type=file]').set_input_files({'name': 'evidence.txt', 'mimeType': 'text/plain', 'buffer': b'Evidence'})
            alice.get_by_role('button', name='Upload files').click()
            expect(alice.get_by_role('link', name='evidence.txt').first).to_be_visible()
            alice.set_viewport_size({'width': 390, 'height': 844})
            assert alice.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.get_by_role('button', name='Refresh', exact=True).click()
            expect(page.get_by_text('Coordinate with the port operations team.', exact=True)).to_be_visible()
            expect(page.get_by_role('link', name='evidence.txt')).to_be_visible()
            page.get_by_role('tab', name='Director board', exact=True).click()
            expect(blue_cell).to_have_attribute('data-status', 'answered')
            expect(blue_cell).to_contain_text('2 / 2 questions')
            expect(red_cell).to_have_attribute('data-status', 'published')
            assert blue_cell.evaluate('el => getComputedStyle(el).backgroundColor') != red_cell.evaluate(
                'el => getComputedStyle(el).backgroundColor')
            blue_cell.click()
            expect(blue_cell).to_have_attribute('data-status', 'unpublished')
            expect(blue_cell).to_contain_text('2 / 2 questions')
            expect(red_cell).to_have_attribute('data-status', 'published')
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            expect(alice.get_by_role('heading', name='Waiting for the next inject')).to_be_visible()
            blue_cell.click()
            expect(blue_cell).to_have_attribute('data-status', 'answered')
            for index in range(20):
                followup = page.request.post(f'{base}/api/exercises/1/injects', data={
                    'name': f'Coordination update {index + 1:02}', 'information_only': True,
                }, headers={'X-CSRF-Token': csrf})
                assert followup.status == 201
            page.get_by_role('button', name='Refresh', exact=True).click()
            expect(page.locator('.release-table tbody tr')).to_have_count(21)
            last_cell = page.locator('.release-cell[data-id="21"][data-target-key="group:1"]')
            last_cell.scroll_into_view_if_needed()
            last_cell.focus()
            old_table = page.locator('.release-table-wrap').element_handle()
            scroll_top = page.evaluate('scrollY')
            assert scroll_top > 0
            assert old_table.evaluate('el => el.scrollHeight <= el.clientHeight + 1')
            page.evaluate('window.dispatchEvent(new HashChangeEvent("hashchange"))')
            page.wait_for_function('table => !table.isConnected', arg=old_table)
            expect(last_cell).to_be_focused()
            assert abs(page.evaluate('scrollY') - scroll_top) < 2
            page.evaluate('window.scrollTo(0, 0)')
            page.locator('.release-table-wrap').evaluate('el => { el.scrollLeft = 0; }')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            expect(blue_cell).to_be_visible()
            if destination := os.getenv('TABLE42_SCREENSHOT_DIR'):
                screenshots = Path(destination)
                screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshots / 'director-board-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 720})
            if destination:
                page.screenshot(path=str(screenshots / 'director-board.png'), full_page=True)
            page.get_by_role('link', name='Open inject: Initial alert', exact=True).click()
            expect(page.get_by_role('tab', name='Injects & responses', exact=True)).to_have_attribute('aria-selected', 'true')
            expect(page.get_by_role('link', name='evidence.txt')).to_be_visible()
            if destination := os.getenv('TABLE42_SCREENSHOT_DIR'):
                screenshots = Path(destination)
                screenshots.mkdir(parents=True, exist_ok=True)
                page.evaluate('window.scrollTo(0, 0)')
                alice.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(screenshots / 'director.png'), full_page=True)
                alice.screenshot(path=str(screenshots / 'participant-mobile.png'), full_page=True)
            with page.expect_download() as download_info:
                page.get_by_role('link', name='Export results').click()
            download = download_info.value
            assert download.suggested_filename.endswith('.zip')
            assert download.failure() is None
            page.get_by_role('button', name='Unpublish for all', exact=True).click()
            expect(page.get_by_role('button', name='Publish for all', exact=True)).to_be_visible()
            expect(page.locator('.inject-head .badge').first).to_have_text('draft')
            alice.get_by_role('button', name='Refresh', exact=True).first.click()
            expect(alice.get_by_role('heading', name='Waiting for the next inject')).to_be_visible()
            assert not errors, errors
            browser.close()
            print('Browser flow passed: branding, preview, accounts, groups, team release board, keyboard controls, completion colours, mobile board, authored replies, evidence, export, unpublication.')
    finally:
        process.terminate()
        process.wait(timeout=10)
