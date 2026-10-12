const memory = new Map();

function keyFor(user, exercise, inject) {
  return `table42:draft:${user.id}:${user.username}:${exercise.id}:${exercise.created_at}:${inject.id}:${inject.created_at}`;
}

export function getDraft(user, exercise, inject) {
  const key = keyFor(user, exercise, inject);
  if (memory.has(key)) return memory.get(key);
  try {
    const value = JSON.parse(localStorage.getItem(key));
    if (value && Number.isInteger(value.revision) && value.revision >= 0 && value.answers &&
        typeof value.answers === 'object' && !Array.isArray(value.answers)) {
      const draft = { ...value, persisted: true };
      memory.set(key, draft);
      return draft;
    }
  } catch { /* Keep the form usable when browser storage is unavailable. */ }
  return null;
}

export function saveDraft(user, exercise, inject, answers, revision) {
  const key = keyFor(user, exercise, inject);
  const draft = { answers, revision, savedAt: new Date().toISOString() };
  let persisted = false;
  try {
    localStorage.setItem(key, JSON.stringify(draft));
    persisted = true;
  } catch { /* An in-memory copy still protects navigation within this tab. */ }
  memory.set(key, { ...draft, persisted });
  return persisted;
}

export function clearDraft(user, exercise, inject) {
  const key = keyFor(user, exercise, inject);
  memory.set(key, null);
  try { localStorage.removeItem(key); } catch { /* A failed removal cannot overwrite a saved reply. */ }
}

function filled(question, value) {
  if (value == null) return false;
  if (question.type === 'boolean') return typeof value === 'boolean';
  if (question.type === 'number') return typeof value === 'number' && Number.isFinite(value);
  if (question.type === 'matrix') return question.rows.every(row =>
    question.columns.every(column => typeof value[row]?.[column] === 'string' && value[row][column].trim())
  );
  if (question.type === 'ranking') return Array.isArray(value.order) &&
    value.order.length === question.options.length && new Set(value.order).size === question.options.length &&
    value.order.every(item => question.options.includes(item)) && typeof value.explanation === 'string' &&
    Boolean(value.explanation.trim());
  if (['list', 'multiple_choice'].includes(question.type)) return Array.isArray(value) &&
    value.length > 0 && value.every(item => typeof item === 'string' && item.trim());
  return typeof value === 'string' && Boolean(value.trim());
}

export function filledCount(questions, answers) {
  return questions.filter(question => filled(question, answers[question.id])).length;
}
