import json
from collections import defaultdict

from fastapi import HTTPException

from .db import many
from .schemas import validate_answers


def board_targets(db, eid):
    teams = many(db, '''SELECT g.id,g.name,COUNT(m.user_id) member_count FROM groups g
                       LEFT JOIN members m ON m.group_id=g.id AND m.role='participant'
                       WHERE g.exercise_id=? GROUP BY g.id ORDER BY g.name''', (eid,))
    people = many(db, '''SELECT u.id,u.name FROM members m JOIN users u ON u.id=m.user_id
                        WHERE m.exercise_id=? AND m.role='participant' AND m.group_id IS NULL
                        ORDER BY u.name,u.id''', (eid,))
    return [{'key': f"group:{team['id']}", 'kind': 'group', **team} for team in teams] + [
        {'key': f"user:{person['id']}", 'kind': 'user', 'member_count': 1, **person} for person in people
    ]


def publication_target(row):
    return f"group:{row['group_id']}" if row['group_id'] is not None else f"user:{row['user_id']}"


def published_for(inject, publications, target_key=None):
    override = next((row for row in publications if publication_target(row) == target_key), None)
    return bool(override['published'] if override else inject['published'])


def publication_scope(inject, publications, targets):
    states = [published_for(inject, publications, target['key']) for target in targets] or [bool(inject['published'])]
    return 'all' if all(states) else 'some' if any(states) else 'none'


def answered_questions(questions, answers):
    result = set()
    for question in questions:
        try:
            # Completion includes optional questions and every part of a structured answer.
            validate_answers([{**question, 'required': True}], {question['id']: answers.get(question['id'])})
        except HTTPException:
            continue
        result.add(question['id'])
    return result


def exercise_board(db, eid):
    targets = board_targets(db, eid)
    publications = defaultdict(list)
    for row in many(db, '''SELECT p.* FROM inject_publications p JOIN injects i ON i.id=p.inject_id
                          WHERE i.exercise_id=?''', (eid,)):
        publications[row['inject_id']].append(row)
    responses = defaultdict(list)
    for row in many(db, '''SELECT r.* FROM responses r JOIN injects i ON i.id=r.inject_id
                          WHERE i.exercise_id=?''', (eid,)):
        responses[(row['inject_id'], publication_target(row))].append(json.loads(row['answers']))
    injects = []
    for inject in many(db, 'SELECT * FROM injects WHERE exercise_id=? ORDER BY position,id', (eid,)):
        questions = json.loads(inject['questions'])
        cells = []
        for target in targets:
            replies = responses[(inject['id'], target['key'])]
            answered = set().union(*(answered_questions(questions, reply) for reply in replies))
            published = published_for(inject, publications[inject['id']], target['key'])
            complete = bool(questions) and not inject['information_only'] and len(answered) == len(questions)
            cells.append({'target_key': target['key'], 'published': published,
                          'status': 'unpublished' if not published else 'answered' if complete else 'published',
                          'answered_count': len(answered), 'question_count': len(questions),
                          'response_count': len(replies)})
        injects.append({key: inject[key] for key in ('id', 'name', 'position', 'session_time', 'optional',
                                                   'information_only')} | {'cells': cells})
    return {'targets': targets, 'injects': injects}
