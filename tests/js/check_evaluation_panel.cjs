const assert = require('assert');
const panel = require('../../frontend/evaluation_panel.js');

const strict = panel.buildViewModel({
  status: 'completed',
  entity: {
    mode: 'strict', threshold: 0.9,
    overall: { precision: 0.95, recall: 0.9, f1: 0.9231, requirement_met: true },
    categories: {
      organization: {
        label: '机构', true_positive: 4, false_positive: 0, false_negative: 0,
        precision: 1, recall: 1, f1: 1,
      },
    },
  },
  public_links: { status: 'completed', accessibility_rate: 0.98, requirement_met: true },
  errors: [],
});
assert.deepStrictEqual(strict.cards.map(card => card.value), ['92.31%', '90.00%', '98.00%']);
assert.strictEqual(strict.cards[0].state, 'pass');
assert.strictEqual(strict.rows[0].label, '机构');

const proxy = panel.buildViewModel({
  status: 'completed',
  entity: { mode: 'proxy', overall: null, categories: {}, proxy_evidence_support_rate: 0.75 },
  public_links: { status: 'no_public_urls', accessibility_rate: null, requirement_met: null },
  errors: [],
});
assert.deepStrictEqual(proxy.cards.map(card => card.value), ['待标准答案', '待标准答案', '无公开链接']);
assert(proxy.message.includes('证据支撑率（代理指标）：75.00%'));
assert(!proxy.message.includes('准确率：75.00%'));

const invalid = panel.buildViewModel({
  status: 'failed',
  entity: { mode: 'invalid', status: 'invalid_ground_truth', overall: null, categories: {} },
  public_links: { status: 'evaluation_failed', accessibility_rate: null },
  errors: [{ scope: 'entity', message: '标准答案文件无法解析。' }],
});
assert.strictEqual(invalid.cards[0].value, '标准答案错误');
assert.strictEqual(invalid.cards[2].value, '测评未完成');
assert(invalid.message.includes('标准答案文件无法解析'));

const failed = panel.buildViewModel({
  status: 'partial',
  entity: { mode: 'proxy', status: 'evaluation_failed', overall: null, categories: {} },
  public_links: { status: 'completed', accessibility_rate: 0.5, requirement_met: false },
  errors: [{ scope: 'entity', message: '实体抽取测评未完成。' }],
});
assert.deepStrictEqual(failed.cards.map(card => card.value), ['测评未完成', '测评未完成', '50.00%']);
assert.strictEqual(failed.cards[2].state, 'fail');

assert.strictEqual(panel.buildViewModel(null).visible, false);
console.log('evaluation_panel_checks=passed');
