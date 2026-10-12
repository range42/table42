import io
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from .board import answer_progress, answered_questions, exercise_board, publication_target
from .db import many


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
WIDTH = A4[0] - 72


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


def coverage(completed, total):
    return f'{completed} / {total} ({completed / total:.0%})' if total else 'N/A'


def timestamp(value):
    return datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M UTC')


def entry_summary(question, replies):
    values = [reply['answers'][question['id']] for reply in replies
              if question['id'] in answered_questions([question], reply['answers'])]
    if not values:
        return ''
    kind = question['type']
    if kind in ('single_choice', 'multiple_choice'):
        counts = Counter(values if kind == 'single_choice' else [item for value in values for item in value])
        prefix = 'Choice counts: ' if kind == 'single_choice' else 'Selection counts (multiple allowed): '
        return prefix + '; '.join(f'{option}: {counts[option]}' for option in question['options'])
    if kind == 'boolean':
        return f'Boolean counts: Yes: {values.count(True)}; No: {values.count(False)}'
    if kind == 'number':
        return f'Numeric summary: n={len(values)}; min={min(values):g}; max={max(values):g}; mean={mean(values):g}'
    return ''


def build_pdf(data, output=None):
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
    }
    exercise, stats = data['exercise'], data['stats']
    generated = timestamp(data['generated_at'])

    def paragraph(value, style='body'):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[style])

    def table(headers, rows, widths):
        content = [[paragraph(value, 'cell') for value in headers]]
        content.extend([[paragraph(value, 'cell') for value in row] for row in rows])
        result = LongTable(content, colWidths=widths, repeatRows=1, splitInRow=1, hAlign='LEFT')
        result.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), LIGHT), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, BLUE), ('LINEBELOW', (0, 1), (-1, -1), .3, LINE),
            ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        return result

    def header_footer(canvas, document):
        canvas.saveState()
        canvas.setTitle(f"{exercise['name']} - exercise answer report")
        canvas.setAuthor('Range42 / Table42')
        canvas.setSubject('Exercise statistics and latest attributed answers')
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
        canvas.drawString(70, A4[1] - 38, 'Table42 | Exercise answer report')
        canvas.setStrokeColor(LINE)
        canvas.line(36, 36, A4[0] - 36, 36)
        canvas.setFillColor(MUTED)
        canvas.setFont('Table42', 7)
        canvas.drawString(36, 24, f'Range42 / Table42 | Generated {generated}')
        canvas.drawRightString(A4[0] - 36, 24, f'Page {document.page}')
        canvas.restoreState()

    story = [paragraph(exercise['name'], 'title'), paragraph('Exercise results', 'heading'),
             paragraph(f"Status: {exercise['status'].capitalize()} | Generated: {generated}", 'small')]
    if exercise['description']:
        story.append(paragraph(exercise['description']))
    metrics = [
        f"Total injects: {len(data['injects'])}", f"Response injects: {stats['response_injects']}",
        f"Information updates: {stats['information']}",
        f"Current participants who replied: {stats['participating']} / {stats['participants']}",
        f"Saved replies: {stats['replies']}", f"Distinct authors: {stats['authors']}",
        f"Released completion: {coverage(stats['completed'], stats['released'])}",
        f"Questions answered: {stats['answered']} / {stats['questions']}",
        f"Retained completed injects: {stats['retained']}",
    ]
    story.extend(paragraph(metric) for metric in metrics)
    story.extend([paragraph('How to read the statistics', 'question'), paragraph(
        'Released completion counts team/individual and inject pairs that are currently published. '
        'Information updates are excluded. N/A means no response injects are released. Completion requires '
        'every question part, including optional questions, or an acknowledgement for a question-free inject. '
        'It does not score answer quality. Retained completed injects counts injects completed by at least one '
        'team or person, including work that is now unpublished.', 'small'), paragraph(
        'Roster counts use current assignments. Saved replies keep the team recorded at submission. '
        'The named author identifies who entered a reply, without implying that it is their personal opinion. '
        'Choice and numeric summaries count saved entries, not group votes. Only the latest revision of each '
        'reply is shown. Full revision history and original evidence files are in the ZIP export.', 'small')])
    story.append(paragraph('Team and individual progress', 'heading'))
    if data['teams']:
        story.append(table(['Team / person', 'Current\nparticipants', 'Authors', 'Saved\nreplies',
                            'Released\ncompletion', 'Retained\ncomplete', 'Questions\nanswered'], [
            [team['name'], team['member_count'], team['authors'], team['replies'],
             coverage(team['completed'], team['released']), team['retained'],
             f"{team['answered']} / {team['questions']}"]
            for team in data['teams']], [WIDTH * ratio for ratio in (.24, .1, .08, .1, .2, .14, .14)]))
    else:
        story.append(paragraph('No teams or individual participants are assigned.'))
    story.append(paragraph('Inject overview', 'heading'))
    board = {inject['id']: inject for inject in data['board']['injects']}
    labels = {'none': 'Unpublished', 'some': 'Partly published', 'all': 'Published'}
    replies = defaultdict(list)
    for reply in data['replies']:
        replies[reply['inject_id']].append(reply)
    if data['injects']:
        story.append(table(['Inject', 'Current release', 'Saved replies', 'Mode'], [
            [f"{inject['position']}. {inject['name']}", labels[board[inject['id']]['publication_scope']],
             len(replies[inject['id']]), 'Information' if inject['information_only'] else 'Response']
            for inject in data['injects']], [WIDTH * ratio for ratio in (.5, .22, .13, .15)]))
    else:
        story.append(paragraph('No injects have been defined.'))

    def answer(question, value):
        if value is None or value == '' or value == [] or value == {}:
            return [paragraph('No answer', 'small')]
        kind = question['type']
        if kind == 'boolean':
            return [paragraph('Yes' if value else 'No')]
        if kind in ('list', 'multiple_choice'):
            return [paragraph(f'{index}. {item}' if kind == 'list' else f'• {item}')
                    for index, item in enumerate(value, 1)]
        if kind == 'ranking':
            return [*[paragraph(f'{index}. {item}') for index, item in enumerate(value['order'], 1)],
                    paragraph('Reason: ' + value['explanation'])]
        if kind == 'matrix':
            axes = question['columns']
            widths = [WIDTH * .2] + [WIDTH * .8 / len(axes)] * len(axes)
            return [table(['Row', *axes], [[row, *(value[row].get(column) or 'No answer' for column in axes)]
                                         for row in question['rows']], widths), Spacer(1, 8)]
        return [paragraph(value)]

    for inject in data['injects']:
        story.extend([PageBreak(), paragraph(f"{inject['position']}. {inject['name']}", 'title'),
                      paragraph(f"Current release: {labels[board[inject['id']]['publication_scope']]}", 'small')])
        if inject['sender']:
            story.append(paragraph('From: ' + inject['sender'], 'small'))
        if inject['description']:
            story.append(paragraph(inject['description']))
        material = [item['filename'] for item in data['attachments']
                    if item['inject_id'] == inject['id'] and item['response_id'] is None
                    and item['variant_key'] in (None, inject['active_variant'])]
        if material:
            story.append(paragraph('Exercise material: ' + '; '.join(material), 'small'))
        current_replies = replies[inject['id']]
        if inject['information_only']:
            story.append(paragraph('Information update; no response requested.'))
            continue
        if current_replies and inject['questions']:
            story.append(paragraph('Question statistics', 'heading'))
            for question in inject['questions']:
                count = sum(question['id'] in answered_questions([question], reply['answers'])
                            for reply in current_replies)
                story.extend([paragraph(question['label'], 'question'),
                              paragraph(f"Complete entries: {count} / {len(current_replies)}", 'small')])
                summary = entry_summary(question, current_replies)
                if summary:
                    story.append(paragraph(summary, 'small'))
        story.append(paragraph('Latest answers', 'heading'))
        if not current_replies:
            story.append(paragraph('No submitted replies.'))
            for question in inject['questions']:
                story.extend([paragraph(question['label'], 'question'), paragraph('No answer', 'small')])
        for reply in current_replies:
            story.extend([paragraph(reply['author_name'], 'heading'),
                          paragraph(f"Team at submission: {reply['group_name'] or 'No group'}", 'small'),
                          paragraph(f"Revision {reply['revision']} | Updated: {timestamp(reply['updated_at'])}", 'small'),
                          paragraph(f"Questions filled: {len(answered_questions(inject['questions'], reply['answers']))}"
                                    f" / {len(inject['questions'])}", 'small')])
            for question in inject['questions']:
                story.append(paragraph(question['label'] + (' (required)' if question['required'] else ''), 'question'))
                story.extend(answer(question, reply['answers'].get(question['id'])))
            if not inject['questions']:
                story.append(paragraph('Acknowledged.'))
            evidence = [item['filename'] for item in data['attachments'] if item['response_id'] == reply['id']]
            if evidence:
                story.append(paragraph('Supporting evidence: ' + '; '.join(evidence), 'small'))
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=65,
                                 bottomMargin=48, pageCompression=1, title=exercise['name'])
    document.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    if isinstance(output, io.BytesIO):
        return output.getvalue()
