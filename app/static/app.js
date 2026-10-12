const root = document.querySelector('#app');
const editor = document.querySelector('#editor');
const state = { user: null, csrf: '', exercise: null, board: null, selected: null, responses: [], tab: 'injects', dirty: false, filter: '', preview: false, previewDrafts: true, previewGroup: '', previewAnswers: {}, previewVariants: {} };
let noticeTimer;
let loading = false;

const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const field = (name, label, value = '', type = 'text', extra = '') => `<div class="field"><label for="${name}">${label}</label><input id="${name}" name="${name}" type="${type}" value="${esc(value)}" ${extra}></div>`;
const textarea = (name, label, value = '', extra = '') => `<div class="field"><label for="${name}">${label}</label><textarea id="${name}" name="${name}" ${extra}>${esc(value)}</textarea></div>`;
const badge = value => `<span class="badge ${esc(value)}">${esc(value)}</span>`;
const date = value => new Date(value).toLocaleString();
const errorSlot = '<div class="error" data-error role="alert" hidden></div>';
const empty = (title, text, action = '') => `<div class="empty"><div class="symbol" aria-hidden="true">◇</div><h2>${title}</h2><p class="muted">${text}</p>${action}</div>`;
const types = { short_text: 'Short text', long_text: 'Long text', single_choice: 'Single choice', multiple_choice: 'Multiple choice', number: 'Number', date: 'Date', boolean: 'Yes / no', list: 'List of items', ranking: 'Ranked priorities', matrix: 'Response table' };
const options = (items, value, label = 'name', key = 'id') => items.map(item => `<option value="${esc(item[key])}" ${String(value) === String(item[key]) ? 'selected' : ''}>${esc(item[label])}</option>`).join('');

async function api(path, body, multipart = false) {
  const config = body === undefined ? {} : { method: 'POST', headers: { 'X-CSRF-Token': state.csrf || '' } };
  if (body !== undefined) {
    config.body = multipart ? body : JSON.stringify(body);
    if (!multipart) config.headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(`/api${path}`, config);
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && state.user) {
      state.user = null;
      state.csrf = '';
      editor.close();
      auth(false);
    }
    const detail = result.detail;
    throw new Error(Array.isArray(detail) ? detail.map(e => `${e.loc.slice(1).join(' ')}: ${e.msg}`).join('\n') : detail || 'The request failed.');
  }
  return result;
}

function notify(message) {
  const el = document.querySelector('#notice');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { el.hidden = true; }, 5000);
}

async function run(form, action) {
  const button = form?.querySelector('[type=submit]');
  if (button?.disabled) return;
  if (button) button.disabled = true;
  const error = form?.querySelector('[data-error]');
  if (error) error.hidden = true;
  try { await action(); } catch (err) {
    if (error?.isConnected) { error.textContent = err.message; error.hidden = false; error.scrollIntoView({ block: 'nearest' }); }
    else notify(err.message);
  } finally { if (button?.isConnected) button.disabled = false; }
}

function shell(content, active = 'exercises') {
  const admin = state.user.is_admin;
  root.innerHTML = `<div class="shell"><aside class="sidebar"><div><a class="brand" href="#"><img src="/static/range42-logo.svg" alt="Range42 logo">Table42</a><p class="eyebrow">Range42 tabletop exercises</p></div>
    <nav class="nav" aria-label="Main navigation"><a href="#" class="${active === 'exercises' ? 'active' : ''}">◇ &nbsp; Exercises</a>${admin ? `<a href="#users" class="${active === 'users' ? 'active' : ''}">○ &nbsp; People</a>` : ''}</nav>
    <div class="sidebar-bottom"><strong>${esc(state.user.name)}</strong><p>${admin ? 'Administrator' : 'Exercise participant'}</p><div class="row"><a class="button small ghost" href="#account">Account</a><button class="small ghost" data-action="logout">Sign out</button></div></div></aside>
    <div class="workspace"><header class="topbar"><strong>Exercise workspace</strong><span>Range42 / Table42</span></header><main class="content">${content}</main></div></div>`;
}

function auth(needsSetup) {
  root.innerHTML = `<main class="auth"><section class="auth-story"><a class="brand" href="#"><img src="/static/range42-logo.svg" alt="Range42 logo">Table42</a><div class="intro"><p class="eyebrow">A Range42 application</p><h1>Bring your scenario<br>to the <span>table.</span></h1><p>Prepare the exercise. Release the next inject. Capture the decisions, and the people behind them.</p></div><footer>Simple tools for a focused tabletop exercise.</footer></section>
    <section class="auth-form"><div><p class="eyebrow">${needsSetup ? 'Your first exercise starts here' : 'Welcome back'}</p><h2>${needsSetup ? 'Set up Table42' : 'Sign in'}</h2><p class="muted">${needsSetup ? 'Create the administrator account to start preparing exercises.' : 'Use the local account provided by your exercise director.'}</p>
    <form data-form="${needsSetup ? 'setup' : 'login'}">${errorSlot}${needsSetup ? field('name', 'Full name', '', 'text', 'required maxlength="120" autocomplete="name"') : ''}
    ${field('username', 'Username', '', 'text', 'required maxlength="80" autocomplete="username" autocapitalize="none"')}
    ${field('password', 'Password', '', 'password', `required ${needsSetup ? 'minlength="10"' : ''} maxlength="256" autocomplete="${needsSetup ? 'new-password' : 'current-password'}"`)}
    ${needsSetup ? '<p class="muted">Use at least 10 characters. You can create participant accounts after signing in.</p>' : ''}<button class="primary" type="submit">${needsSetup ? 'Create administrator account' : 'Sign in'}</button></form></div></section></main>`;
}

async function dashboard() {
  const exercises = await api('/exercises');
  const create = state.user.is_admin ? '<button class="primary" data-action="new-exercise">+ New exercise</button>' : '';
  shell(`<div class="page-head"><div><p class="eyebrow">Exercise control</p><h1>Your exercises</h1><p class="muted">A place to prepare, play, and look back.</p></div>${create}</div>
    ${exercises.length ? `<div class="cards">${exercises.map(ex => `<a class="card exercise-card" href="#exercise/${ex.id}">${badge(ex.status)}<div><h2>${esc(ex.name)}</h2><p class="muted">${esc(ex.description.slice(0,140))}${ex.description.length > 140 ? '…' : ''}</p></div><footer class="row between"><span class="muted">${ex.member_count} member${ex.member_count === 1 ? '' : 's'}</span><strong>Open exercise →</strong></footer></a>`).join('')}</div>`
      : empty(state.user.is_admin ? 'Set the scene' : 'No exercises assigned yet', state.user.is_admin ? 'Create an exercise, add your groups, and prepare the first inject.' : 'Your director will add you to an exercise. Refresh this page when you are ready.', create)}`);
}

async function exercisePage(id, quiet = false, section = '') {
  const exercise = await api(`/exercises/${id}`);
  if (state.exercise?.id !== exercise.id) { state.selected = null; state.tab = exercise.is_director ? 'board' : 'injects'; state.filter = ''; state.previewDrafts = true; state.previewGroup = ''; state.previewAnswers = {}; state.previewVariants = {}; }
  if (quiet && (state.dirty || editor.open)) return;
  if (exercise.is_director && ['board', 'injects', 'people'].includes(section)) state.tab = section;
  const focusedCell = document.activeElement?.closest('.release-cell');
  const boardScroll = document.querySelector('.release-table-wrap')?.scrollLeft || 0;
  const boardScrollTop = document.querySelector('.release-table-wrap')?.scrollTop || 0;
  state.exercise = exercise;
  state.preview = section === 'preview' && exercise.is_director;
  if (state.preview) { previewPage(); return; }
  if (!exercise.injects.some(i => i.id === state.selected)) state.selected = exercise.injects[0]?.id ?? null;
  const director = exercise.is_director;
  state.responses = state.selected && (!director || state.tab === 'injects') ? await api(`/injects/${state.selected}/responses`) : [];
  if (director && state.tab === 'board') state.board = await api(`/exercises/${id}/board`);
  const statusAction = exercise.status === 'live' ? ['closed', 'Close exercise'] : ['live', exercise.status === 'closed' ? 'Reopen exercise' : 'Start exercise'];
  const controls = director ? `<div><div class="row"><a class="button" href="#exercise/${id}/preview">Preview exercise</a><a class="button" href="/api/exercises/${id}/export.zip">↓ Export results</a><button data-action="edit-exercise">Edit</button><button class="primary" data-action="status" data-status="${statusAction[0]}">${statusAction[1]}</button></div><small class="muted">Export includes CSV, JSON, and all attachments.</small></div>` : '<button data-action="refresh">Refresh</button>';
  shell(`<div class="page-head"><div><p class="eyebrow"><a href="#">Exercises</a> / Exercise room</p><div class="row"><h1>${esc(exercise.name)}</h1>${badge(exercise.status)}</div><p class="muted text">${esc(exercise.description)}</p></div>${controls}</div>
    ${exercise.status === 'draft' ? '<div class="banner">This exercise is being prepared. Participants will see published injects when the director starts the exercise.</div>' : ''}
    ${exercise.status === 'closed' ? '<div class="banner">The exercise is closed. Responses remain available for review and export.</div>' : ''}
    ${director ? `<div class="tabs" role="tablist" aria-label="Exercise sections"><button role="tab" aria-selected="${state.tab === 'board'}" class="${state.tab === 'board' ? 'active' : ''}" data-action="tab" data-tab="board">Director board</button><button role="tab" aria-selected="${state.tab === 'injects'}" class="${state.tab === 'injects' ? 'active' : ''}" data-action="tab" data-tab="injects">Injects & responses</button><button role="tab" aria-selected="${state.tab === 'people'}" class="${state.tab === 'people' ? 'active' : ''}" data-action="tab" data-tab="people">Groups & members</button></div>` : ''}
    ${director && state.tab === 'board' ? directorBoard() : state.tab === 'people' && director ? peoplePanel() : injectPanel()}`);
  const boardTable = document.querySelector('.release-table-wrap');
  if (boardTable) {
    boardTable.scrollLeft = boardScroll;
    boardTable.scrollTop = boardScrollTop;
    if (focusedCell) [...boardTable.querySelectorAll('.release-cell')].find(cell =>
      cell.dataset.id === focusedCell.dataset.id && cell.dataset.targetKey === focusedCell.dataset.targetKey
    )?.focus({ preventScroll: true });
  }
  focusInjectSequence();
}

function directorBoard() {
  const { targets, injects } = state.board;
  const labels = { unpublished: 'Unpublished', published: 'Published', answered: 'Answered' };
  const counts = Object.fromEntries(Object.keys(labels).map(status => [status,
    injects.reduce((total, inject) => total + inject.cells.filter(cell => cell.status === status).length, 0)
  ]));
  const header = `<header class="release-head row between"><div><p class="eyebrow">Exercise direction</p><h2>Release board</h2><p class="muted">Click a cell to publish or unpublish for that team or participant.</p></div><div class="row"><button data-action="refresh">Refresh</button><button class="primary" data-action="new-inject">+ New inject</button></div></header>`;
  if (!targets.length) return `<section class="card release-board">${header}${empty('Add your teams or participants', 'Assign exercise members to start managing releases for each team or person.', '<button class="primary" data-action="member">+ Assign member</button><button data-action="new-group">+ Add team</button>')}</section>`;
  if (!injects.length) return `<section class="card release-board">${header}${empty('Prepare your first inject', 'Your injects will appear as rows, with one release control for each team or participant.')}</section>`;
  return `<section class="card release-board">${header}
    <div class="release-summary" aria-label="Release status summary">${Object.entries(labels).map(([status, label]) => `<div class="release-stat ${status}"><span class="release-dot" aria-hidden="true"></span><strong>${counts[status]}</strong><span>${label}</span></div>`).join('')}</div>
    <div class="table-wrap release-table-wrap" tabindex="0" role="region" aria-label="Injects by team and participant, scroll to see all columns"><table class="release-table"><caption class="sr-only">Inject publication and question completion for every team and individual participant</caption><thead><tr><th scope="col">Inject sequence</th>${targets.map(target => `<th scope="col"><strong>${esc(target.name)}</strong><span>${target.kind === 'group' ? `Team · ${target.member_count} member${target.member_count === 1 ? '' : 's'}` : 'Individual participant'}</span></th>`).join('')}</tr></thead><tbody>${injects.map((inject, index) => `<tr><th scope="row"><div class="release-inject"><span class="release-number">${String(index + 1).padStart(2, '0')}</span><div><button class="inject-link" data-action="board-open-inject" data-id="${inject.id}" aria-label="Open inject: ${esc(inject.name)}">${esc(inject.name)}</button>${inject.session_time ? `<span class="muted">${esc(inject.session_time)}</span>` : ''}${inject.optional ? '<span class="badge optional">Optional</span>' : ''}</div></div></th>${inject.cells.map((cell, position) => {
      const target = targets[position];
      const label = labels[cell.status];
      const action = cell.published ? 'Unpublish' : 'Publish';
      return `<td><button type="button" class="release-cell ${cell.status}" data-action="team-publication" data-id="${inject.id}" data-target-key="${esc(target.key)}" data-target-kind="${target.kind}" data-target-id="${target.id}" data-published="${cell.published}" data-status="${cell.status}" aria-pressed="${cell.published}" aria-label="${esc(`${action} ${inject.name} for ${target.name}. ${label}. ${cell.answered_count} of ${cell.question_count} questions answered.`)}" title="${action} for ${esc(target.name)}"><span class="release-state"><span class="release-dot" aria-hidden="true">${cell.status === 'answered' ? '✓' : ''}</span><strong>${label}</strong></span><span>${cell.question_count ? `${cell.answered_count} / ${cell.question_count} questions` : 'No questions'}</span><small>${cell.response_count} repl${cell.response_count === 1 ? 'y' : 'ies'}</small></button></td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div>
    <footer class="release-footer"><p>A team is answered when its saved replies cover every question, including optional questions. Information updates stay orange when published.</p><span>Updates every 15 seconds</span></footer></section>`;
}

function peoplePanel() {
  const ex = state.exercise;
  return `<div class="card stack"><div class="row between"><div><h2>Groups & members</h2><p class="muted">Each response records its author and their group at the time of submission.</p></div><div class="row"><button data-action="new-group">+ Add group</button><button class="primary" data-action="member">+ Assign member</button></div></div>
    <div class="row">${ex.groups.length ? ex.groups.map(g => `<span class="stat">${esc(g.name)} · ${ex.members.filter(m => m.group_id === g.id).length} members · <strong>${g.response_count}</strong> replies</span>`).join('') : '<p class="muted">Add groups to attribute participant replies to a team.</p>'}</div>
    <div class="table-wrap"><table><thead><tr><th>Person</th><th>Role</th><th>Group</th><th>Manage</th></tr></thead><tbody>${ex.members.map(m => `<tr><td><strong>${esc(m.name)}</strong><br><span class="muted">${esc(m.username)}</span></td><td>${esc(m.role)}</td><td>${esc(m.group_name || 'Unassigned')}</td><td><div class="row"><button class="small" data-action="member" data-id="${m.user_id}">Edit</button><button class="small danger" data-action="remove-member" data-id="${m.user_id}">Remove</button></div></td></tr>`).join('')}</tbody></table></div></div>`;
}

function injectNavigation(injects, preview = false) {
  return `<aside class="card inject-list" aria-label="${preview ? 'Preview injects' : 'Injects'}"><header class="row between"><strong>${state.exercise.is_director ? 'Inject sequence' : 'Published injects'}</strong>${state.exercise.is_director && !preview ? '<button class="small" data-action="new-inject">+ Add</button>' : ''}</header>
    <div class="mobile-inject-select field"><label for="inject-picker">Choose inject</label><select id="inject-picker" data-change="inject-picker">${options(injects, state.selected)}</select></div><div class="inject-items">${injects.map((item, index) => `<button class="inject-item ${item.id === state.selected ? 'active' : ''}" data-action="select-inject" data-id="${item.id}"><div class="row">${badge(item.published ? 'published' : 'draft')}${item.optional ? badge('optional') : ''}</div><strong>${index + 1}. ${esc(item.name)}</strong><span class="muted">${item.information_only ? 'Information update' : `${item.questions.length} question${item.questions.length === 1 ? '' : 's'}`}${!preview && state.exercise.is_director ? ` · ${item.response_count} replies` : ''}${item.session_time ? `<br>${esc(item.session_time)}` : ''}</span></button>`).join('')}</div>${!injects.length ? '<p class="muted">No injects in this view.</p>' : ''}</aside>`;
}

function focusInjectSequence() {
  const list = document.querySelector('.inject-items');
  const selected = list?.querySelector('.active');
  if (selected && list.clientHeight) list.scrollTop += selected.getBoundingClientRect().top - list.getBoundingClientRect().top - list.clientHeight / 3;
}

function variantPicker(inject, preview = false) {
  const variants = Object.entries(inject.variants || {});
  if (!variants.length) return '';
  const key = preview ? state.previewVariants[inject.id] || inject.active_variant : inject.active_variant;
  return `<div class="field variant-picker"><label for="variant-picker">${preview ? 'Preview variant' : 'Release variant'}</label><select id="variant-picker" data-change="${preview ? 'preview-variant' : 'release-variant'}" ${!preview && (inject.published || inject.response_count) ? 'disabled' : ''}>${variants.map(([value, item]) => `<option value="${esc(value)}" ${value === key ? 'selected' : ''}>${esc(item.name)}</option>`).join('')}</select><small>${preview ? 'Try another variant without changing the exercise.' : 'Applies to every participant. Select before publication and the first response.'}</small></div>`;
}

function previewInject() {
  const inject = state.exercise.injects.find(item => item.id === state.selected);
  const variant = inject?.variants?.[state.previewVariants[inject.id] || inject.active_variant];
  if (!inject) return null;
  return { ...inject, ...(variant ? { description: variant.description, attachments: variant.attachments } : {}), published: previewPublished(inject) };
}

function previewPublished(inject) {
  const override = state.previewGroup && inject.publications?.find(item => String(item.group_id) === state.previewGroup);
  return override ? override.published : inject.published_by_default ?? inject.published;
}

function previewPage() {
  const ex = state.exercise;
  const injects = ex.injects.filter(inject => state.previewDrafts || previewPublished(inject)).map(inject => ({ ...inject, published: previewPublished(inject) }));
  if (!injects.some(inject => inject.id === state.selected)) state.selected = injects[0]?.id ?? null;
  const inject = previewInject();
  const group = ex.groups.find(item => String(item.id) === state.previewGroup);
  shell(`<div class="page-head"><div><p class="eyebrow">${esc(ex.name)} / Participant view</p><h1>Exercise preview</h1><p class="muted text">${esc(ex.description)}</p></div><a class="button" href="#exercise/${ex.id}"><span aria-hidden="true">←</span> Back to exercise</a></div>
    <div class="banner preview-banner"><strong>Preview only</strong><p>Review the participant experience, including draft injects. Answers entered here are never saved as exercise responses.</p></div>
    <div class="card preview-controls"><div class="field"><label for="preview-group">Preview group</label><select id="preview-group" data-change="preview-group"><option value="">No group</option>${options(ex.groups, state.previewGroup)}</select></div><label class="check"><input type="checkbox" data-change="preview-drafts" ${state.previewDrafts ? 'checked' : ''}>Include unpublished injects</label><span class="muted">${injects.length} inject${injects.length === 1 ? '' : 's'} · ${injects.reduce((count, item) => count + item.questions.length, 0)} questions</span></div>
    <div class="exercise-grid">${injectNavigation(injects, true)}
    <section class="inject-panel">${inject ? `<div class="card">${variantPicker(inject, true)}<header class="inject-head">${badge(inject.published ? 'published' : 'draft')}<h2>${esc(inject.name)}</h2>${inject.sender ? `<p class="muted">From: <strong>${esc(inject.sender)}</strong></p>` : ''}<p class="text">${esc(inject.description)}</p></header>${attachmentList(inject.attachments)}</div>
      ${inject.information_only ? '<div class="card"><p class="muted">Information update — no response requested.</p></div>' : `<div class="card"><h2>Reply to this inject</h2><p class="muted">Submitted by <strong>Example participant</strong>${group ? ` for <strong>${esc(group.name)}</strong>` : ''}. Each person submits a named reply for their group.</p><form data-form="preview-response">${errorSlot}${inject.questions.map(question => questionInput(question, state.previewAnswers[inject.id]?.[question.id])).join('')}<div class="preview-feedback" role="status" data-preview-feedback hidden></div><div class="row between"><span class="muted">Try the questions without saving a response.</span><button class="primary" type="submit">Check response</button></div></form><p class="muted preview-evidence">Participants can attach supporting files after submitting a response.</p></div>`}`
      : empty(state.previewDrafts ? 'No injects to preview' : 'No published injects', state.previewDrafts ? 'Return to the exercise to prepare your first inject.' : 'Enable “Include unpublished injects” to preview the drafts.')}</section></div>`);
  focusInjectSequence();
}

function injectPanel() {
  const ex = state.exercise;
  const inject = ex.injects.find(i => i.id === state.selected);
  return `<div class="exercise-grid">${injectNavigation(ex.injects)}<section class="inject-panel">${inject ? injectDetail(inject) : empty(ex.is_director ? 'Prepare your first inject' : 'Waiting for the next inject', ex.is_director ? 'Give it a name, set the scene, and add the questions you want the room to consider.' : 'Published injects will appear here when your director releases them.', ex.is_director ? '<button class="primary" data-action="new-inject">+ New inject</button>' : '<button data-action="refresh">Refresh</button>')}</section></div>`;
}

function attachmentList(attachments) {
  return [...(attachments || [])].sort((a, b) => Number(b.media_type.startsWith('image/')) - Number(a.media_type.startsWith('image/'))).map(a => {
    const url = `/api/attachments/${a.id}`;
    if (a.media_type.startsWith('image/')) return `<figure class="attachment attachment-image"><a class="image-view" href="${url}" target="_blank" rel="noopener" aria-label="View full-size image: ${esc(a.filename)}"><img src="${url}" alt="${esc(a.filename)}" loading="lazy"></a><figcaption><span class="muted">${esc(a.filename)}</span><div class="row"><span class="muted">Click image to view full size</span><a href="${url}" download>Download image</a></div></figcaption></figure>`;
    const media = a.media_type.startsWith('video/') ? `<video src="${url}" controls preload="metadata"></video>` : a.media_type.startsWith('audio/') ? `<audio src="${url}" controls preload="metadata"></audio>` : '';
    return `<div class="attachment">${media}<a href="${url}" ${media ? 'download' : ''}>${esc(a.filename)}</a> <span class="muted">· ${(a.size / 1024 / 1024).toFixed(2)} MB</span></div>`;
  }).join('');
}

function uploadForm(kind, id) {
  return `<form class="upload" data-form="upload" data-kind="${kind}" data-id="${id}">${errorSlot}<label for="upload-${kind}-${id}">${kind === 'injects' ? 'Attach inject material' : 'Attach supporting evidence'}</label><p class="muted">Images, video, audio, documents, or text. Choose one or more files.</p><input id="upload-${kind}-${id}" type="file" name="files" multiple required><button class="small" type="submit">Upload files</button></form>`;
}

function questionInput(q, answer) {
  const id = `answer-${q.id}`;
  const required = q.required ? 'required' : '';
  let control;
  if (q.type === 'list') {
    const count = Math.min(q.max_items, Math.max(3, answer?.length || 0));
    control = `<div data-list="${esc(q.id)}" data-max="${q.max_items}" data-label="${esc(q.label)}">${Array.from({ length: count }, (_, index) => listItem(q.id, q.label, index, answer?.[index], q.required)).join('')}<button class="small" type="button" data-action="add-list-item" ${count >= q.max_items ? 'hidden' : ''}>+ Add item</button></div><small class="muted">Up to ${q.max_items} items. Leave unused items empty.</small>`;
  } else if (q.type === 'ranking') {
    control = `<div role="group" aria-labelledby="label-${id}">${q.options.map((_, index) => `<div class="field"><label for="${id}-${index}">Priority ${index + 1}</label><select id="${id}-${index}" name="${esc(q.id)}__rank" aria-label="${esc(`Priority ${index + 1} — ${q.label}`)}" ${required}><option value="">Select a priority</option>${q.options.map(item => `<option value="${esc(item)}" ${answer?.order?.[index] === item ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></div>`).join('')}<label for="${id}-reason">Reason for this order</label><textarea id="${id}-reason" name="${esc(q.id)}__reason" aria-label="${esc(`Reason for this order — ${q.label}`)}" maxlength="20000" ${required}>${esc(answer?.explanation)}</textarea><small class="muted">Use each priority once.</small></div>`;
  } else if (q.type === 'matrix') {
    control = `<div class="table-wrap"><table class="answer-matrix" aria-labelledby="label-${id}"><thead><tr><th scope="col">Topic</th>${q.columns.map(column => `<th scope="col">${esc(column)}</th>`).join('')}</tr></thead><tbody>${q.rows.map((row, ri) => `<tr><th scope="row">${esc(row)}</th>${q.columns.map((column, ci) => `<td><textarea name="${esc(q.id)}__r${ri}_c${ci}" aria-label="${esc(`${row} — ${column}`)}" maxlength="1000" ${required}>${esc(answer?.[row]?.[column])}</textarea></td>`).join('')}</tr>`).join('')}</tbody></table></div><small class="muted">Scroll the table horizontally to see every column.</small>`;
  } else if (q.type === 'long_text') control = `<textarea id="${id}" name="${esc(q.id)}" aria-label="${esc(q.label)}" ${required} maxlength="20000" ${q.max_words ? `data-max-words="${q.max_words}"` : ''}>${esc(answer)}</textarea>${q.max_words ? `<small class="muted" data-word-count>${String(answer || '').trim().split(/\s+/).filter(Boolean).length} / ${q.max_words} words</small>` : ''}`;
  else if (q.type === 'multiple_choice') control = `<div role="group" aria-labelledby="label-${id}">${q.options.map((o, i) => `<label class="check" for="${id}-${i}"><input id="${id}-${i}" type="checkbox" name="${esc(q.id)}" value="${esc(o)}" ${(answer || []).includes(o) ? 'checked' : ''}>${esc(o)}</label>`).join('')}</div>`;
  else if (q.type === 'single_choice') control = `<select id="${id}" name="${esc(q.id)}" ${required}><option value="">Select an option</option>${q.options.map(o => `<option value="${esc(o)}" ${o === answer ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  else if (q.type === 'boolean') control = `<select id="${id}" name="${esc(q.id)}" ${required}><option value="">Select an answer</option><option value="true" ${answer === true ? 'selected' : ''}>Yes</option><option value="false" ${answer === false ? 'selected' : ''}>No</option></select>`;
  else control = `<input id="${id}" name="${esc(q.id)}" type="${q.type === 'short_text' ? 'text' : q.type}" ${q.type === 'number' ? 'step="any"' : ''} ${required} ${q.type === 'short_text' ? 'maxlength="1000"' : ''} value="${esc(answer)}">`;
  return `<div class="question"><label id="label-${id}" ${['multiple_choice', 'list', 'ranking', 'matrix'].includes(q.type) ? '' : `for="${id}"`}>${esc(q.label)}${q.required ? ' <span class="muted">(required)</span>' : ''}</label>${q.hint ? `<p class="muted question-hint">${esc(q.hint)}</p>` : ''}${control}</div>`;
}

function listItem(id, label, index, value = '', required = false) {
  return `<div class="field list-item"><label for="answer-${esc(id)}-${index}">Item ${index + 1}</label><input id="answer-${esc(id)}-${index}" name="${esc(id)}" aria-label="${esc(`Item ${index + 1} — ${label}`)}" value="${esc(value)}" maxlength="1000" ${required && index === 0 ? 'required' : ''}></div>`;
}

function injectDetail(inject) {
  const ex = state.exercise;
  const mine = state.responses.find(r => r.user_id === state.user.id);
  const canReply = ex.status === 'live' && inject.published && !inject.information_only;
  const group = mine ? mine.group_name : ex.groups.find(g => g.id === ex.membership?.group_id)?.name;
  return `<div class="card"><header class="inject-head"><div class="row between"><div class="row">${badge(inject.published ? 'published' : 'draft')}${inject.optional ? badge('optional') : ''}</div>${ex.is_director ? `<div class="row"><button class="small" data-action="edit-inject">Edit inject</button><button class="small ${inject.published ? '' : 'primary'}" data-action="publication">${inject.published ? 'Unpublish' : 'Publish inject'}</button></div>` : ''}</div><h2>${esc(inject.name)}</h2>${inject.sender ? `<p class="muted">From: <strong>${esc(inject.sender)}</strong></p>` : ''}${ex.is_director && inject.session_time ? `<p class="muted">Planned release: ${esc(inject.session_time)}</p>` : ''}${ex.is_director ? variantPicker(inject) : ''}<p class="text">${esc(inject.description)}</p></header>
    ${attachmentList(inject.attachments)}${ex.is_director && ex.status !== 'closed' ? uploadForm('injects', inject.id) : ''}
    ${inject.information_only ? '<p class="muted">Information update — no response requested.</p>' : ''}
    ${ex.is_director ? `<h3>Questions</h3>${inject.questions.length ? inject.questions.map(q => `<div class="question-preview"><strong>${esc(q.label)}</strong><small>${types[q.type]}${q.required ? ' · Required' : ''}${q.max_words ? ` · Up to ${q.max_words} words` : ''}</small>${q.options.length ? `<small>${esc(q.options.join(' / '))}</small>` : ''}${q.type === 'matrix' ? `<small>${q.rows.length} rows · ${q.columns.length} columns</small>` : ''}</div>`).join('') : `<p class="muted">${inject.information_only ? 'No questions for this update.' : 'Participants can acknowledge this inject by submitting a reply.'}</p>`}${inject.facilitator_notes ? `<details class="facilitator-notes"><summary>Director notes</summary><p class="muted">Visible only to exercise directors.</p><div class="text">${esc(inject.facilitator_notes)}</div></details>` : ''}` : ''}</div>
    ${(!ex.is_director || mine) && !inject.information_only ? `<div class="card"><div class="row between"><h2>${mine ? 'Your response' : 'Reply to this inject'}</h2>${mine ? `<span class="muted">Saved · revision ${mine.revision}</span>` : ''}</div><p class="muted">Submitted by <strong>${esc(state.user.name)}</strong>${group ? ` for <strong>${esc(group)}</strong>` : ''}. Your name stays attached to the reply.</p>
      ${canReply ? `<form data-form="response" data-revision="${mine?.revision || 0}">${errorSlot}${inject.questions.map(q => questionInput(q, mine?.answers[q.id])).join('')}<div class="row between"><span class="muted">${mine ? `Last saved ${esc(date(mine.updated_at))}` : 'Each group member can submit their own reply.'}</span><button class="primary" type="submit">${mine ? 'Save changes' : 'Submit response'}</button></div></form>` : '<p class="muted">Replies are currently closed.</p>'}
      ${mine ? attachmentList(mine.attachments) + (canReply ? uploadForm('responses', mine.id) : '') : '<p class="muted">You can attach supporting files after submitting your response.</p>'}</div>` : ''}
    ${!inject.information_only ? `<div class="card"><div class="row between"><h2>${ex.is_director ? 'Responses' : 'Group responses'}</h2><button class="small" data-action="refresh">Refresh</button></div><p class="muted">Replies are attributed to their named authors and groups.</p>${ex.is_director ? `<div class="stats"><span class="stat"><strong>${state.responses.length}</strong> respondents</span><span class="stat"><strong>${new Set(state.responses.map(r => r.group_id).filter(Boolean)).size}</strong> groups represented</span></div><div class="field"><label for="response-filter">Filter by group</label><select id="response-filter" data-change="filter"><option value="">All groups</option>${options(ex.groups, state.filter)}<option value="none" ${state.filter === 'none' ? 'selected' : ''}>No group</option></select></div>` : ''}
    <div id="response-list">${responseList(inject)}</div></div>` : ''}`;
}

function responseList(inject) {
  const rows = state.responses.filter(r => !state.filter || (state.filter === 'none' ? !r.group_id : String(r.group_id) === state.filter));
  return rows.length ? rows.map(r => `<article class="response"><div class="row between"><div><strong>${esc(r.author_name)}</strong><span class="muted"> · ${esc(r.group_name || 'No group')}</span></div><small class="muted">${esc(date(r.updated_at))} · v${r.revision}</small></div><dl>${inject.questions.map(q => {
    const a = r.answers[q.id];
    return `<dt>${esc(q.label)}</dt><dd>${answerDisplay(q, a)}</dd>`;
  }).join('')}</dl>${attachmentList(r.attachments)}</article>`).join('') : '<p class="muted">No responses yet.</p>';
}

function answerDisplay(q, answer) {
  if (answer == null) return '—';
  if (q.type === 'matrix') return `<div class="table-wrap"><table><thead><tr><th>Topic</th>${q.columns.map(column => `<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${q.rows.map(row => `<tr><th scope="row">${esc(row)}</th>${q.columns.map(column => `<td>${esc(answer[row]?.[column] || '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  if (q.type === 'ranking') return `<ol>${(answer.order || []).map(item => `<li>${esc(item)}</li>`).join('')}</ol><p class="text">${esc(answer.explanation)}</p>`;
  if (Array.isArray(answer)) return `<ul>${answer.map(item => `<li>${esc(item)}</li>`).join('')}</ul>`;
  return esc(answer === true ? 'Yes' : answer === false ? 'No' : answer);
}

async function usersPage() {
  const users = await api('/users');
  shell(`<div class="page-head"><div><p class="eyebrow">Local accounts</p><h1>People</h1><p class="muted">Create an account, then assign the person to an exercise and group.</p></div><button class="primary" data-action="new-user">+ Create account</button></div><div class="card table-wrap"><table><thead><tr><th>Name</th><th>Username</th><th>Access</th><th>Account</th></tr></thead><tbody>${users.map(u => `<tr><td><strong>${esc(u.name)}</strong></td><td>${esc(u.username)}</td><td>${u.is_admin ? 'Administrator' : 'Exercise member'}</td><td>${u.id !== state.user.id ? `<button class="small" data-action="reset-password" data-id="${u.id}">Reset password</button>` : '<span class="muted">Your account</span>'}</td></tr>`).join('')}</tbody></table></div>`, 'users');
}

function accountPage() {
  shell(`<div class="page-head"><div><p class="eyebrow">${esc(state.user.username)}</p><h1>Your account</h1><p class="muted">${esc(state.user.name)}</p></div></div><div class="card"><h2>Change password</h2><form data-form="password">${errorSlot}${field('current_password', 'Current password', '', 'password', 'required autocomplete="current-password" maxlength="256"')}${field('new_password', 'New password', '', 'password', 'required minlength="10" maxlength="256" autocomplete="new-password"')}<button class="primary" type="submit">Update password</button></form></div>`);
}

async function load(quiet = false) {
  if (loading) return;
  loading = true;
  try {
    if (!state.user) {
      const session = await api('/session');
      state.user = session.user; state.csrf = session.csrf_token;
      if (!state.user) { auth(session.needs_setup); return; }
    }
    const route = location.hash.slice(1).split('/');
    if (route[0] === 'exercise' && /^\d+$/.test(route[1])) await exercisePage(Number(route[1]), quiet, route[2] || '');
    else if (route[0] === 'users' && state.user.is_admin) await usersPage();
    else if (route[0] === 'account') accountPage();
    else await dashboard();
  } catch (error) { notify(error.message); }
  finally { loading = false; }
}

function dialog(title, body, formName, data = '') {
  editor.innerHTML = `<form data-form="${formName}" ${data}><header class="dialog-head row between"><h2 id="dialog-title">${title}</h2><button type="button" class="small ghost" data-action="close-dialog" aria-label="Close dialog">×</button></header><div class="dialog-body">${errorSlot}${body}</div><footer class="dialog-footer"><button type="button" data-action="close-dialog">Cancel</button><button class="primary" type="submit">Save</button></footer></form>`;
  editor.showModal();
}

function questionEditor(q = { id: crypto.randomUUID(), label: '', type: 'long_text', required: false, options: [] }, locked = false) {
  const choices = ['single_choice', 'multiple_choice', 'ranking'].includes(q.type);
  return `<fieldset class="question-editor" data-question="${esc(q.id)}" ${locked ? 'disabled' : ''}><div class="row between"><strong>Question</strong>${locked ? '' : '<button class="small danger" type="button" data-action="remove-question">Remove</button>'}</div>
    <div class="field"><label>Question text<input name="q_label" value="${esc(q.label)}" required maxlength="1000"></label></div><div class="form-grid"><div class="field"><label for="q-type-${esc(q.id)}">Answer type</label><select id="q-type-${esc(q.id)}" name="q_type" data-change="question-type">${Object.entries(types).map(([key, label]) => `<option value="${key}" ${q.type === key ? 'selected' : ''}>${label}</option>`).join('')}</select></div><label class="check"><input type="checkbox" name="q_required" ${q.required ? 'checked' : ''}>Required answer</label></div>
    <div class="field" data-kinds="single_choice multiple_choice ranking" ${choices ? '' : 'hidden'}><label for="q-options-${esc(q.id)}">Options, one per line</label><textarea id="q-options-${esc(q.id)}" name="q_options" ${choices ? 'required' : ''}>${esc(q.options.join('\n'))}</textarea></div>
    <div data-kinds="matrix" ${q.type === 'matrix' ? '' : 'hidden'}><div class="field"><label for="q-rows-${esc(q.id)}">Rows, one per line</label><textarea id="q-rows-${esc(q.id)}" name="q_rows" ${q.type === 'matrix' ? 'required' : ''}>${esc((q.rows || []).join('\n'))}</textarea></div><div class="field"><label for="q-columns-${esc(q.id)}">Columns, one per line</label><textarea id="q-columns-${esc(q.id)}" name="q_columns" ${q.type === 'matrix' ? 'required' : ''}>${esc((q.columns || []).join('\n'))}</textarea></div></div>
    <div class="field" data-kinds="list" ${q.type === 'list' ? '' : 'hidden'}><label>Maximum items<input name="q_max_items" type="number" min="1" max="20" value="${q.max_items || 10}"></label></div>
    <div class="field" data-kinds="short_text long_text" ${['short_text', 'long_text'].includes(q.type) ? '' : 'hidden'}><label>Maximum words (optional)<input name="q_max_words" type="number" min="1" max="5000" value="${q.max_words || ''}"></label></div>
    <div class="field"><label for="q-hint-${esc(q.id)}">Participant guidance (optional)</label><textarea id="q-hint-${esc(q.id)}" name="q_hint" maxlength="2000">${esc(q.hint)}</textarea></div></fieldset>`;
}

function injectDialog(inject = null) {
  const locked = !!inject?.response_count;
  dialog(inject ? 'Edit inject' : 'New inject', `${field('name', 'Inject name', inject?.name, 'text', 'required maxlength="160"')}${field('sender', 'Sender', inject?.sender, 'text', 'maxlength="250"')}${textarea('description', 'Description / situation', inject?.description, 'maxlength="20000"')}
    <details class="editor-director-details"><summary>Director settings</summary>${field('session_time', 'Planned release (director only)', inject?.session_time, 'text', 'maxlength="250"')}${textarea('facilitator_notes', 'Director notes (private)', inject?.facilitator_notes, 'maxlength="30000"')}<label class="check"><input type="checkbox" name="optional" ${inject?.optional ? 'checked' : ''}>Optional inject</label></details>
    <label class="check"><input type="checkbox" name="information_only" data-change="information-only" ${inject?.information_only ? 'checked' : ''} ${locked ? 'disabled' : ''}>Information update — no response requested</label>
    <section data-question-section ${inject?.information_only ? 'hidden' : ''}><div class="row between"><h3>Questions</h3>${locked ? '<small class="muted">Locked after the first response</small>' : '<button type="button" class="small" data-action="add-question">+ Add question</button>'}</div><div id="question-editors">${(inject?.questions || [{ id: crypto.randomUUID(), label: '', type: 'long_text', required: true, options: [] }]).map(q => questionEditor(q, locked)).join('')}</div></section>`, 'inject', `data-id="${inject?.id || ''}" data-locked="${locked}"`);
}

async function memberDialog(id) {
  const users = await api(`/exercises/${state.exercise.id}/available-users`);
  const member = state.exercise.members.find(m => m.user_id === Number(id));
  dialog(member ? 'Edit exercise member' : 'Assign exercise member', `<div class="field"><label for="member-user">Person</label><select id="member-user" name="user_id" required>${options(users, member?.user_id)}</select></div><div class="form-grid"><div class="field"><label for="member-role">Exercise role</label><select id="member-role" name="role"><option value="participant">Participant</option><option value="director" ${member?.role === 'director' ? 'selected' : ''}>Director</option></select></div><div class="field"><label for="member-group">Group</label><select id="member-group" name="group_id"><option value="">No group</option>${options(state.exercise.groups, member?.group_id)}</select></div></div><p class="muted">Directors can prepare and publish injects, review all replies, and export this exercise. Participants can reply and review their group's replies.</p>`, 'member');
}

function allowDiscard() {
  if (!state.dirty || confirm('Discard unsaved response changes?')) { state.dirty = false; return true; }
  return false;
}

document.addEventListener('click', event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  run(null, async () => {
    const ex = state.exercise;
    const inject = ex?.injects.find(i => i.id === state.selected);
    if (action === 'logout' && allowDiscard()) { await api('/logout', {}); state.user = null; state.csrf = ''; location.hash = ''; await load(); }
    else if (action === 'new-exercise' || action === 'edit-exercise') dialog(action === 'new-exercise' ? 'New exercise' : 'Edit exercise', field('name', 'Exercise name', action === 'edit-exercise' ? ex.name : '', 'text', 'required maxlength="160"') + textarea('description', 'Description', action === 'edit-exercise' ? ex.description : '', 'maxlength="20000"'), 'exercise', `data-id="${action === 'edit-exercise' ? ex.id : ''}"`);
    else if (action === 'new-user') dialog('Create local account', field('name', 'Full name', '', 'text', 'required maxlength="120"') + field('username', 'Username', '', 'text', 'required minlength="2" maxlength="80" pattern="[a-zA-Z0-9_.-]+" autocomplete="off"') + field('password', 'Initial password', '', 'password', 'required minlength="10" maxlength="256" autocomplete="new-password"'), 'user');
    else if (action === 'reset-password') dialog('Reset account password', '<p class="muted">This signs the person out of all sessions.</p>' + field('password', 'New password', '', 'password', 'required minlength="10" maxlength="256" autocomplete="new-password"'), 'reset', `data-id="${button.dataset.id}"`);
    else if (action === 'new-group') dialog('Add group', field('name', 'Group name', '', 'text', 'required maxlength="120"'), 'group');
    else if (action === 'member') await memberDialog(button.dataset.id);
    else if (action === 'remove-member' && confirm('Remove this person from the exercise? Their responses will be retained.')) { await api(`/exercises/${ex.id}/members/${button.dataset.id}/remove`, {}); await load(); }
    else if (action === 'new-inject') injectDialog();
    else if (action === 'edit-inject') injectDialog(inject);
    else if (action === 'close-dialog') editor.close();
    else if (action === 'add-question') editor.querySelector('#question-editors').insertAdjacentHTML('beforeend', questionEditor());
    else if (action === 'remove-question') button.closest('[data-question]').remove();
    else if (action === 'add-list-item') {
      const list = button.closest('[data-list]');
      const count = list.querySelectorAll('input').length;
      if (count < Number(list.dataset.max)) button.insertAdjacentHTML('beforebegin', listItem(list.dataset.list, list.dataset.label, count));
      button.hidden = count + 1 >= Number(list.dataset.max);
    }
    else if (action === 'select-inject' && allowDiscard()) { state.selected = Number(button.dataset.id); state.filter = ''; await load(); }
    else if (action === 'board-open-inject') { state.selected = Number(button.dataset.id); state.filter = ''; state.tab = 'injects'; location.hash = `exercise/${ex.id}/injects`; }
    else if (action === 'tab' && allowDiscard()) { state.tab = button.dataset.tab; const hash = `#exercise/${ex.id}/${state.tab}`; if (location.hash === hash) await load(); else location.hash = hash; }
    else if (action === 'refresh' && allowDiscard()) await load();
    else if (action === 'team-publication') {
      if (button.disabled) return;
      button.disabled = true;
      const published = button.dataset.published !== 'true';
      try {
        await api(`/injects/${button.dataset.id}/publication`, {
          published, [`${button.dataset.targetKind === 'group' ? 'group' : 'user'}_id`]: Number(button.dataset.targetId)
        });
        notify(published ? 'Inject published for this recipient.' : 'Inject unpublished for this recipient.');
        await load();
      } finally { if (button.isConnected) button.disabled = false; }
    }
    else if (action === 'publication') { await api(`/injects/${inject.id}/publication`, { published: !inject.published }); notify(inject.published ? 'Inject unpublished.' : 'Inject published.'); await load(); }
    else if (action === 'status') {
      if (button.dataset.status === 'closed' && !confirm('Close this exercise? Participants will no longer be able to edit replies.')) return;
      await api(`/exercises/${ex.id}/status`, { status: button.dataset.status }); await load();
    }
  });
});

document.addEventListener('change', event => {
  const target = event.target;
  if (target.dataset.change === 'question-type') {
    for (const section of target.closest('[data-question]').querySelectorAll('[data-kinds]')) {
      section.hidden = !section.dataset.kinds.split(' ').includes(target.value);
      for (const textarea of section.querySelectorAll('textarea')) textarea.required = !section.hidden;
    }
  } else if (target.dataset.change === 'information-only') {
    const section = editor.querySelector('[data-question-section]');
    section.hidden = target.checked;
    for (const question of section.querySelectorAll('fieldset')) question.disabled = target.checked;
  } else if (target.dataset.change === 'inject-picker') {
    if (allowDiscard()) { state.selected = Number(target.value); state.filter = ''; load(); }
    else target.value = state.selected;
  } else if (target.dataset.change === 'preview-variant') {
    state.previewVariants[state.selected] = target.value;
    previewPage();
  } else if (target.dataset.change === 'release-variant') {
    run(null, async () => { try { await api(`/injects/${state.selected}/variant`, { key: target.value }); } finally { await load(); } });
  } else if (target.dataset.change === 'filter') {
    state.filter = target.value;
    document.querySelector('#response-list').innerHTML = responseList(state.exercise.injects.find(i => i.id === state.selected));
  } else if (target.dataset.change === 'preview-group') {
    state.previewGroup = target.value;
    previewPage();
  } else if (target.dataset.change === 'preview-drafts') {
    state.previewDrafts = target.checked;
    previewPage();
  }
});

function readAnswers(form, inject) {
  const data = new FormData(form);
  const answers = {};
  for (const question of inject.questions) {
    const value = data.get(question.id);
    if (question.type === 'list') answers[question.id] = data.getAll(question.id).map(value => value.trim()).filter(Boolean);
    else if (question.type === 'ranking') {
      const order = data.getAll(`${question.id}__rank`);
      const explanation = data.get(`${question.id}__reason`) || '';
      if (order.some(Boolean) || explanation.trim()) answers[question.id] = { order, explanation };
    } else if (question.type === 'matrix') {
      const table = Object.fromEntries(question.rows.map((row, ri) => [row, Object.fromEntries(question.columns.map((column, ci) => [column, data.get(`${question.id}__r${ri}_c${ci}`) || '']))]));
      if (question.required || Object.values(table).some(cells => Object.values(cells).some(value => value.trim()))) answers[question.id] = table;
    } else if (question.type === 'multiple_choice') answers[question.id] = data.getAll(question.id);
    else if (value !== '' && value !== null) answers[question.id] = question.type === 'number' ? Number(value) : question.type === 'boolean' ? value === 'true' : value;
  }
  return answers;
}

document.addEventListener('input', event => {
  if (event.target.dataset.maxWords) {
    const count = event.target.value.trim().split(/\s+/).filter(Boolean).length;
    const counter = event.target.nextElementSibling;
    counter.textContent = `${count} / ${event.target.dataset.maxWords} words`;
    counter.classList.toggle('word-limit-exceeded', count > Number(event.target.dataset.maxWords));
    event.target.setAttribute('aria-invalid', String(count > Number(event.target.dataset.maxWords)));
  }
  if (event.target.closest('[data-form="response"]')) state.dirty = true;
  const previewForm = event.target.closest('[data-form="preview-response"]');
  if (previewForm) {
    const inject = state.exercise.injects.find(item => item.id === state.selected);
    state.previewAnswers[inject.id] = readAnswers(previewForm, inject);
    previewForm.querySelector('[data-preview-feedback]').hidden = true;
  }
});
document.addEventListener('submit', event => {
  const form = event.target.closest('[data-form]');
  if (!form) return;
  event.preventDefault();
  run(form, async () => {
    const values = Object.fromEntries(new FormData(form));
    const kind = form.dataset.form;
    const ex = state.exercise;
    if (kind === 'setup' || kind === 'login') {
      const result = await api(`/${kind}`, values);
      state.user = result.user; state.csrf = result.csrf_token; await load(); return;
    }
    if (kind === 'preview-response') {
      const inject = ex.injects.find(item => item.id === state.selected);
      const answers = readAnswers(form, inject);
      await api(`/injects/${inject.id}/preview`, { answers });
      const feedback = form.querySelector('[data-preview-feedback]');
      feedback.textContent = 'Preview response is valid. No response was saved.';
      feedback.hidden = false;
      return;
    }
    if (kind === 'exercise') {
      const result = await api(form.dataset.id ? `/exercises/${form.dataset.id}/details` : '/exercises', values);
      editor.close();
      if (!form.dataset.id) location.hash = `exercise/${result.id}`;
      else await load();
      return;
    }
    if (kind === 'user') await api('/users', values);
    else if (kind === 'reset') await api(`/users/${form.dataset.id}/password`, values);
    else if (kind === 'password') { await api('/password', values); form.reset(); notify('Password updated.'); return; }
    else if (kind === 'group') await api(`/exercises/${ex.id}/groups`, values);
    else if (kind === 'member') await api(`/exercises/${ex.id}/members`, { user_id: Number(values.user_id), role: values.role, group_id: values.group_id ? Number(values.group_id) : null });
    else if (kind === 'inject') {
      const informationOnly = form.querySelector('[name=information_only]').checked;
      const lines = (el, name) => el.querySelector(`[name=${name}]`).value.split('\n').map(value => value.trim()).filter(Boolean);
      const questions = informationOnly ? [] : form.dataset.locked === 'true' ? ex.injects.find(i => i.id === Number(form.dataset.id)).questions : [...form.querySelectorAll('[data-question]')].map(el => {
        const type = el.querySelector('[name=q_type]').value;
        return { id: el.dataset.question, label: el.querySelector('[name=q_label]').value, type, required: el.querySelector('[name=q_required]').checked,
          options: ['single_choice', 'multiple_choice', 'ranking'].includes(type) ? lines(el, 'q_options') : [],
          rows: type === 'matrix' ? lines(el, 'q_rows') : [], columns: type === 'matrix' ? lines(el, 'q_columns') : [],
          max_items: Number(el.querySelector('[name=q_max_items]').value || 10), max_words: el.querySelector('[name=q_max_words]').value ? Number(el.querySelector('[name=q_max_words]').value) : null,
          hint: el.querySelector('[name=q_hint]').value };
      });
      const payload = { name: values.name, description: values.description, questions, sender: values.sender, session_time: values.session_time,
        facilitator_notes: values.facilitator_notes, optional: form.querySelector('[name=optional]').checked, information_only: informationOnly };
      const result = await api(form.dataset.id ? `/injects/${form.dataset.id}/details` : `/exercises/${ex.id}/injects`, payload);
      if (result.id) { state.selected = result.id; state.tab = 'injects'; location.hash = `exercise/${ex.id}/injects`; }
    } else if (kind === 'response') {
      const inject = ex.injects.find(i => i.id === state.selected);
      const answers = readAnswers(form, inject);
      await api(`/injects/${inject.id}/responses`, { revision: Number(form.dataset.revision), answers });
      state.dirty = false; notify('Response saved with your name and group.');
    } else if (kind === 'upload') {
      const files = [...form.querySelector('[type=file]').files];
      let done = 0;
      for (const file of files) {
        const data = new FormData(); data.append('file', file);
        try { await api(`/${form.dataset.kind}/${form.dataset.id}/attachments`, data, true); done++; }
        catch (error) { throw new Error(`${done} of ${files.length} files uploaded. ${file.name}: ${error.message}`); }
      }
      notify(`${done} file${done === 1 ? '' : 's'} uploaded.`);
    }
    if (editor.open) editor.close();
    await load();
  });
});

document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]');
  if (link && !allowDiscard()) event.preventDefault();
});
window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('hashchange', () => { state.dirty = false; load(); });
setInterval(() => { if (state.user && location.hash.startsWith('#exercise/') && !location.hash.endsWith('/preview') && !state.dirty && !editor.open && ![...document.querySelectorAll('input[type=file]')].some(input => input.files.length) && ![...document.querySelectorAll('video,audio')].some(media => !media.paused)) load(true); }, 15000);
await load();
