import sqlite3
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    csrf_token TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, started_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','live','closed')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY, exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    name TEXT NOT NULL, UNIQUE(exercise_id, name)
);
CREATE TABLE IF NOT EXISTS members (
    exercise_id INTEGER NOT NULL REFERENCES exercises(id), user_id INTEGER NOT NULL REFERENCES users(id),
    role TEXT NOT NULL CHECK(role IN ('director','participant')), group_id INTEGER REFERENCES groups(id),
    PRIMARY KEY(exercise_id, user_id)
);
CREATE TABLE IF NOT EXISTS injects (
    id INTEGER PRIMARY KEY, exercise_id INTEGER NOT NULL REFERENCES exercises(id), name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', questions TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY, inject_id INTEGER NOT NULL REFERENCES injects(id),
    user_id INTEGER NOT NULL REFERENCES users(id), group_id INTEGER REFERENCES groups(id),
    author_id INTEGER NOT NULL REFERENCES users(id),
    answers TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(inject_id, user_id)
);
CREATE TABLE IF NOT EXISTS response_history (
    id INTEGER PRIMARY KEY, response_id INTEGER NOT NULL REFERENCES responses(id),
    author_id INTEGER NOT NULL REFERENCES users(id), answers TEXT NOT NULL,
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY, inject_id INTEGER NOT NULL REFERENCES injects(id),
    response_id INTEGER REFERENCES responses(id), filename TEXT NOT NULL, storage_name TEXT NOT NULL,
    media_type TEXT NOT NULL, size INTEGER NOT NULL, author_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalogue_imports (
    import_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS injects_exercise ON injects(exercise_id);
CREATE INDEX IF NOT EXISTS responses_inject ON responses(inject_id);
CREATE INDEX IF NOT EXISTS attachments_inject ON attachments(inject_id);
"""


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'exercise.sqlite3'
        self.uploads = directory / 'uploads'
        self.uploads.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            additions = {
                'injects': {
                    'source_key': "TEXT NOT NULL DEFAULT ''", 'sender': "TEXT NOT NULL DEFAULT ''",
                    'facilitator_notes': "TEXT NOT NULL DEFAULT ''", 'session_time': "TEXT NOT NULL DEFAULT ''",
                    'optional': 'INTEGER NOT NULL DEFAULT 0', 'information_only': 'INTEGER NOT NULL DEFAULT 0',
                    'variants': "TEXT NOT NULL DEFAULT '{}'", 'active_variant': "TEXT NOT NULL DEFAULT 'default'",
                },
                'attachments': {'variant_key': 'TEXT', 'source_path': 'TEXT'},
            }
            for table, columns in additions.items():
                existing = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                for column, definition in columns.items():
                    if column not in existing:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            db.execute('PRAGMA journal_mode=WAL')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()


def one(db, sql, args=()):
    row = db.execute(sql, args).fetchone()
    return dict(row) if row else None


def many(db, sql, args=()):
    return [dict(row) for row in db.execute(sql, args).fetchall()]
