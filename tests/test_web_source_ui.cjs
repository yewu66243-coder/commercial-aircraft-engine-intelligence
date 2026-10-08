const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.textContent = ''; }
  replaceChildren() { this.children = []; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
}
const ids = Object.fromEntries(['webSourceTracking', 'webSourceRows', 'webSourceFilter', 'webSourceSummary'].map(id => [id, new Element()]));
const source = fs.readFileSync('frontend/scripts.js', 'utf8');
const start = source.indexOf('  const renderWebSourceTracking =');
const end = source.indexOf('  const renderSelectedSources =', start);
assert.ok(start >= 0 && end > start);
const context = { URL, document: { getElementById: id => ids[id], createElement: () => new Element() } };
vm.createContext(context);
vm.runInContext(source.slice(start, end) + '\nthis.render = renderWebSourceTracking;', context);
context.render({summary:{searched:3, fetched:2, cited:1}, sources:[
  {url:'https://example.com/article', title:'正常原文', raw_characters:300, evidence_eligible:true, writing_selected:true, cited:true, reason:'已引用'},
  {url:'https://example.com/challenge', title:'验证页面', raw_characters:200, evidence_eligible:false, cited:false, reason:'验证页面'},
  {url:'javascript:alert(1)', title:'<img src=x onerror=alert(1)>', raw_characters:0, cited:false, reason:'失败'},
]});
assert.equal(ids.webSourceTracking.hidden, false);
assert.equal(ids.webSourceRows.children.length, 3);
assert.equal(ids.webSourceRows.children[2].children[0].href, undefined);
assert.equal(ids.webSourceRows.children[2].children[0].textContent, '<img src=x onerror=alert(1)>');
for (const [filter, expected] of [['cited',1],['uncited',2],['failed',1],['excluded',1]]) {
  ids.webSourceFilter.value = filter;
  ids.webSourceFilter.onchange();
  assert.equal(ids.webSourceRows.children.length, expected);
}
context.render(null);
assert.equal(ids.webSourceTracking.hidden, true);
assert.equal(ids.webSourceRows.children.length, 0);
assert.equal(ids.webSourceFilter.onchange, null);
console.log('PASS: rendering, filters, safe source links, and clearing stale results');
