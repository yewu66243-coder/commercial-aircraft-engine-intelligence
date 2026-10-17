(function (root) {
  'use strict';

  const KEY = 'conversationHistory';

  function parse(raw) {
    if (raw === null || raw === '') return [];
    const entries = JSON.parse(raw);
    if (!Array.isArray(entries) || entries.some(entry => !entry || typeof entry !== 'object' || Array.isArray(entry))) {
      throw new Error('Invalid history data; original storage has been preserved');
    }
    return entries;
  }

  function readCookie(doc) {
    const prefix = KEY + '=';
    const cookie = doc.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(prefix));
    return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
  }

  function expireCookie(doc) {
    doc.cookie = KEY + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  }

  function metadata(entry) {
    const result = {
      prompt: entry.prompt || entry.task || '',
      links: entry.links || entry.downloadLinks || {},
      timestamp: entry.timestamp || new Date().toISOString()
    };
    for (const key of ['reportSource', 'searchScopes', 'queryDomains', 'demandModelId',
      'taskTemplateId', 'sourceTemplateIds', 'sourceCategories']) {
      if (entry[key] !== undefined) result[key] = entry[key];
    }
    return result;
  }

  function identity(entry) {
    const links = entry.links || entry.downloadLinks || {};
    return JSON.stringify([entry.prompt || entry.task || '', entry.timestamp || '',
      Object.keys(links).sort().map(key => [key, links[key]])]);
  }

  function save(storage, doc, entries) {
    // Refuse to overwrite malformed data, and let quota/security errors reach the UI.
    parse(storage.getItem(KEY));
    storage.setItem(KEY, JSON.stringify(entries.map(metadata)));
    // Cookie access can be blocked independently of localStorage.
    try { expireCookie(doc); } catch (_) { /* Local history is already saved. */ }
  }

  function load(storage, doc) {
    const entries = parse(storage.getItem(KEY));
    let legacy;
    try { legacy = readCookie(doc); } catch (warning) { return { entries, warning }; }
    if (!legacy) return { entries };
    let oldEntries;
    try { oldEntries = parse(legacy); } catch (warning) { return { entries, warning }; }
    const seen = new Set(entries.map(identity));
    for (const entry of oldEntries) {
      const key = identity(entry);
      if (!seen.has(key)) {
        entries.push(entry);
        seen.add(key);
      }
    }
    try { save(storage, doc, entries); } catch (warning) { return { entries, warning }; }
    return { entries };
  }

  const api = { load, save };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ReportHistoryStorage = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
