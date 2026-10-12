import csv
import io
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .board import answer_progress, board_targets, exercise_board, publication_scope, publication_target, published_for
from .db import Store, many, one
from .schemas import (
    Credentials, ExerciseClone, ExerciseCreate, GroupCreate, InjectSet, MemberSet, PasswordChange,
    PasswordReset, PreviewAnswers, PublicationSet, Question, ResponseSet, StatusSet, UserCreate, VariantSet, validate_answers,
)
from .security import hash_password, token_hash, verify_password
from .reports import build_pdf, snapshot


STATIC = Path(__file__).parent / 'static'
PUBLIC_USER = 'id, username, name, is_admin'
FILE_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
    '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime',
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
    '.txt': 'text/plain', '.md': 'text/plain', '.csv': 'text/csv', '.json': 'application/json',
    '.pdf': 'application/pdf', '.zip': 'application/zip',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def inject_data(row, director=True):
    result = {**row, 'questions': [Question.model_validate(q).model_dump() for q in json.loads(row['questions'])],
              'published': bool(row['published']), 'information_only': bool(row['information_only']),
              'optional': bool(row['optional']), 'variants': json.loads(row['variants'])}
    if not director:
        for key in ('source_key', 'facilitator_notes', 'session_time', 'optional', 'variants', 'active_variant'):
            result.pop(key, None)
    return result


def public_user(row):
    return {**{k: row[k] for k in ('id', 'username', 'name')}, 'is_admin': bool(row['is_admin'])}


def create_app(data_dir=None):
    directory = Path(data_dir or os.getenv('RANGE42_DATA_DIR', str(STATIC.parent.parent / 'data')))
    store = Store(directory)
    app = FastAPI(title='Table42', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    secure_cookie = os.getenv('RANGE42_SECURE_COOKIE', 'false').lower() == 'true'
    upload_limit = int(os.getenv('RANGE42_MAX_UPLOAD_MB', '100')) * 1024 * 1024
    dummy_hash = hash_password(secrets.token_urlsafe(24))

    @app.middleware('http')
    async def security(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
                return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
            length = request.headers.get('content-length', '0')
            if not length.isdigit() or int(length) > upload_limit + 1024 * 1024:
                return JSONResponse({'detail': 'Upload is too large.'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
            "media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(sqlite3.IntegrityError)
    async def duplicate_handler(request, exc):
        return JSONResponse({'detail': 'That username or group name already exists.'}, status_code=409)

    def session(db, request):
        token = request.cookies.get('range42_session', '')
        return one(db, '''SELECT s.*, u.id, u.username, u.name, u.is_admin FROM sessions s
                          JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?''',
                   (token_hash(token), time.time()))

    def actor(db, request, admin=False):
        user = session(db, request)
        if not user:
            raise HTTPException(401, 'Please sign in.')
        if request.method != 'GET' and not secrets.compare_digest(
                request.headers.get('x-csrf-token', ''), user['csrf_token']):
            raise HTTPException(403, 'Your session changed. Reload the page and try again.')
        if admin and not user['is_admin']:
            raise HTTPException(403, 'An administrator is required.')
        return user

    def exercise_access(db, user, eid, director=False):
        exercise = one(db, 'SELECT * FROM exercises WHERE id=?', (eid,))
        member = one(db, 'SELECT * FROM members WHERE exercise_id=? AND user_id=?', (eid, user['id']))
        if not exercise or (not user['is_admin'] and not member):
            raise HTTPException(404, 'Exercise not found.')
        is_director = bool(user['is_admin'] or member['role'] == 'director')
        if director and not is_director:
            raise HTTPException(403, 'An exercise director is required.')
        return exercise, member, is_director

    def inject_access(db, user, iid, director=False):
        inject = one(db, 'SELECT * FROM injects WHERE id=?', (iid,))
        if not inject:
            raise HTTPException(404, 'Inject not found.')
        exercise, member, is_director = exercise_access(db, user, inject['exercise_id'], director)
        variant = inject['active_variant']
        inject = inject_with_publication(db, inject, member, is_director)
        inject['active_variant'] = variant  # Internal attachment access still needs the selected variant.
        if not is_director and (not inject['published'] or exercise['status'] == 'draft'):
            raise HTTPException(404, 'Inject not found.')
        return inject, exercise, member, is_director

    def inject_with_publication(db, row, member, director, targets=None):
        result = inject_data(row, director)
        publications = many(db, 'SELECT group_id,user_id,published FROM inject_publications WHERE inject_id=?',
                            (row['id'],))
        if director:
            scope = publication_scope(row, publications, targets if targets is not None else
                                      board_targets(db, row['exercise_id']))
            result.update(published=scope != 'none', publication_scope=scope,
                          published_by_default=bool(row['published']),
                          publications=[{**item, 'published': bool(item['published'])} for item in publications])
        else:
            result['published'] = published_for(row, publications, publication_target(member) if member else None)
        return result

    def response_access(db, user, rid):
        row = one(db, 'SELECT * FROM responses WHERE id=?', (rid,))
        if not row:
            raise HTTPException(404, 'Response not found.')
        inject, exercise, member, director = inject_access(db, user, row['inject_id'])
        owns = row['user_id'] == user['id']
        in_group = bool(row['group_id'] and member and row['group_id'] == member['group_id'])
        if not director and not owns and not in_group:
            raise HTTPException(404, 'Response not found.')
        return row, inject, exercise, owns

    def attachment_rows(db, iid, rid=None, variant=None):
        query = '''SELECT id, inject_id, response_id, filename, media_type, size, author_id, created_at
                   FROM attachments WHERE inject_id=? AND response_id IS ?'''
        args = [iid, rid]
        if variant is not None:
            query += ' AND (variant_key IS NULL OR variant_key=?)'
            args.append(variant)
        return many(db, query + ' ORDER BY id', args)

    def response_data(db, row):
        author = one(db, 'SELECT name FROM users WHERE id=?', (row['author_id'],))
        group = one(db, 'SELECT name FROM groups WHERE id=?', (row['group_id'],))
        return {**row, 'answers': json.loads(row['answers']), 'author_name': author['name'],
                'group_name': group['name'] if group else None,
                'attachments': attachment_rows(db, row['inject_id'], row['id'])}

    def login_response(db, user, status=200):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires_at<=?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (token_hash(token), user['id'], csrf, time.time() + 43200))
        response = JSONResponse({'user': public_user(user),
                                 'csrf_token': csrf}, status_code=status)
        response.set_cookie('range42_session', token, max_age=43200, httponly=True, secure=secure_cookie,
                            samesite='strict', path='/')
        return response

    @app.get('/api/session')
    def get_session(request: Request):
        with store.connect() as db:
            user = session(db, request)
            return {'user': public_user(user) if user else None,
                    'csrf_token': user['csrf_token'] if user else None,
                    'needs_setup': not bool(one(db, 'SELECT id FROM users LIMIT 1'))}

    @app.post('/api/setup')
    def setup(payload: UserCreate):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if one(db, 'SELECT id FROM users LIMIT 1'):
                raise HTTPException(409, 'Initial setup is already complete.')
            uid = db.execute('INSERT INTO users(username,name,password_hash,is_admin) VALUES (?,?,?,1)',
                             (payload.username.lower(), payload.name.strip(), hash_password(payload.password))).lastrowid
            return login_response(db, one(db, f'SELECT {PUBLIC_USER} FROM users WHERE id=?', (uid,)), 201)

    @app.post('/api/login')
    def login(payload: Credentials, request: Request):
        key = f"{request.client.host if request.client else 'local'}:{payload.username.lower()}"
        with store.connect() as db:
            attempt = one(db, 'SELECT * FROM login_attempts WHERE key=?', (key,))
            stamp = time.time()
            if attempt and attempt['started_at'] > stamp - 300 and attempt['attempts'] >= 10:
                raise HTTPException(429, 'Too many attempts. Try again in five minutes.')
            user = one(db, 'SELECT * FROM users WHERE username=?', (payload.username.strip(),))
            valid = verify_password(payload.password, user['password_hash'] if user else dummy_hash)
            if not valid or not user:
                if not attempt or attempt['started_at'] <= stamp - 300:
                    db.execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)', (key, 1, stamp))
                else:
                    db.execute('UPDATE login_attempts SET attempts=attempts+1 WHERE key=?', (key,))
                return JSONResponse({'detail': 'Incorrect username or password.'}, status_code=401)
            db.execute('DELETE FROM login_attempts WHERE key=?', (key,))
            old_token = request.cookies.get('range42_session', '')
            db.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(old_token),))
            return login_response(db, user)

    @app.post('/api/logout')
    def logout(request: Request):
        with store.connect() as db:
            user = actor(db, request)
            db.execute('DELETE FROM sessions WHERE token_hash=?', (user['token_hash'],))
        result = JSONResponse({'ok': True})
        result.delete_cookie('range42_session', path='/')
        return result

    @app.get('/api/users')
    def users(request: Request):
        with store.connect() as db:
            actor(db, request, admin=True)
            return many(db, f'SELECT {PUBLIC_USER} FROM users ORDER BY name')

    @app.post('/api/users', status_code=201)
    def create_user(payload: UserCreate, request: Request):
        with store.connect() as db:
            actor(db, request, admin=True)
            if not payload.name.strip():
                raise HTTPException(422, 'A display name is required.')
            uid = db.execute('INSERT INTO users(username,name,password_hash) VALUES (?,?,?)',
                             (payload.username.lower(), payload.name.strip(), hash_password(payload.password))).lastrowid
            return one(db, f'SELECT {PUBLIC_USER} FROM users WHERE id=?', (uid,))

    @app.post('/api/password')
    def change_password(payload: PasswordChange, request: Request):
        with store.connect() as db:
            user = actor(db, request)
            stored = one(db, 'SELECT password_hash FROM users WHERE id=?', (user['id'],))
            if not verify_password(payload.current_password, stored['password_hash']):
                raise HTTPException(403, 'Your current password is incorrect.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(payload.new_password), user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?', (user['id'], user['token_hash']))
        return {'ok': True}

    @app.post('/api/users/{uid}/password')
    def reset_password(uid: int, payload: PasswordReset, request: Request):
        with store.connect() as db:
            actor(db, request, admin=True)
            if not one(db, 'SELECT id FROM users WHERE id=?', (uid,)):
                raise HTTPException(404, 'User not found.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(payload.password), uid))
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        return {'ok': True}

    @app.get('/api/exercises')
    def exercises(request: Request):
        with store.connect() as db:
            user = actor(db, request)
            return many(db, '''SELECT e.*, m.role, (SELECT COUNT(*) FROM members WHERE exercise_id=e.id) member_count
                              FROM exercises e LEFT JOIN members m ON m.exercise_id=e.id AND m.user_id=?
                              WHERE ? OR m.user_id IS NOT NULL ORDER BY e.id DESC''', (user['id'], user['is_admin']))

    @app.post('/api/exercises', status_code=201)
    def create_exercise(payload: ExerciseCreate, request: Request):
        with store.connect() as db:
            user = actor(db, request, admin=True)
            eid = db.execute('INSERT INTO exercises(name,description,created_at) VALUES (?,?,?)',
                             (payload.name, payload.description, now())).lastrowid
            db.execute('INSERT INTO members VALUES (?,?,?,NULL)', (eid, user['id'], 'director'))
            return one(db, 'SELECT * FROM exercises WHERE id=?', (eid,))

    @app.get('/api/exercises/{eid}')
    def exercise_detail(eid: int, request: Request):
        with store.connect() as db:
            user = actor(db, request)
            exercise, member, director = exercise_access(db, user, eid)
            rows = many(db, 'SELECT * FROM injects WHERE exercise_id=? ORDER BY position,id', (eid,))
            targets = board_targets(db, eid) if director else None
            participant_replies = many(db, '''SELECT r.* FROM responses r JOIN injects i ON i.id=r.inject_id
                WHERE i.exercise_id=? AND (r.user_id=? OR (? IS NOT NULL AND r.group_id=?))''',
                (eid, user['id'], member['group_id'], member['group_id'])) if not director else []
            injects = []
            for row in rows:
                inject = inject_with_publication(db, row, member, director, targets)
                if not director and (not inject['published'] or exercise['status'] == 'draft'):
                    continue
                inject['attachments'] = attachment_rows(db, inject['id'], variant=row['active_variant'])
                if director:
                    for key, variant in inject['variants'].items():
                        variant['attachments'] = attachment_rows(db, inject['id'], variant=key)
                inject['response_count'] = one(db, 'SELECT COUNT(*) n FROM responses WHERE inject_id=?',
                                               (inject['id'],))['n'] if director else None
                if not director:
                    replies = [reply for reply in participant_replies if reply['inject_id'] == inject['id']]
                    mine = next((reply for reply in replies if reply['user_id'] == user['id']), None)
                    team = [reply for reply in replies if reply['group_id'] == member['group_id']] if (
                        member['group_id'] is not None) else [mine] if mine else []
                    inject['my_progress'] = {**answer_progress(inject['questions'],
                        [json.loads(mine['answers'])] if mine else []),
                        'revision': mine['revision'] if mine else 0, 'updated_at': mine['updated_at'] if mine else None}
                    inject['team_progress'] = answer_progress(inject['questions'],
                                                            [json.loads(reply['answers']) for reply in team])
                injects.append(inject)
            members = many(db, '''SELECT m.*, u.name, u.username, g.name group_name FROM members m
                                 JOIN users u ON u.id=m.user_id LEFT JOIN groups g ON g.id=m.group_id
                                 WHERE m.exercise_id=? ORDER BY u.name''', (eid,)) if director else []
            groups = many(db, 'SELECT * FROM groups WHERE exercise_id=? ORDER BY name', (eid,))
            if director:
                for group in groups:
                    group['response_count'] = one(db, '''SELECT COUNT(*) n FROM responses r
                                                         JOIN injects i ON i.id=r.inject_id
                                                         WHERE i.exercise_id=? AND r.group_id=?''', (eid, group['id']))['n']
            return {**exercise, 'is_director': director, 'membership': member, 'injects': injects,
                    'groups': groups,
                    'members': members}

    @app.get('/api/exercises/{eid}/board')
    def director_board(eid: int, request: Request):
        with store.connect() as db:
            db.execute('BEGIN')
            exercise_access(db, actor(db, request), eid, director=True)
            return exercise_board(db, eid)

    @app.post('/api/exercises/{eid}/publication')
    def exercise_publication(eid: int, payload: PublicationSet, request: Request):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exercise_access(db, actor(db, request), eid, director=True)
            if payload.group_id is not None or payload.user_id is not None:
                raise HTTPException(422, 'This control applies to every inject and all participants in the exercise.')
            count = db.execute('UPDATE injects SET published=? WHERE exercise_id=?',
                               (int(payload.published), eid)).rowcount
            db.execute('DELETE FROM inject_publications WHERE inject_id IN '
                       '(SELECT id FROM injects WHERE exercise_id=?)', (eid,))
        return {'ok': True, 'inject_count': count}

    @app.post('/api/exercises/{eid}/details')
    def edit_exercise(eid: int, payload: ExerciseCreate, request: Request):
        with store.connect() as db:
            exercise_access(db, actor(db, request), eid, director=True)
            db.execute('UPDATE exercises SET name=?,description=? WHERE id=?', (payload.name, payload.description, eid))
        return {'ok': True}

    @app.post('/api/exercises/{eid}/clone', status_code=201)
    def clone_exercise(eid: int, payload: ExerciseClone, request: Request):
        copied_files = []
        try:
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                user = actor(db, request)
                source, membership, _ = exercise_access(db, user, eid, director=True)
                stamp = now()
                name = payload.name or source['name'][:153] + ' (copy)'
                clone_id = db.execute('INSERT INTO exercises(name,description,created_at) VALUES (?,?,?)',
                                      (name, source['description'], stamp)).lastrowid
                group_ids = {}
                if payload.include_groups:
                    for group in many(db, 'SELECT * FROM groups WHERE exercise_id=? ORDER BY id', (eid,)):
                        group_ids[group['id']] = db.execute('INSERT INTO groups(exercise_id,name) VALUES (?,?)',
                                                           (clone_id, group['name'])).lastrowid
                owner_group = group_ids.get(membership['group_id']) if payload.include_users and membership else None
                db.execute('INSERT INTO members VALUES (?,?,?,?)', (clone_id, user['id'], 'director', owner_group))
                if payload.include_users:
                    for member in many(db, 'SELECT * FROM members WHERE exercise_id=? ORDER BY user_id', (eid,)):
                        if member['user_id'] != user['id']:
                            db.execute('INSERT INTO members VALUES (?,?,?,?)',
                                       (clone_id, member['user_id'], member['role'], group_ids.get(member['group_id'])))
                columns = ('name', 'description', 'questions', 'position', 'source_key', 'sender', 'session_time',
                           'facilitator_notes', 'optional', 'information_only', 'variants', 'active_variant')
                for inject in many(db, 'SELECT * FROM injects WHERE exercise_id=? ORDER BY position,id', (eid,)):
                    inject_id = db.execute(
                        f"INSERT INTO injects(exercise_id,created_at,{','.join(columns)}) "
                        f"VALUES ({','.join('?' for _ in range(len(columns) + 2))})",
                        (clone_id, stamp, *(inject[column] for column in columns))).lastrowid
                    material = many(db, 'SELECT * FROM attachments WHERE inject_id=? '
                                    'AND response_id IS NULL ORDER BY id',
                                    (inject['id'],))
                    for attachment in material:
                        original = store.uploads / attachment['storage_name']
                        if not original.is_file():
                            raise HTTPException(409, f"Exercise material is missing: {attachment['filename']}. "
                                                'Restore the file before cloning.')
                        storage_name = secrets.token_hex(24) + Path(attachment['storage_name']).suffix
                        target = store.uploads / storage_name
                        copied_files.append(target)
                        shutil.copyfile(original, target)
                        db.execute('''INSERT INTO attachments(inject_id,filename,storage_name,media_type,size,
                                      author_id,created_at,variant_key,source_path) VALUES (?,?,?,?,?,?,?,?,?)''',
                                   (inject_id, attachment['filename'], storage_name, attachment['media_type'],
                                    attachment['size'], user['id'], stamp, attachment['variant_key'],
                                    attachment['source_path']))
                clone = one(db, 'SELECT * FROM exercises WHERE id=?', (clone_id,))
            return clone
        except BaseException as error:
            for path in copied_files:
                path.unlink(missing_ok=True)
            if isinstance(error, OSError):
                raise HTTPException(409, 'Exercise material could not be copied. '
                                    'Check available storage and try again.') from error
            raise

    @app.post('/api/exercises/{eid}/status')
    def status(eid: int, payload: StatusSet, request: Request):
        with store.connect() as db:
            exercise, _, _ = exercise_access(db, actor(db, request), eid, director=True)
            allowed = {'draft': {'live'}, 'live': {'closed'}, 'closed': {'live'}}
            if payload.status != exercise['status'] and payload.status not in allowed[exercise['status']]:
                raise HTTPException(409, 'Start, close, or reopen this exercise. A started exercise cannot become a draft.')
            db.execute('UPDATE exercises SET status=? WHERE id=?', (payload.status, eid))
        return {'ok': True}

    @app.post('/api/exercises/{eid}/groups', status_code=201)
    def create_group(eid: int, payload: GroupCreate, request: Request):
        with store.connect() as db:
            exercise_access(db, actor(db, request), eid, director=True)
            gid = db.execute('INSERT INTO groups(exercise_id,name) VALUES (?,?)', (eid, payload.name)).lastrowid
            return one(db, 'SELECT * FROM groups WHERE id=?', (gid,))

    @app.get('/api/exercises/{eid}/available-users')
    def available_users(eid: int, request: Request):
        with store.connect() as db:
            exercise_access(db, actor(db, request), eid, director=True)
            return many(db, f'SELECT {PUBLIC_USER} FROM users ORDER BY name')

    @app.post('/api/exercises/{eid}/members')
    def set_member(eid: int, payload: MemberSet, request: Request):
        with store.connect() as db:
            exercise_access(db, actor(db, request), eid, director=True)
            if not one(db, 'SELECT id FROM users WHERE id=?', (payload.user_id,)):
                raise HTTPException(404, 'User not found.')
            if payload.group_id and not one(db, 'SELECT id FROM groups WHERE id=? AND exercise_id=?',
                                            (payload.group_id, eid)):
                raise HTTPException(422, 'Select a group from this exercise.')
            db.execute('''INSERT INTO members VALUES (?,?,?,?) ON CONFLICT(exercise_id,user_id)
                          DO UPDATE SET role=excluded.role,group_id=excluded.group_id''',
                       (eid, payload.user_id, payload.role, payload.group_id))
        return {'ok': True}

    @app.post('/api/exercises/{eid}/members/{uid}/remove')
    def remove_member(eid: int, uid: int, request: Request):
        with store.connect() as db:
            exercise_access(db, actor(db, request), eid, director=True)
            db.execute('DELETE FROM members WHERE exercise_id=? AND user_id=?', (eid, uid))
        return {'ok': True}

    @app.post('/api/exercises/{eid}/injects', status_code=201)
    def create_inject(eid: int, payload: InjectSet, request: Request):
        with store.connect() as db:
            exercise_access(db, actor(db, request), eid, director=True)
            position = one(db, 'SELECT COALESCE(MAX(position),0)+1 n FROM injects WHERE exercise_id=?', (eid,))['n']
            iid = db.execute('''INSERT INTO injects(exercise_id,name,description,questions,position,created_at,
                                sender,session_time,facilitator_notes,optional,information_only)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (eid, payload.name, payload.description,
                                json.dumps([q.model_dump() for q in payload.questions]),
                                position, now(), payload.sender, payload.session_time, payload.facilitator_notes,
                                int(payload.optional), int(payload.information_only))).lastrowid
            return inject_data(one(db, 'SELECT * FROM injects WHERE id=?', (iid,)))

    @app.post('/api/injects/{iid}/details')
    def edit_inject(iid: int, payload: InjectSet, request: Request):
        with store.connect() as db:
            inject, _, _, _ = inject_access(db, actor(db, request), iid, director=True)
            questions = [q.model_dump() for q in payload.questions]
            if one(db, 'SELECT id FROM responses WHERE inject_id=? LIMIT 1', (iid,)) and (
                    inject['questions'] != questions or inject['information_only'] != payload.information_only):
                raise HTTPException(409, 'Questions and response mode are locked after the first response.')
            variants = inject['variants']
            if inject['active_variant'] in variants:
                variants[inject['active_variant']]['description'] = payload.description
            db.execute('''UPDATE injects SET name=?,description=?,questions=?,sender=?,session_time=?,
                          facilitator_notes=?,optional=?,information_only=?,variants=? WHERE id=?''',
                       (payload.name, payload.description, json.dumps(questions), payload.sender, payload.session_time,
                        payload.facilitator_notes, int(payload.optional), int(payload.information_only),
                        json.dumps(variants, ensure_ascii=False), iid))
        return {'ok': True}

    @app.post('/api/injects/{iid}/variant')
    def select_variant(iid: int, payload: VariantSet, request: Request):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            inject, _, _, _ = inject_access(db, actor(db, request), iid, director=True)
            if payload.key not in inject['variants']:
                raise HTTPException(422, 'Select an available scenario variant.')
            if inject['published'] or one(db, 'SELECT id FROM responses WHERE inject_id=? LIMIT 1', (iid,)):
                raise HTTPException(409, 'Choose the variant before publication and the first response.')
            db.execute('UPDATE injects SET active_variant=?,description=? WHERE id=?',
                       (payload.key, inject['variants'][payload.key]['description'], iid))
        return {'ok': True}

    @app.post('/api/injects/{iid}/publication')
    def publication(iid: int, payload: PublicationSet, request: Request):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            inject, _, _, _ = inject_access(db, actor(db, request), iid, director=True)
            if payload.group_id is not None:
                if not one(db, 'SELECT id FROM groups WHERE id=? AND exercise_id=?',
                           (payload.group_id, inject['exercise_id'])):
                    raise HTTPException(422, 'Select a team from this exercise.')
                db.execute('''INSERT INTO inject_publications(inject_id,group_id,published) VALUES (?,?,?)
                              ON CONFLICT(inject_id,group_id) DO UPDATE SET published=excluded.published''',
                           (iid, payload.group_id, int(payload.published)))
            elif payload.user_id is not None:
                if not one(db, '''SELECT user_id FROM members WHERE exercise_id=? AND user_id=?
                                  AND role='participant' AND group_id IS NULL''',
                           (inject['exercise_id'], payload.user_id)):
                    raise HTTPException(422, 'Select an individual participant without a team in this exercise.')
                db.execute('''INSERT INTO inject_publications(inject_id,user_id,published) VALUES (?,?,?)
                              ON CONFLICT(inject_id,user_id) DO UPDATE SET published=excluded.published''',
                           (iid, payload.user_id, int(payload.published)))
            else:
                db.execute('UPDATE injects SET published=? WHERE id=?', (int(payload.published), iid))
                db.execute('DELETE FROM inject_publications WHERE inject_id=?', (iid,))
        return {'ok': True}

    @app.get('/api/injects/{iid}/responses')
    def responses(iid: int, request: Request):
        with store.connect() as db:
            user = actor(db, request)
            _, _, member, director = inject_access(db, user, iid)
            rows = many(db, 'SELECT * FROM responses WHERE inject_id=? ORDER BY updated_at DESC', (iid,))
            visible = [row for row in rows if director or row['user_id'] == user['id']
                       or (row['group_id'] and member and row['group_id'] == member['group_id'])]
            return [response_data(db, row) for row in visible]

    @app.post('/api/injects/{iid}/responses')
    def save_response(iid: int, payload: ResponseSet, request: Request):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = actor(db, request)
            inject, exercise, member, _ = inject_access(db, user, iid)
            if exercise['status'] != 'live' or not inject['published']:
                raise HTTPException(409, 'Replies are open only for published injects in a live exercise.')
            if inject['information_only']:
                raise HTTPException(409, 'This inject is an information update; no response is requested.')
            validate_answers(inject['questions'], payload.answers)
            previous = one(db, 'SELECT * FROM responses WHERE inject_id=? AND user_id=?', (iid, user['id']))
            if (previous['revision'] if previous else 0) != payload.revision:
                raise HTTPException(409, 'Someone updated this reply. Reload the latest response before saving.')
            revision, stamp = payload.revision + 1, now()
            answers = json.dumps(payload.answers, ensure_ascii=False)
            group_id = previous['group_id'] if previous else member['group_id'] if member else None
            db.execute('''INSERT INTO responses(inject_id,user_id,group_id,author_id,answers,revision,updated_at)
                          VALUES (?,?,?,?,?,?,?) ON CONFLICT(inject_id,user_id) DO UPDATE SET
                          author_id=excluded.author_id,answers=excluded.answers,revision=excluded.revision,
                          updated_at=excluded.updated_at''', (iid, user['id'], group_id, user['id'], answers, revision, stamp))
            row = one(db, 'SELECT * FROM responses WHERE inject_id=? AND user_id=?', (iid, user['id']))
            db.execute('INSERT INTO response_history(response_id,author_id,answers,revision,updated_at) VALUES (?,?,?,?,?)',
                       (row['id'], user['id'], answers, revision, stamp))
            return response_data(db, row)

    @app.post('/api/injects/{iid}/preview')
    def preview_response(iid: int, payload: PreviewAnswers, request: Request):
        with store.connect() as db:
            inject, _, _, _ = inject_access(db, actor(db, request), iid, director=True)
            if inject['information_only']:
                raise HTTPException(409, 'This inject is an information update; no response is requested.')
            validate_answers(inject['questions'], payload.answers)
        return {'ok': True}

    async def upload(request, iid, rid=None):
        with store.connect() as db:
            user = actor(db, request)
            if rid is None:
                inject, exercise, _, _ = inject_access(db, user, iid, director=True)
            else:
                _, inject, exercise, owns = response_access(db, user, rid)
                iid = inject['id']
                if not owns:
                    raise HTTPException(403, 'Only the response owner can add evidence.')
            if exercise['status'] == 'closed' or (rid is not None and exercise['status'] != 'live'):
                raise HTTPException(409, 'Attachments are closed for this exercise.')
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get('file')
            if not isinstance(file, UploadFile) and not hasattr(file, 'filename'):
                raise HTTPException(422, 'Choose a file.')
            filename = Path(file.filename.replace('\\', '/')).name[:180]
            suffix = Path(filename).suffix.lower()
            if suffix not in FILE_TYPES:
                raise HTTPException(422, 'Use an image, video, audio file, PDF, Office document, ZIP, or text file.')
            storage_name = secrets.token_hex(24) + suffix
            path = store.uploads / storage_name
            size = 0
            try:
                with path.open('wb') as target:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > upload_limit:
                            raise HTTPException(413, f'Files may be at most {upload_limit // (1024 * 1024)} MB.')
                        target.write(chunk)
                if not size:
                    raise HTTPException(422, 'The file is empty.')
                with store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    user = actor(db, request)
                    if rid is None:
                        _, exercise, _, _ = inject_access(db, user, iid, director=True)
                    else:
                        _, _, exercise, owns = response_access(db, user, rid)
                        if not owns:
                            raise HTTPException(403, 'Response ownership changed.')
                    if exercise['status'] == 'closed':
                        raise HTTPException(409, 'This exercise has closed.')
                    aid = db.execute('''INSERT INTO attachments(inject_id,response_id,filename,storage_name,
                                        media_type,size,author_id,created_at) VALUES (?,?,?,?,?,?,?,?)''',
                                     (iid, rid, filename, storage_name, FILE_TYPES[suffix], size, user['id'], now())).lastrowid
                    return next(a for a in attachment_rows(db, iid, rid) if a['id'] == aid)
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    @app.post('/api/injects/{iid}/attachments', status_code=201)
    async def inject_upload(iid: int, request: Request):
        return await upload(request, iid)

    @app.post('/api/responses/{rid}/attachments', status_code=201)
    async def response_upload(rid: int, request: Request):
        return await upload(request, None, rid)

    @app.get('/api/attachments/{aid}')
    def attachment(aid: int, request: Request):
        with store.connect() as db:
            user = actor(db, request)
            row = one(db, 'SELECT * FROM attachments WHERE id=?', (aid,))
            if not row:
                raise HTTPException(404, 'Attachment not found.')
            if row['response_id']:
                response_access(db, user, row['response_id'])
            else:
                inject, _, _, director = inject_access(db, user, row['inject_id'])
                if not director and row['variant_key'] is not None and row['variant_key'] != inject['active_variant']:
                    raise HTTPException(404, 'Attachment not found.')
            path = store.uploads / row['storage_name']
            if not path.is_file():
                raise HTTPException(404, 'Attachment file not found.')
            preview = row['media_type'].startswith(('image/', 'video/', 'audio/'))
            return FileResponse(path, media_type=row['media_type'], filename=row['filename'],
                                content_disposition_type='inline' if preview else 'attachment')

    @app.get('/api/exercises/{eid}/report.pdf')
    def pdf_report(eid: int, request: Request):
        with store.connect() as db:
            db.execute('BEGIN')
            exercise, _, _ = exercise_access(db, actor(db, request), eid, director=True)
            data = snapshot(db, exercise, now())
        buffer = tempfile.TemporaryFile()
        try:
            build_pdf(data, buffer)
            length = buffer.tell()
            buffer.seek(0)
        except BaseException:
            buffer.close()
            raise

        def stream_report():
            try:
                while chunk := buffer.read(1024 * 1024):
                    yield chunk
            finally:
                buffer.close()

        return StreamingResponse(stream_report(), media_type='application/pdf', headers={
            'Content-Disposition': f'attachment; filename="exercise-{eid}-report.pdf"',
            'Content-Length': str(length),
        })

    @app.get('/api/exercises/{eid}/export.zip')
    def export(eid: int, request: Request):
        with store.connect() as db:
            db.execute('BEGIN')
            exercise, _, _ = exercise_access(db, actor(db, request), eid, director=True)
            injects = [inject_data(r) for r in many(db, 'SELECT * FROM injects WHERE exercise_id=? ORDER BY position,id', (eid,))]
            rows = many(db, 'SELECT r.* FROM responses r JOIN injects i ON i.id=r.inject_id WHERE i.exercise_id=? ORDER BY r.id', (eid,))
            responses = [response_data(db, r) for r in rows]
            members = many(db, '''SELECT m.user_id,m.role,m.group_id,u.name,u.username FROM members m
                                 JOIN users u ON u.id=m.user_id WHERE m.exercise_id=?''', (eid,))
            history = many(db, '''SELECT h.*, u.name author_name FROM response_history h
                                 JOIN users u ON u.id=h.author_id JOIN responses r ON r.id=h.response_id
                                 JOIN injects i ON i.id=r.inject_id WHERE i.exercise_id=? ORDER BY h.id''', (eid,))
            for item in history:
                item['answers'] = json.loads(item['answers'])
            attachments = many(db, '''SELECT a.* FROM attachments a JOIN injects i ON i.id=a.inject_id
                                     WHERE i.exercise_id=? ORDER BY a.id''', (eid,))
            for attachment in attachments:
                attachment['archive_path'] = f"attachments/{attachment['id']}-{attachment['filename']}"
            data = {'exported_at': now(), 'exercise': exercise, 'members': members,
                    'groups': many(db, 'SELECT * FROM groups WHERE exercise_id=?', (eid,)),
                    'injects': injects, 'responses': responses, 'response_history': history,
                    'publications': [{**row, 'published': bool(row['published'])} for row in many(db,
                        '''SELECT p.* FROM inject_publications p JOIN injects i ON i.id=p.inject_id
                           WHERE i.exercise_id=? ORDER BY p.id''', (eid,))],
                    'attachments': [{k: v for k, v in a.items() if k != 'storage_name'} for a in attachments]}
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(['exercise', 'inject', 'response_id', 'group', 'author', 'revision',
                             'updated_at', 'question_id', 'question', 'type', 'answer'])
            index = {inject['id']: inject for inject in injects}
            for response in responses:
                inject = index[response['inject_id']]
                for question in inject['questions'] or [{'id': '', 'label': '', 'type': ''}]:
                    answer = response['answers'].get(question['id'], '')
                    if isinstance(answer, (dict, list, bool)):
                        answer = json.dumps(answer, ensure_ascii=False)
                    values = [exercise['name'], inject['name'], response['id'], response['group_name'] or '',
                              response['author_name'], response['revision'], response['updated_at'], question['id'],
                              question['label'], question['type'], answer]
                    writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v
                                     for v in values])
            buffer = tempfile.TemporaryFile()
            try:
                with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
                    bundle.writestr('exercise.json', json.dumps(data, indent=2, ensure_ascii=False))
                    bundle.writestr('responses.csv', output.getvalue().encode('utf-8-sig'))
                    for attachment in attachments:
                        path = store.uploads / attachment['storage_name']
                        if not path.is_file():
                            raise HTTPException(409, f"Missing attachment: {attachment['filename']}. Restore it before exporting.")
                        bundle.write(path, attachment['archive_path'])
                length = buffer.tell()
                buffer.seek(0)
            except BaseException:
                buffer.close()
                raise

            def stream_archive():
                try:
                    while chunk := buffer.read(1024 * 1024):
                        yield chunk
                finally:
                    buffer.close()

            return StreamingResponse(stream_archive(), media_type='application/zip',
                                     headers={'Content-Disposition': f'attachment; filename="exercise-{eid}.zip"',
                                              'Content-Length': str(length)})

    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/')
    def home():
        return FileResponse(STATIC / 'index.html')

    return app
