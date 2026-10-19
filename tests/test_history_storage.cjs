const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const history = require('../frontend/history-storage.js');
const KEY = 'conversationHistory';

function fixture(localEntries = null, cookieEntries = null) {
  let raw = localEntries === null ? null : JSON.stringify(localEntries);
  let cookie = cookieEntries === null ? '' : `${KEY}=${encodeURIComponent(JSON.stringify(cookieEntries))}`;
  return {
    storage: {
      getItem: () => raw,
      setItem: (key, value) => { assert.equal(key, KEY); raw = value; },
    },
    doc: {
      get cookie() { return cookie; },
      set cookie(value) { if (value.startsWith(`${KEY}=`)) cookie = ''; },
    },
    raw: () => raw,
    corrupt: value => { raw = value; },
  };
}

function entry(i) {
  return {
    prompt: '\u5546\u7528\u822a\u7a7a\u53d1\u52a8\u673a\u6280\u672f\u7814\u7a76'.repeat(8) + i,
    timestamp: new Date(2026, 0, 1, 0, i).toISOString(),
    links: { md: `/outputs/report_${i}.md`, docx: `/outputs/report_${i}.docx`, pdf: `/outputs/report_${i}.pdf` },
    searchScopes: ['papers', 'user'], taskTemplateId: 'technical',
  };
}

test('100 long Chinese titles survive save/reload without a 4KB or 20-item cap', () => {
  const f = fixture();
  const entries = Array.from({ length: 100 }, (_, i) => entry(i));
  assert.ok(encodeURIComponent(JSON.stringify(entries)).length > 4000);
  history.save(f.storage, f.doc, entries);
  assert.deepEqual(history.load(f.storage, f.doc).entries, entries);
});

test('migration merges cookie and local history without duplicating identical tasks', () => {
  const f = fixture([entry(0), entry(1)], [entry(1), entry(2)]);
  assert.equal(history.load(f.storage, f.doc).entries.length, 3);
  assert.equal(f.doc.cookie, '');
  assert.equal(history.load(f.storage, f.doc).entries.length, 3);
});

test('same task title on different runs is retained', () => {
  const second = { ...entry(1), prompt: entry(0).prompt };
  const f = fixture([entry(0)], [second]);
  assert.equal(history.load(f.storage, f.doc).entries.length, 2);
});

test('quota failure preserves local data and the legacy cookie', () => {
  const f = fixture([entry(0)], [entry(1)]);
  const original = f.raw();
  f.storage.setItem = () => { throw new Error('QuotaExceededError'); };
  const result = history.load(f.storage, f.doc);
  assert.equal(result.entries.length, 2);
  assert.ok(result.warning);
  assert.equal(f.raw(), original);
  assert.ok(f.doc.cookie);
  assert.throws(() => history.save(f.storage, f.doc, [entry(2)]), /Quota/);
  assert.equal(f.raw(), original);
});

test('malformed local data is not deleted or overwritten', () => {
  for (const raw of ['{broken', '{}', '[null]']) {
    const f = fixture(null, [entry(1)]);
    f.corrupt(raw);
    assert.throws(() => history.load(f.storage, f.doc));
    assert.throws(() => history.save(f.storage, f.doc, []));
    assert.equal(f.raw(), raw);
    assert.ok(f.doc.cookie);
  }
});

test('delete and clear survive reload without resurrecting migrated tasks', () => {
  const f = fixture(null, [entry(0), entry(1)]);
  const entries = history.load(f.storage, f.doc).entries;
  history.save(f.storage, f.doc, entries.slice(1));
  assert.deepEqual(history.load(f.storage, f.doc).entries, [entry(1)]);
  history.save(f.storage, f.doc, []);
  assert.deepEqual(history.load(f.storage, f.doc).entries, []);
});

test('report bodies are not stored, but links and task settings are', () => {
  const f = fixture();
  history.save(f.storage, f.doc, [{ ...entry(0), report: 'large report body' }]);
  assert.deepEqual(history.load(f.storage, f.doc).entries, [entry(0)]);
});

test('cookies can be unavailable while local history still works', () => {
  const f = fixture([entry(0)]);
  const doc = { get cookie() { throw new Error('blocked'); }, set cookie(_) { throw new Error('blocked'); } };
  assert.equal(history.load(f.storage, doc).entries.length, 1);
  history.save(f.storage, doc, [entry(1)]);
  assert.equal(history.load(f.storage, doc).entries[0].timestamp, entry(1).timestamp);
});

const script = fs.readFileSync('frontend/scripts.js', 'utf8');
function ui(f) {
  const messages = [];
  const context = {
    ReportHistoryStorage: history, localStorage: f.storage, document: f.doc,
    console: { error() {}, warn() {} }, confirm: () => true,
    showToast: message => messages.push(message), renderHistoryEntries() {},
  };
  vm.createContext(context);
  const start = script.indexOf('  const loadConversationHistory =');
  const end = script.indexOf('  // Filter history entries', start);
  vm.runInContext(`let conversationHistory = []; let hasUnsavedHistory = false;\n${script.slice(start, end)}\nthis.api = {
    load: loadConversationHistory, save: saveConversationHistory,
    remove: deleteHistoryEntry, clear: clearConversationHistory,
    get: () => conversationHistory, set: value => { conversationHistory = value; },
    markUnsaved: () => { hasUnsavedHistory = true; }
  };`, context);
  return { api: context.api, messages };
}

test('UI persists and reloads 50 entries', () => {
  const f = fixture();
  const { api } = ui(f);
  api.set(Array.from({ length: 50 }, (_, i) => entry(i)));
  assert.equal(api.save(), true);
  api.set([]);
  api.load();
  assert.equal(api.get().length, 50);
  api.remove(0);
  api.load();
  assert.equal(api.get().length, 49);
  api.clear();
  api.load();
  assert.equal(api.get().length, 0);
});

test('UI rolls back failed deletion/clear and never reports success', () => {
  const f = fixture([entry(0), entry(1)]);
  const { api, messages } = ui(f);
  api.load();
  f.storage.setItem = () => { throw new Error('QuotaExceededError'); };
  api.remove(0);
  assert.equal(api.get().length, 2);
  api.clear();
  assert.equal(api.get().length, 2);
  assert.equal(messages.length, 2);
  assert.ok(messages.every(message => !message.includes('successfully')));
});

test('storage script loads before the app; legacy truncation is gone', () => {
  const html = fs.readFileSync('frontend/index.html', 'utf8');
  assert.ok(html.indexOf('/site/history-storage.js?') < html.indexOf('/site/scripts.js?'));
  assert.ok(!script.includes('MAX_COOKIE_SIZE'));
  assert.ok(!script.includes('storageHistory.slice(0, 20)'));
  new vm.Script(script);
});

test('opening history after a failed new-task save keeps the unsaved task exportable', () => {
  const f = fixture([entry(0)]);
  const { api } = ui(f);
  api.set([entry(1), entry(0)]);
  api.markUnsaved();
  api.load();
  assert.equal(api.get().length, 2);
  api.save();
  api.load();
  assert.equal(api.get().length, 2);
});

test('import persists more than 20 entries and rolls back when storage is full', () => {
  const f = fixture();
  const messages = [];
  const context = {
    ReportHistoryStorage: history, localStorage: f.storage, document: f.doc,
    console: { error() {}, warn() {}, debug() {} }, confirm: () => true,
    showToast: message => messages.push(message), renderHistoryEntries() {},
    FileReader: class { readAsText(file) { this.onload({ target: { result: file.content } }); } },
  };
  vm.createContext(context);
  const saveStart = script.indexOf('  const saveConversationHistory =');
  const saveEnd = script.indexOf('  // Delete a history entry', saveStart);
  const importStart = script.indexOf('  const handleFileImport =');
  const importEnd = script.indexOf('\n  }', importStart) + 4;
  vm.runInContext(`let conversationHistory = []; let hasUnsavedHistory = false;
    ${script.slice(saveStart, saveEnd)}
    ${script.slice(importStart, importEnd)}
    this.importFile = handleFileImport;
    this.count = () => conversationHistory.length;`, context);
  const imported = Array.from({ length: 40 }, (_, i) => entry(i));
  context.importFile({ target: { files: [{ content: JSON.stringify(imported) }], value: 'file' } });
  assert.equal(context.count(), 40);
  assert.equal(history.load(f.storage, f.doc).entries.length, 40);
  const stored = f.raw();
  f.storage.setItem = () => { throw new Error('QuotaExceededError'); };
  messages.length = 0;
  context.importFile({ target: { files: [{ content: JSON.stringify([entry(99)]) }], value: 'file' } });
  assert.equal(context.count(), 40);
  assert.equal(f.raw(), stored);
  assert.ok(messages.every(message => !message.includes('Successfully imported')));
});
