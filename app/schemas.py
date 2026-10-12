import math
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Credentials(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class UserCreate(Credentials):
    username: str = Field(min_length=2, max_length=80, pattern=r'^[a-zA-Z0-9_.-]+$')
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=256)

    @model_validator(mode='after')
    def clean_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('A display name is required.')
        return self


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class PasswordReset(Input):
    password: str = Field(min_length=10, max_length=256)
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class ExerciseCreate(Input):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=20000)


class GroupCreate(Input):
    name: str = Field(min_length=1, max_length=120)


class MemberSet(Input):
    user_id: int = Field(gt=0)
    role: Literal['director', 'participant']
    group_id: int | None = Field(default=None, gt=0)


class StatusSet(Input):
    status: Literal['draft', 'live', 'closed']


class PublicationSet(Input):
    published: bool = Field(strict=True)
    group_id: int | None = Field(default=None, gt=0)
    user_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def one_recipient(self):
        if self.group_id is not None and self.user_id is not None:
            raise ValueError('Select one team or one individual participant.')
        return self


class VariantSet(Input):
    key: str = Field(min_length=1, max_length=80)


class Question(Input):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    label: str = Field(min_length=1, max_length=1000)
    type: Literal['short_text', 'long_text', 'single_choice', 'multiple_choice', 'number', 'date', 'boolean',
                  'list', 'ranking', 'matrix']
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=100)
    rows: list[str] = Field(default_factory=list, max_length=20)
    columns: list[str] = Field(default_factory=list, max_length=10)
    max_items: int = Field(default=10, ge=1, le=20)
    max_words: int | None = Field(default=None, ge=1, le=5000)
    hint: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def valid_options(self):
        if self.type in ('single_choice', 'multiple_choice', 'ranking'):
            if len(self.options) < 2 or any(not o.strip() or len(o) > 1000 for o in self.options):
                raise ValueError('Choice questions need at least two nonempty options.')
            if len(set(self.options)) != len(self.options):
                raise ValueError('Choice options must be unique.')
        if self.type == 'matrix':
            for axis in (self.rows, self.columns):
                if not axis or len(set(axis)) != len(axis) or any(not v.strip() or len(v) > 200 for v in axis):
                    raise ValueError('Tables need unique, nonempty row and column labels.')
        return self


class InjectSet(ExerciseCreate):
    questions: list[Question] = Field(default_factory=list, max_length=100)
    sender: str = Field(default='', max_length=250)
    session_time: str = Field(default='', max_length=250)
    facilitator_notes: str = Field(default='', max_length=30000)
    optional: bool = False
    information_only: bool = False

    @model_validator(mode='after')
    def unique_ids(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError('Each question needs a unique ID.')
        if self.information_only and self.questions:
            raise ValueError('Informational injects cannot include response questions.')
        return self


class PreviewAnswers(Input):
    answers: dict[str, Any]


class ResponseSet(PreviewAnswers):
    revision: int = Field(ge=0)


def validate_answers(questions, answers):
    if set(answers) - {q['id'] for q in questions}:
        raise HTTPException(422, 'An answer refers to an unknown question.')
    for q in questions:
        value = answers.get(q['id'])
        empty = value is None or value == '' or value == [] or value == {} or (isinstance(value, str) and not value.strip())
        if empty:
            if q['required']:
                raise HTTPException(422, f"Please answer: {q['label']}")
            continue
        kind = q['type']
        valid = True
        if kind in ('short_text', 'long_text'):
            valid = isinstance(value, str) and len(value) <= (1000 if kind == 'short_text' else 20000)
            if valid and q.get('max_words'):
                valid = len(value.split()) <= q['max_words']
        elif kind == 'list':
            valid = (isinstance(value, list) and len(value) <= q.get('max_items', 10)
                     and all(isinstance(v, str) and v.strip() and len(v) <= 1000 for v in value))
        elif kind == 'ranking':
            order = value.get('order') if isinstance(value, dict) else None
            valid = (isinstance(value, dict) and set(value) == {'order', 'explanation'}
                     and isinstance(order, list) and all(isinstance(v, str) for v in order)
                     and len(order) == len(q['options']) and set(order) == set(q['options'])
                     and isinstance(value.get('explanation'), str) and len(value['explanation']) <= 20000
                     and (not q['required'] or bool(value['explanation'].strip())))
        elif kind == 'matrix':
            valid = isinstance(value, dict) and set(value) == set(q['rows'])
            if valid:
                valid = all(isinstance(cells, dict) and set(cells) == set(q['columns'])
                            and all(isinstance(v, str) and len(v) <= 1000 and (not q['required'] or v.strip())
                                    for v in cells.values()) for cells in value.values())
        elif kind == 'single_choice':
            valid = isinstance(value, str) and value in q['options']
        elif kind == 'multiple_choice':
            valid = (isinstance(value, list) and all(isinstance(v, str) and v in q['options'] for v in value)
                     and len(value) == len(set(value)))
        elif kind == 'number':
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
        elif kind == 'boolean':
            valid = isinstance(value, bool)
        elif kind == 'date':
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except (ValueError, TypeError):
                valid = False
        if not valid:
            raise HTTPException(422, f"Invalid answer for: {q['label']}")
