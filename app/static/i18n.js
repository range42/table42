// Only explicitly marked interface messages are translated. Authored content is untouched.
const supported = ['en', 'fr', 'de'];
const storageKey = 'table42:language';
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const catalogues = Object.fromEntries(await Promise.all(supported.map(async language => {
  const response = await fetch(`/static/locales/${language}.json`);
  if (!response.ok) throw new Error(`Translation catalogue unavailable: ${language}`);
  return [language, await response.json()];
})));

function supportedLanguage(value) {
  const candidate = String(value || '').toLowerCase().split('-')[0];
  return supported.includes(candidate) ? candidate : null;
}

let stored;
try { stored = localStorage.getItem(storageKey); } catch { /* Browser preference still works. */ }
let language = supportedLanguage(stored) || (navigator.languages || [navigator.language])
  .map(supportedLanguage).find(Boolean) || 'en';

export const getLanguage = () => language;
export const number = value => new Intl.NumberFormat(language, { maximumSignificantDigits: 15 }).format(value);
export const formatDate = value => new Intl.DateTimeFormat(language, {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
}).format(new Date(value));

function parameter(value) {
  if (Array.isArray(value)) return value.map(parameter).join('\n');
  if (value && typeof value === 'object') {
    if (value.i18nKey) return t(value.i18nKey, value.values);
    if (value.date) return formatDate(value.date);
  }
  return typeof value === 'number' ? number(value) : String(value ?? '');
}

export function t(key, values = {}) {
  let pattern = catalogues[language][key] ?? catalogues.en[key] ?? key;
  if (typeof pattern === 'object') {
    pattern = pattern[new Intl.PluralRules(language).select(Number(values.count || 0))] || pattern.other;
  }
  return pattern.replace(/\{(\w+)\}/g, (_, name) => parameter(values[name]));
}

export function message(key, values = {}) {
  return `<span data-i18n="${escapeHtml(key)}" data-i18n-values="${escapeHtml(JSON.stringify(values))}">${escapeHtml(t(key, values))}</span>`;
}

export function attribute(name, key, values = {}) {
  return `${name}="${escapeHtml(t(key, values))}" data-i18n-${name}="${escapeHtml(key)}" data-i18n-${name}-values="${escapeHtml(JSON.stringify(values))}"`;
}

export function dateMessage(value) {
  return `<span data-i18n-date="${escapeHtml(value)}">${escapeHtml(formatDate(value))}</span>`;
}

export function bindMessage(element, key, values = {}) {
  if (!element) return;
  element.dataset.i18n = key;
  element.dataset.i18nValues = JSON.stringify(values);
  element.textContent = t(key, values);
}

export function languagePicker(id = 'interface-language') {
  return `<div class="language-picker"><label for="${id}">${message('common.language')}</label><select id="${id}" data-change="language" ${attribute('aria-label', 'common.language')}>
    ${[['en', 'English'], ['fr', 'Français'], ['de', 'Deutsch']].map(([code, label]) => `<option value="${code}" ${code === language ? 'selected' : ''} lang="${code}">${label}</option>`).join('')}</select></div>`;
}

export function updateBindings(root = document) {
  document.documentElement.lang = language;
  document.title = t('meta.title');
  document.querySelector('meta[name=description]')?.setAttribute('content', t('meta.description'));
  for (const element of root.querySelectorAll('[data-i18n]')) {
    element.textContent = t(element.dataset.i18n, JSON.parse(element.dataset.i18nValues || '{}'));
  }
  for (const name of ['aria-label', 'title', 'alt', 'placeholder']) {
    for (const element of root.querySelectorAll(`[data-i18n-${name}]`)) {
      element.setAttribute(name, t(element.getAttribute(`data-i18n-${name}`),
        JSON.parse(element.getAttribute(`data-i18n-${name}-values`) || '{}')));
    }
  }
  for (const element of root.querySelectorAll('[data-i18n-date]')) element.textContent = formatDate(element.dataset.i18nDate);
  for (const element of root.querySelectorAll('[data-i18n-language-link]')) {
    const url = new URL(element.getAttribute('href'), location.href);
    url.searchParams.set('lang', language);
    element.setAttribute('href', url.pathname + url.search);
  }
  for (const select of root.querySelectorAll('[data-change=language]')) select.value = language;
}

export function setLanguage(value) {
  const selected = supportedLanguage(value);
  if (!selected) return;
  language = selected;
  try { localStorage.setItem(storageKey, language); } catch { /* Keep switching usable without storage. */ }
  updateBindings();
}

// Adapt the existing English API errors without changing their public response format.
// New interface code uses semantic keys; this adapter is limited to errors and notices.
const legacyMessages = Object.entries(catalogues.en).filter(([key, value]) =>
  typeof value === 'string' && /^(errors|notice|confirm)\./.test(key)
).map(([key, value]) => {
  const names = [];
  const escaped = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = escaped.replace(/\\\{(\w+)\\\}/g, (_, name) => { names.push(name); return '([\\s\\S]*?)'; });
  return { key, names, pattern: new RegExp(`^${pattern}$`) };
});

export function legacyMessage(value) {
  if (catalogues.en[value]) return { key: value, values: {} };
  for (const entry of legacyMessages) {
    const match = String(value).match(entry.pattern);
    if (match) return { key: entry.key, values: Object.fromEntries(entry.names.map((name, index) => [name, match[index + 1]])) };
  }
  return null;
}

export function bindLegacyMessage(element, value) {
  const found = legacyMessage(value);
  if (found) bindMessage(element, found.key, found.values);
  else {
    delete element.dataset.i18n;
    delete element.dataset.i18nValues;
    element.textContent = value;
  }
}

export function validationError(details, path) {
  const fields = {
    name: path.includes('/groups') ? 'members.group_name' : path.startsWith('/users') || path === '/setup' ? 'auth.full_name' : 'exercise.name',
    username: 'auth.username', password: 'auth.password', description: 'exercise.description',
    label: 'question.text', role: 'members.role', group_id: 'common.group',
  };
  const errors = details.map(detail => {
    const field = detail.loc.at(-1);
    const context = detail.ctx || {};
    const kinds = { missing: 'validation.required', string_too_short: 'validation.minimum_length',
      string_too_long: 'validation.maximum_length', string_pattern_mismatch: 'validation.pattern',
      int_parsing: 'validation.number', float_parsing: 'validation.number', finite_number: 'validation.number' };
    const reason = legacyMessage(detail.msg.replace(/^Value error,\s*/, '')) || {
      key: kinds[detail.type] || 'validation.invalid', values: { count: context.min_length ?? context.max_length },
    };
    return { i18nKey: 'validation.field', values: {
      field: fields[field] ? { i18nKey: fields[field] } : String(field),
      reason: { i18nKey: reason.key, values: reason.values },
    } };
  });
  const error = new Error(t('validation.summary', { errors }));
  error.i18nKey = 'validation.summary';
  error.values = { errors };
  return error;
}

updateBindings();
