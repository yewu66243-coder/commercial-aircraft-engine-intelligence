const assert = require('assert');
const panel = require('../../frontend/evaluation_panel.js');

const cardValues = model => model.cards.map(card => card.value);
const cardStatus = model => model.cards.map(card => card.statusText);

const strictSummary = {
  status: 'completed',
  entity: {
    mode: 'strict', threshold: 0.9,
    overall: { precision: 0.9474, recall: 0.9, f1: 0.9231, requirement_met: true },
    categories: {
      organization: {
        label: '机构', true_positive: 4, false_positive: 0, false_negative: 0,
        precision: 1, recall: 1, f1: 1,
      },
    },
  },
  public_links: {
    status: 'completed',
    accessibility: {
      status: 'completed', threshold: 0.98, total_count: 72, accessible_count: 71,
      rate: 0.9861, requirement_met: true,
    },
    claim_support: {
      status: 'completed', threshold: 0.9, relationship_count: 40, supported_count: 37,
      partially_supported_count: 1, unsupported_count: 1, unchecked_count: 1,
      accuracy: 0.925, requirement_met: true,
    },
  },
  evaluation_report_paths: {
    markdown: '/outputs/evaluations/a.md',
    word: '/outputs/evaluations/a.docx',
    pdf: '/outputs/evaluations/a.pdf',
  },
  errors: [],
};

const strict = panel.buildViewModel(strictSummary, { canRerun: true });
assert.deepStrictEqual(strict.cards.map(card => card.id), [
  'evaluationEntityPrecision',
  'evaluationEntityRecallF1',
  'evaluationLinkAccessibility',
  'evaluationClaimSupport',
]);
assert.deepStrictEqual(cardValues(strict), ['94.74%', 'Recall 90.00% · F1 92.31%', '98.61%', '92.50%']);
assert.deepStrictEqual(cardStatus(strict), ['达标', '达标', '达标', '达标']);
assert.strictEqual(strict.cards[0].state, 'pass');
assert.strictEqual(strict.rows[0].label, '机构');
assert.strictEqual(strict.canRerun, true);
assert.strictEqual(strict.downloads.word, '/outputs/evaluations/a.docx');
assert.strictEqual(strict.downloads.pdf, '/outputs/evaluations/a.pdf');

const failedStrict = panel.buildViewModel({
  status: 'completed',
  entity: {
    mode: 'strict', status: 'completed',
    overall: { precision: 0.5, recall: 0.4, f1: 0.4444, requirement_met: false },
    categories: {},
  },
  public_links: {
    accessibility: { status: 'completed', rate: 0.5, requirement_met: false },
    claim_support: { status: 'completed', accuracy: 0.81, requirement_met: false },
  },
  errors: [],
});
assert.deepStrictEqual(cardStatus(failedStrict), ['未达标', '未达标', '未达标', '未达标']);
assert.deepStrictEqual(failedStrict.cards.map(card => card.state), ['fail', 'fail', 'fail', 'fail']);

const proxy = panel.buildViewModel({
  status: 'completed',
  entity: { mode: 'proxy', status: 'proxy', overall: null, categories: {}, proxy_evidence_support_rate: 0.75 },
  public_links: {
    status: 'no_public_urls',
    accessibility: { status: 'no_public_urls', rate: null, requirement_met: null },
    claim_support: { status: 'no_public_relationships', accuracy: null, requirement_met: null },
  },
  errors: [],
});
assert.deepStrictEqual(cardValues(proxy), ['待标准答案', '待标准答案', '无公开链接', '无公开链接']);
assert.deepStrictEqual(cardStatus(proxy), ['待标准答案', '待标准答案', '不适用', '不适用']);
assert.deepStrictEqual(proxy.cards.map(card => card.state), ['neutral', 'neutral', 'neutral', 'neutral']);
assert(proxy.message.includes('证据支撑率（代理指标）：75.00%'));
assert(!proxy.message.includes('准确率：75.00%'));

const invalid = panel.buildViewModel({
  status: 'failed',
  entity: { mode: 'invalid', status: 'invalid_ground_truth', overall: null, categories: {} },
  public_links: {
    accessibility: { status: 'evaluation_failed', rate: null },
    claim_support: { status: 'evaluation_failed', accuracy: null },
  },
  errors: [{ scope: 'entity', message: '标准答案文件无法解析。' }],
});
assert.deepStrictEqual(cardValues(invalid), ['标准答案错误', '标准答案错误', '测评未完成', '测评未完成']);
assert.deepStrictEqual(cardStatus(invalid), ['需修正标准答案', '需修正标准答案', '需处理', '需处理']);
assert(invalid.message.includes('标准答案文件无法解析'));

const failed = panel.buildViewModel({
  status: 'partial',
  entity: { mode: 'proxy', status: 'evaluation_failed', overall: null, categories: {} },
  public_links: {
    accessibility: { status: 'completed', rate: 0.5, requirement_met: false },
    claim_support: { status: 'completed', accuracy: 0.95, requirement_met: true },
  },
  errors: [{ scope: 'entity', message: '实体抽取测评未完成。' }],
});
assert.deepStrictEqual(cardValues(failed), ['测评未完成', '测评未完成', '50.00%', '95.00%']);
assert.deepStrictEqual(cardStatus(failed), ['需处理', '需处理', '未达标', '达标']);

const emptyStrict = panel.buildViewModel({
  status: 'completed',
  entity: {
    mode: 'strict', status: 'completed', categories: {},
    overall: { precision: null, recall: null, f1: null, requirement_met: null },
  },
  public_links: {
    accessibility: { status: 'completed', rate: null, requirement_met: null },
    claim_support: { status: 'completed', accuracy: null, requirement_met: null },
  },
  errors: [],
});
assert.deepStrictEqual(cardValues(emptyStrict), ['—', 'Recall — · F1 —', '—', '—']);
assert.deepStrictEqual(cardStatus(emptyStrict), ['无可计算样本', '无可计算样本', '无可计算样本', '无可计算样本']);
assert.deepStrictEqual(emptyStrict.cards.map(card => card.state), ['neutral', 'neutral', 'neutral', 'neutral']);

// Legacy flat summaries must still render the accessibility card.
const legacy = panel.buildViewModel({
  status: 'completed',
  entity: { mode: 'proxy', overall: null, categories: {} },
  public_links: { status: 'completed', accessibility_rate: 0.98, requirement_met: true },
  errors: [],
});
assert.strictEqual(cardValues(legacy)[2], '98.00%');
assert.strictEqual(cardStatus(legacy)[2], '达标');

const withTruth = panel.buildViewModel(
  strictSummary,
  { canRerun: true, groundTruth: { name: 'truth.json', entityCount: 12 }, actionStatus: '已替换标准答案。' }
);
assert(withTruth.groundTruthText.includes('truth.json'));
assert(withTruth.groundTruthText.includes('12'));
assert.strictEqual(withTruth.actionStatus, '已替换标准答案。');
assert.strictEqual(
  panel.buildViewModel(strictSummary).groundTruthText.includes('未上传'),
  true
);
assert.strictEqual(panel.buildViewModel(null).visible, false);

function element() {
  return {
    textContent: '', hidden: false, dataset: {}, children: [], parentElement: null,
    attributes: {}, onclick: null, disabled: false, href: '#', value: '',
    replaceChildren() { this.children = []; },
    appendChild(child) { this.children.push(child); },
    querySelector() { return this.details || null; },
    classList: {
      store: new Set(),
      add(name) { this.store.add(name); },
      remove(name) { this.store.delete(name); },
      contains(name) { return this.store.has(name); },
    },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
  };
}
const ids = {};
[
  'evaluationPanel',
  ...panel.CARD_IDS,
  'evaluationEntityPrecisionStatus', 'evaluationEntityRecallF1Status',
  'evaluationLinkAccessibilityStatus', 'evaluationClaimSupportStatus',
  'evaluationCategoryBody', 'evaluationMessage', 'evaluationDownloadWord',
  'evaluationDownloadPdf', 'evaluationRerunButton', 'evaluationGroundTruthInput',
  'evaluationGroundTruthStatus', 'evaluationActionStatus',
].forEach(id => { ids[id] = element(); });
ids.evaluationPanel.details = element();
panel.CARD_IDS.forEach(id => { ids[id].parentElement = element(); });
const fakeDocument = {
  getElementById(id) { return ids[id] || null; },
  createElement() { return element(); },
};

panel.render(strictSummary, fakeDocument, { canRerun: true, groundTruth: { name: 'gt.json', entityCount: 3 } });
assert.strictEqual(ids.evaluationEntityPrecisionStatus.textContent, '达标');
assert.strictEqual(ids.evaluationClaimSupportStatus.textContent, '达标');
assert.strictEqual(ids.evaluationDownloadWord.href, '/outputs/evaluations/a.docx');
assert.strictEqual(ids.evaluationDownloadWord.attributes.download, '');
assert.strictEqual(ids.evaluationDownloadPdf.attributes['aria-disabled'], 'false');
assert.strictEqual(ids.evaluationRerunButton.disabled, false);
assert(ids.evaluationGroundTruthStatus.textContent.includes('gt.json'));
assert.strictEqual(ids.evaluationCategoryBody.children.length, 1);

panel.reset(fakeDocument);
assert.strictEqual(ids.evaluationEntityPrecision.textContent, '—');
assert.strictEqual(ids.evaluationEntityPrecisionStatus.textContent, '待测评');
assert.strictEqual(ids.evaluationClaimSupport.textContent, '—');
assert.strictEqual(ids.evaluationPanel.hidden, true);
assert.strictEqual(ids.evaluationDownloadWord.href, '#');
assert.strictEqual(ids.evaluationDownloadWord.attributes.download, undefined);
assert.strictEqual(ids.evaluationRerunButton.disabled, true);
assert.strictEqual(ids.evaluationCategoryBody.children.length, 0);
assert(ids.evaluationGroundTruthStatus.textContent.includes('未上传'));
assert.strictEqual(ids.evaluationActionStatus.textContent, '');

console.log('evaluation_panel_checks=passed');
