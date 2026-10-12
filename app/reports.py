import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from statistics import mean
from xml.sax.saxutils import escape

import reportlab
from babel.dates import format_datetime
from babel.numbers import format_decimal, format_percent
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from .board import answer_progress, answered_questions, exercise_board, publication_target
from .db import many
from .localisation import translate


FONT_DIR = Path(reportlab.__file__).parent / 'fonts'
pdfmetrics.registerFont(TTFont('Table42', str(FONT_DIR / 'Vera.ttf')))
pdfmetrics.registerFont(TTFont('Table42Bold', str(FONT_DIR / 'VeraBd.ttf')))
pdfmetrics.registerFontFamily('Table42', normal='Table42', bold='Table42Bold',
                            italic='Table42', boldItalic='Table42Bold')
INK = colors.HexColor('#17223b')
BLUE = colors.HexColor('#3156eb')
MUTED = colors.HexColor('#526076')
LIGHT = colors.HexColor('#edf2ff')
LINE = colors.HexColor('#d7dfed')
WIDTH = A4[0] - 84  # Page margins and the frame's six-point padding on each side.
TEAM_COLOURS = [
    (colors.HexColor('#1d4ed8'), colors.HexColor('#eff6ff')),
    (colors.HexColor('#0f766e'), colors.HexColor('#f0fdfa')),
    (colors.HexColor('#7e22ce'), colors.HexColor('#faf5ff')),
    (colors.HexColor('#a16207'), colors.HexColor('#fffbeb')),
]


class ReportContext(Flowable):
    def __init__(self, inject, recipient=None, first=True):
        super().__init__()
        self.inject, self.recipient, self.first = inject, recipient, first

    def draw(self):
        self.canv._table42_context = (self.inject, self.recipient)
        root = f"inject-{self.inject['id']}"
        if self.first:
            self.canv.bookmarkPage(root)
            self.canv.addOutlineEntry(f"{self.inject['position']}. {self.inject['name']}", root, 0)
        if self.recipient:
            bookmark = f"{root}-{self.recipient['key']}"
            self.canv.bookmarkPage(bookmark)
            self.canv.addOutlineEntry(self.recipient['label'], bookmark, 1)


class AnswerBlock(Flowable):
    """Repeat attribution and the team's visual treatment when an answer splits."""
    padding = 10
    spaceBefore = 4
    spaceAfter = 12

    def __init__(self, content, attribution, colour, background):
        super().__init__()
        self.content, self.attribution = content, attribution
        self.colour, self.background = colour, background

    def wrap(self, available_width, available_height):
        self.width = available_width
        inner = available_width - 2 * self.padding
        _, self.caption_height = self.attribution.wrap(inner, available_height)
        _, self.content_height = self.content.wrap(inner, available_height)
        self.height = self.caption_height + self.content_height + 2 * self.padding + 6
        return self.width, self.height

    def split(self, available_width, available_height):
        self.wrap(available_width, available_height)
        remaining = available_height - self.caption_height - 2 * self.padding - 6
        if remaining <= 0:
            return []
        parts = self.content.split(available_width - 2 * self.padding, remaining)
        return [AnswerBlock(part, self.attribution, self.colour, self.background) for part in parts]

    def draw(self):
        self.canv.setFillColor(self.background)
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        self.canv.setFillColor(self.colour)
        self.canv.rect(0, 0, 3, self.height, stroke=0, fill=1)
        self.attribution.drawOn(self.canv, self.padding, self.height - self.padding - self.caption_height)
        self.content.drawOn(self.canv, self.padding, self.padding)


class ReportDocument(SimpleDocTemplate):
    def __init__(self, *args, page_decoration, **kwargs):
        super().__init__(*args, **kwargs)
        self.page_decoration = page_decoration

    def afterPage(self):
        self.page_decoration(self.canv, self)


def snapshot(db, exercise, generated_at):
    eid = exercise['id']
    injects = many(db, 'SELECT * FROM injects WHERE exercise_id=? ORDER BY position,id', (eid,))
    for inject in injects:
        inject['questions'] = json.loads(inject['questions'])
    replies = many(db, '''SELECT r.*,u.name author_name,g.name group_name FROM responses r
                         JOIN injects i ON i.id=r.inject_id JOIN users u ON u.id=r.author_id
                         LEFT JOIN groups g ON g.id=r.group_id WHERE i.exercise_id=?
                         ORDER BY i.position,i.id,g.name,u.name,r.id''', (eid,))
    for reply in replies:
        reply['answers'] = json.loads(reply['answers'])
    members = many(db, 'SELECT * FROM members WHERE exercise_id=?', (eid,))
    attachments = many(db, '''SELECT a.id,a.inject_id,a.response_id,a.filename,a.media_type,a.size,a.variant_key
                             FROM attachments a JOIN injects i ON i.id=a.inject_id
                             WHERE i.exercise_id=? ORDER BY a.id''', (eid,))
    board = exercise_board(db, eid)
    current = {member['user_id'] for member in members if member['role'] == 'participant'}
    response_injects = [inject for inject in injects if not inject['information_only']]
    cells = [cell for inject in board['injects'] if not inject['information_only'] for cell in inject['cells']]
    released = [cell for cell in cells if cell['published']]
    complete = lambda cell: cell['response_count'] > 0 and cell['answered_count'] == cell['question_count']
    teams = []
    for target in board['targets']:
        retained_cells = [cell for cell in cells if cell['target_key'] == target['key']]
        target_cells = [cell for cell in released if cell['target_key'] == target['key']]
        target_replies = [reply for reply in replies if publication_target(reply) == target['key']]
        teams.append({**target, 'authors': len({reply['author_id'] for reply in target_replies}),
                      'replies': len(target_replies), 'released': len(target_cells),
                      'completed': sum(complete(cell) for cell in target_cells),
                      'retained': sum(complete(cell) for cell in retained_cells),
                      'answered': sum(cell['answered_count'] for cell in target_cells),
                      'questions': sum(cell['question_count'] for cell in target_cells)})
    grouped = defaultdict(list)
    for reply in replies:
        grouped[(reply['inject_id'], publication_target(reply))].append(reply['answers'])
    retained = {inject['id'] for inject in response_injects for (iid, _), answers in grouped.items()
                if iid == inject['id'] and answer_progress(inject['questions'], answers)['complete']}
    stats = {'participants': len(current), 'participating': len(current & {reply['user_id'] for reply in replies}),
             'authors': len({reply['author_id'] for reply in replies}), 'replies': len(replies),
             'response_injects': len(response_injects), 'information': len(injects) - len(response_injects),
             'completed': sum(complete(cell) for cell in released), 'released': len(released),
             'answered': sum(cell['answered_count'] for cell in released),
             'questions': sum(cell['question_count'] for cell in released), 'retained': len(retained)}
    return {'exercise': exercise, 'generated_at': generated_at, 'injects': injects, 'replies': replies,
            'attachments': attachments, 'board': board, 'teams': teams, 'stats': stats}


def coverage(completed, total, language='en'):
    return (f'{completed} / {total} ({format_percent(completed / total, locale=language)})'
            if total else translate('pdf.not_applicable', language))


def timestamp(value, language='en'):
    pattern = {'en': "yyyy-MM-dd HH:mm 'UTC'", 'fr': "dd/MM/yyyy HH:mm 'UTC'", 'de': "dd.MM.yyyy HH:mm 'UTC'"}
    return format_datetime(datetime.fromisoformat(value).astimezone(timezone.utc), pattern[language], locale=language)


def entry_summary(question, replies, language='en'):
    values = [reply['answers'][question['id']] for reply in replies
              if question['id'] in answered_questions([question], reply['answers'])]
    if not values:
        return ''
    kind = question['type']
    if kind in ('single_choice', 'multiple_choice'):
        counts = Counter(values if kind == 'single_choice' else [item for value in values for item in value])
        prefix = translate('pdf.choice_counts' if kind == 'single_choice' else 'pdf.selection_counts', language)
        return prefix + '; '.join(f'{option}: {counts[option]}' for option in question['options'])
    if kind == 'boolean':
        return translate('pdf.boolean_counts', language, yes=values.count(True), no=values.count(False))
    if kind == 'number':
        numbers = [min(values), max(values), mean(values)]
        rendered = [f'{value:g}' if language == 'en' else format_decimal(
            value, locale=language, decimal_quantization=False, group_separator=False) for value in numbers]
        return translate('pdf.numeric_summary', language, count=len(values), min=rendered[0], max=rendered[1], mean=rendered[2])
    return ''


def build_pdf(data, output=None, language='en'):
    tr = partial(translate, language=language)
    output = output if output is not None else io.BytesIO()
    styles = {
        'body': ParagraphStyle('body', fontName='Table42', fontSize=9, leading=14, textColor=INK,
                               spaceAfter=8, splitLongWords=True),
        'small': ParagraphStyle('small', fontName='Table42', fontSize=7.5, leading=11, textColor=MUTED,
                                spaceAfter=6, splitLongWords=True),
        'title': ParagraphStyle('title', fontName='Table42Bold', fontSize=24, leading=30,
                                textColor=INK, spaceAfter=12),
        'heading': ParagraphStyle('heading', fontName='Table42Bold', fontSize=15, leading=20,
                                  textColor=BLUE, spaceBefore=15, spaceAfter=10, keepWithNext=True),
        'question': ParagraphStyle('question', fontName='Table42Bold', fontSize=10, leading=15,
                                   textColor=INK, spaceBefore=10, spaceAfter=6, keepWithNext=True),
        'cell': ParagraphStyle('cell', fontName='Table42', fontSize=7.5, leading=11, textColor=INK,
                               splitLongWords=True),
        'answer': ParagraphStyle('answer', fontName='Table42', fontSize=10, leading=15, textColor=INK,
                                 splitLongWords=True),
        'attribution': ParagraphStyle('attribution', fontName='Table42Bold', fontSize=9, leading=14,
                                      textColor=INK, splitLongWords=True),
        'team': ParagraphStyle('team', fontName='Table42Bold', fontSize=14, leading=20,
                               textColor=colors.white, splitLongWords=True),
    }
    exercise, stats = data['exercise'], data['stats']
    generated = timestamp(data['generated_at'], language)

    def paragraph(value, style='body'):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[style])

    recipients = {target['key']: {**target, 'label': tr('pdf.team_label' if target['kind'] == 'group' else 'pdf.individual_label', name=target['name'])}
                  for target in data['board']['targets']}
    for reply in data['replies']:
        key = publication_target(reply)
        if key not in recipients:
            grouped = reply['group_id'] is not None
            name = reply['group_name'] or reply['author_name']
            recipients[key] = {'key': key, 'kind': 'group' if grouped else 'user', 'name': name,
                               'label': tr('pdf.team_label' if grouped else 'pdf.individual_label', name=name)}
    for index, target in enumerate(recipients.values()):
        target['colour'], target['background'] = TEAM_COLOURS[index % len(TEAM_COLOURS)]

    def table(headers, rows, widths, row_colours=None):
        content = [[paragraph(value, 'cell') for value in headers]]
        content.extend([[paragraph(value, 'cell') for value in row] for row in rows])
        result = LongTable(content, colWidths=widths, repeatRows=1, splitInRow=1, hAlign='LEFT')
        result.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), LIGHT), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, BLUE), ('LINEBELOW', (0, 1), (-1, -1), .3, LINE),
            ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        for row, (colour, background) in enumerate(row_colours or [], 1):
            result.setStyle(TableStyle([
                ('BACKGROUND', (0, row), (0, row), background),
                ('LINEBEFORE', (0, row), (0, row), 3, colour),
            ]))
        return result

    def header_footer(canvas, document):
        canvas.saveState()
        canvas.setTitle(tr('pdf.metadata_title', name=exercise['name']))
        canvas.setAuthor('Range42 / Table42')
        canvas.setSubject(tr('pdf.subject'))
        # Vector version of the existing Range42 terminal logo.
        canvas.translate(36, A4[1] - 48)
        canvas.scale(.6, .6)
        canvas.setStrokeColor(colors.HexColor('#217eaa'))
        canvas.setFillColor(colors.HexColor('#edf5f8'))
        canvas.setLineWidth(1.5)
        canvas.roundRect(2, 2, 36, 36, 6, stroke=1, fill=1)
        canvas.setLineWidth(2.5)
        path = canvas.beginPath()
        path.moveTo(10, 25.5)
        path.lineTo(18, 20)
        path.lineTo(10, 14.5)
        canvas.drawPath(path)
        canvas.setFillColor(colors.HexColor('#217eaa'))
        canvas.roundRect(21, 11, 9, 2.5, 1, stroke=0, fill=1)
        canvas.restoreState()
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.setFont('Table42Bold', 10)
        canvas.drawString(70, A4[1] - 38, 'Table42 | ' + tr('pdf.title'))
        context = getattr(canvas, '_table42_context', None)
        if context:
            inject, target = context
            canvas.setFont('Table42', 7.5)
            lines = [f"{inject['position']}. {inject['name']}", target['label'] if target else tr('inject.information')]
            for index, line in enumerate(lines):
                clipped = line
                while pdfmetrics.stringWidth(clipped, 'Table42', 7.5) > WIDTH - 34:
                    clipped = clipped[:-2] + '…'
                canvas.setFillColor(target['colour'] if target and index == 1 else MUTED)
                canvas.drawString(70, A4[1] - 51 - index * 12, clipped)
        canvas.setStrokeColor(LINE)
        canvas.line(36, 36, A4[0] - 36, 36)
        canvas.setFillColor(MUTED)
        canvas.setFont('Table42', 7)
        canvas.drawString(36, 24, 'Range42 / Table42 | ' + tr('pdf.generated', date=generated))
        canvas.drawRightString(A4[0] - 36, 24, tr('pdf.page', page=document.page))
        canvas.restoreState()

    story = [paragraph(exercise['name'], 'title'), paragraph(tr('pdf.results'), 'heading'),
             paragraph(tr('pdf.status', status=tr('status.' + exercise['status']).capitalize(), date=generated), 'small')]
    if exercise['description']:
        story.append(paragraph(exercise['description']))
    metrics = [
        tr('pdf.total_injects', count=len(data['injects'])), tr('pdf.response_injects', count=stats['response_injects']),
        tr('pdf.information', count=stats['information']),
        tr('pdf.participation', answered=stats['participating'], total=stats['participants']),
        tr('pdf.saved_replies', count=stats['replies']), tr('pdf.authors', count=stats['authors']),
        tr('pdf.completion', coverage=coverage(stats['completed'], stats['released'], language)),
        tr('pdf.questions', answered=stats['answered'], total=stats['questions']),
        tr('pdf.retained', count=stats['retained']),
    ]
    story.extend(paragraph(metric) for metric in metrics)
    story.extend([paragraph(tr('pdf.read_stats'), 'question'), paragraph(tr('pdf.definitions'), 'small'), paragraph(tr('pdf.attribution_help'), 'small')])
    story.append(paragraph(tr('pdf.team_progress'), 'heading'))
    if data['teams']:
        story.append(table([tr('pdf.team_person'), tr('pdf.roster'), tr('pdf.authors_column'), tr('pdf.replies_column'),
                            tr('pdf.completion_column'), tr('pdf.retained_column'), tr('pdf.questions_column')], [
            [team['name'], team['member_count'], team['authors'], team['replies'],
             coverage(team['completed'], team['released'], language), team['retained'],
             f"{team['answered']} / {team['questions']}"]
            for team in data['teams']], [WIDTH * ratio for ratio in (.23, .1, .09, .1, .2, .14, .14)],
            [(recipients[team['key']]['colour'], recipients[team['key']]['background']) for team in data['teams']]))
    else:
        story.append(paragraph(tr('pdf.no_teams')))
    story.append(paragraph(tr('pdf.inject_overview'), 'heading'))
    board = {inject['id']: inject for inject in data['board']['injects']}
    labels = {'none': tr('status.unpublished'), 'some': tr('pdf.partly'), 'all': tr('status.published')}
    replies = defaultdict(list)
    for reply in data['replies']:
        replies[reply['inject_id']].append(reply)
    if data['injects']:
        story.append(table([tr('pdf.inject'), tr('pdf.release'), tr('reply.saved_replies'), tr('pdf.mode')], [
            [f"{inject['position']}. {inject['name']}", labels[board[inject['id']]['publication_scope']],
             len(replies[inject['id']]), tr('pdf.mode_information') if inject['information_only'] else tr('pdf.mode_response')]
            for inject in data['injects']], [WIDTH * ratio for ratio in (.5, .22, .13, .15)]))
    else:
        story.append(paragraph(tr('pdf.no_injects')))

    def answer(question, value):
        if value is None or value == '' or value == [] or value == {}:
            return paragraph(tr('pdf.no_answer'), 'answer')
        kind = question['type']
        if kind == 'boolean':
            return paragraph(tr('common.yes') if value else tr('common.no'), 'answer')
        if kind in ('list', 'multiple_choice'):
            return paragraph('\n'.join(f'{index}. {item}' if kind == 'list' else f'• {item}'
                                       for index, item in enumerate(value, 1)), 'answer')
        if kind == 'ranking':
            lines = [f'{index}. {item}' for index, item in enumerate(value['order'], 1)]
            return paragraph('\n'.join([*lines, tr('pdf.reason', reason=value['explanation'] or tr('pdf.no_answer'))]), 'answer')
        if kind == 'matrix':
            axes = question['columns']
            inner = WIDTH - 2 * AnswerBlock.padding
            widths = [inner * .2] + [inner * .8 / len(axes)] * len(axes)
            return table([tr('pdf.row'), *axes], [[row, *(value[row].get(column) or tr('pdf.no_answer') for column in axes)]
                                         for row in question['rows']], widths)
        return paragraph(format_decimal(value, locale=language, decimal_quantization=False, group_separator=False) if kind == 'number' and language != 'en' else value, 'answer')

    def context_intro(inject, target=None, first=True):
        story.extend([PageBreak(), ReportContext(inject, target, first),
                      paragraph(f"{inject['position']}. {inject['name']}", 'title')])
        if target:
            banner = LongTable([[paragraph(target['label'], 'team')]], colWidths=[WIDTH], hAlign='LEFT')
            banner.keepWithNext = True
            banner.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), target['colour']),
                ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
            ]))
            story.extend([banner, Spacer(1, 10)])
            cell = next((cell for cell in board[inject['id']]['cells'] if cell['target_key'] == target['key']), None)
            release = tr('status.published') if cell and cell['published'] else tr('status.unpublished') if cell else tr('pdf.not_current')
            story.append(paragraph(tr('pdf.recipient_release', status=release), 'small'))
            story.append(paragraph(tr('pdf.team_at_submission', team=target['name'] if target['kind'] == 'group' else tr('common.no_group')),
                                   'small'))
        else:
            story.append(paragraph(tr('pdf.current_release', status=labels[board[inject['id']]['publication_scope']]), 'small'))
        if inject['sender']:
            story.append(paragraph(tr('pdf.from', sender=inject['sender']), 'small'))
        if inject['description']:
            story.extend([paragraph(tr('pdf.situation'), 'question'), paragraph(inject['description'], 'small')])
        material = [item['filename'] for item in data['attachments']
                    if item['inject_id'] == inject['id'] and item['response_id'] is None
                    and item['variant_key'] in (None, inject['active_variant'])]
        if material:
            story.append(paragraph(tr('pdf.material', files='; '.join(material)), 'small'))

    def response_block(question, reply, target):
        caption = tr('pdf.response_by', name=reply['author_name']) if reply else tr('pdf.no_reply')
        metadata = tr('pdf.saved_revision', revision=reply['revision'], date=timestamp(reply['updated_at'], language)) if reply else ''
        attribution = paragraph(caption + ('\n' + metadata if metadata else ''), 'attribution')
        if reply is None:
            content = paragraph(tr('pdf.no_answer'), 'answer')
        else:
            content = answer(question, reply['answers'].get(question['id'])) if question else paragraph(tr('pdf.acknowledged'), 'answer')
        return AnswerBlock(content, attribution, target['colour'], target['background'])

    targets = list(recipients.values()) or [{
        'key': 'none', 'label': tr('pdf.no_recipients'), 'kind': 'none', 'name': tr('common.no_group'),
        'colour': BLUE, 'background': LIGHT,
    }]
    for inject in data['injects']:
        if inject['information_only']:
            context_intro(inject)
            story.append(paragraph(tr('pdf.information_help')))
            continue
        for index, target in enumerate(targets):
            context_intro(inject, target, first=index == 0)
            current_replies = [reply for reply in replies[inject['id']] if publication_target(reply) == target['key']]
            progress = answer_progress(inject['questions'], [reply['answers'] for reply in current_replies])
            story.append(paragraph(tr('pdf.saved_coverage', count=len(current_replies), answered=progress['answered_count'],
                                       total=len(inject['questions'])), 'small'))
            story.append(paragraph(tr('pdf.latest'), 'heading'))
            if not current_replies:
                story.append(paragraph(tr('pdf.no_team_replies' if target['kind'] == 'group' else 'pdf.no_recipient_replies')))
            for number, question in enumerate(inject['questions'], 1):
                story.append(paragraph(tr('pdf.question_label', index=number, label=question['label']) +
                                       (' ' + tr('common.required_suffix') if question['required'] else ''), 'question'))
                count = sum(question['id'] in answered_questions([question], reply['answers'])
                            for reply in current_replies)
                story.append(paragraph(tr('pdf.entries', answered=count, total=len(current_replies)), 'small'))
                summary = entry_summary(question, current_replies, language)
                if summary:
                    story.append(paragraph(summary, 'small'))
                for reply in current_replies or [None]:
                    story.append(response_block(question, reply, target))
            if not inject['questions']:
                story.append(paragraph(tr('pdf.acknowledgement'), 'question'))
                for reply in current_replies or [None]:
                    story.append(response_block(None, reply, target))
            for reply in current_replies:
                evidence = [item['filename'] for item in data['attachments'] if item['response_id'] == reply['id']]
                if evidence:
                    story.append(paragraph(tr('pdf.evidence_author', name=reply['author_name'], files='; '.join(evidence)), 'small'))
    document = ReportDocument(output, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=85,
                              bottomMargin=48, pageCompression=1, title=exercise['name'],
                              page_decoration=header_footer)
    document.build(story)
    if isinstance(output, io.BytesIO):
        return output.getvalue()
