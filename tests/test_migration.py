import json
import sqlite3

from app.db import Store
from app.main import inject_data


def test_upgrade_keeps_legacy_injects_and_adds_private_metadata(tmp_path):
    with sqlite3.connect(tmp_path / 'exercise.sqlite3') as db:
        db.executescript('''
            CREATE TABLE exercises(id INTEGER PRIMARY KEY,name TEXT,description TEXT,status TEXT,created_at TEXT);
            CREATE TABLE injects(id INTEGER PRIMARY KEY,exercise_id INTEGER,name TEXT,description TEXT,
                                questions TEXT,published INTEGER,position INTEGER,created_at TEXT);
            CREATE TABLE attachments(id INTEGER PRIMARY KEY,inject_id INTEGER,response_id INTEGER,
                filename TEXT,storage_name TEXT,media_type TEXT,size INTEGER,author_id INTEGER,created_at TEXT);
            INSERT INTO exercises VALUES(1,'Existing exercise','Keep me','live','2026-10-09');
        ''')
        question = {'id': 'q1', 'label': 'Assessment', 'type': 'long_text', 'required': True, 'options': []}
        db.execute('INSERT INTO injects VALUES(1,1,?,?,?,?,1,?)',
                   ('Existing inject', 'Keep these facts', json.dumps([question]), 1, '2026-10-09'))
    store = Store(tmp_path)
    Store(tmp_path)  # Repeated startup must remain safe.
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM injects').fetchone())
        assert row['description'] == 'Keep these facts'
        assert row['published'] == 1
        detail = inject_data(row)
        assert detail['questions'][0]['max_words'] is None
        assert detail['questions'][0]['label'] == 'Assessment'
        assert detail['facilitator_notes'] == ''
        public = inject_data(row, director=False)
        assert 'facilitator_notes' not in public
        assert public['information_only'] is False
        assert db.execute('SELECT COUNT(*) FROM inject_publications').fetchone()[0] == 0
