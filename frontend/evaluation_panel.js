(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EvaluationPanel = api;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  const categoryOrder = ['organization', 'model', 'material', 'parameter', 'time', 'other'];
  const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
  const requirementState = met => met === true ? 'pass' : met === false ? 'fail' : 'neutral';

  function buildViewModel(summary) {
    const entity = summary?.entity || {};
    const links = summary?.public_links || {};
    const entityFailed = entity.status === 'evaluation_failed';
    const invalid = entity.mode === 'invalid' || entity.status === 'invalid_ground_truth';
    const strict = entity.mode === 'strict' && entity.overall && !entityFailed;
    const entityValue = metric => {
      if (invalid) return '标准答案错误';
      if (entityFailed) return '测评未完成';
      if (strict) return percent(entity.overall[metric]);
      return '待标准答案';
    };
    const entityState = invalid || entityFailed
      ? 'fail'
      : strict
        ? requirementState(entity.overall.requirement_met)
        : 'neutral';
    const linkValue = links.status === 'no_public_urls'
      ? '无公开链接'
      : links.status === 'evaluation_failed'
        ? '测评未完成'
        : percent(links.accessibility_rate);
    const linkState = links.status === 'completed'
      ? requirementState(links.requirement_met)
      : links.status === 'evaluation_failed'
        ? 'fail'
        : 'neutral';
    const entityF1Status = invalid
      ? '需修正标准答案'
      : entityFailed
        ? '需处理'
        : strict
          ? entity.overall.requirement_met === true
            ? '达标'
            : entity.overall.requirement_met === false
              ? '未达标'
              : '无可计算样本'
          : '待严格测评';
    const entityRecallStatus = invalid
      ? '需修正标准答案'
      : entityFailed
        ? '需处理'
        : strict
          ? '参考值'
          : '待严格测评';
    const linkStatus = links.status === 'no_public_urls'
      ? '不适用'
      : links.status === 'evaluation_failed'
        ? '需处理'
        : links.requirement_met === true
          ? '达标'
          : links.requirement_met === false
            ? '未达标'
            : '无可计算样本';

    const cards = [
      {
        id: 'evaluationEntityF1', statusId: 'evaluationEntityF1Status',
        value: entityValue('f1'), state: entityState, statusText: entityF1Status,
      },
      {
        id: 'evaluationEntityRecall', statusId: 'evaluationEntityRecallStatus',
        value: entityValue('recall'), state: invalid || entityFailed ? 'fail' : 'neutral',
        statusText: entityRecallStatus,
      },
      {
        id: 'evaluationLinkAccessibility', statusId: 'evaluationLinkAccessibilityStatus',
        value: linkValue, state: linkState, statusText: linkStatus,
      },
    ];
    const categories = entity.categories || {};
    const rows = categoryOrder
      .filter(key => categories[key])
      .map(key => ({ key, ...categories[key] }));
    const messages = (summary?.errors || [])
      .map(item => item?.message)
      .filter(Boolean);
    if (!strict && !invalid && !entityFailed && Number.isFinite(entity.proxy_evidence_support_rate)) {
      messages.unshift(
        `证据支撑率（代理指标）：${percent(entity.proxy_evidence_support_rate)}；该指标不能替代实体准确率。`
      );
    }
    return {
      cards,
      rows,
      message: messages.join(' '),
      visible: Boolean(summary),
    };
  }

  function render(summary, doc) {
    const targetDocument = doc || (typeof document !== 'undefined' ? document : null);
    const model = buildViewModel(summary);
    if (!targetDocument) return model;
    const panel = targetDocument.getElementById('evaluationPanel');
    if (!panel) return model;
    panel.hidden = !model.visible;
    model.cards.forEach(card => {
      const value = targetDocument.getElementById(card.id);
      if (!value) return;
      value.textContent = card.value;
      if (value.parentElement) value.parentElement.dataset.state = card.state;
      const status = targetDocument.getElementById(card.statusId);
      if (status) status.textContent = card.statusText;
    });
    const body = targetDocument.getElementById('evaluationCategoryBody');
    if (body) {
      body.replaceChildren();
      model.rows.forEach(row => {
        const tr = targetDocument.createElement('tr');
        [
          row.label,
          row.true_positive,
          row.false_positive,
          row.false_negative,
          percent(row.precision),
          percent(row.recall),
          percent(row.f1),
        ].forEach(cellValue => {
          const td = targetDocument.createElement('td');
          td.textContent = cellValue ?? '—';
          tr.appendChild(td);
        });
        body.appendChild(tr);
      });
    }
    const details = panel.querySelector('.evaluation-details');
    if (details) details.hidden = model.rows.length === 0;
    const message = targetDocument.getElementById('evaluationMessage');
    if (message) {
      message.textContent = model.message;
      message.hidden = !model.message;
    }
    return model;
  }

  function reset(doc) {
    const targetDocument = doc || (typeof document !== 'undefined' ? document : null);
    if (!targetDocument) return;
    const panel = targetDocument.getElementById('evaluationPanel');
    if (panel) panel.hidden = true;
    ['evaluationEntityF1', 'evaluationEntityRecall', 'evaluationLinkAccessibility'].forEach(id => {
      const value = targetDocument.getElementById(id);
      if (value) value.textContent = '—';
      if (value?.parentElement) value.parentElement.dataset.state = 'neutral';
    });
    [
      'evaluationEntityF1Status',
      'evaluationEntityRecallStatus',
      'evaluationLinkAccessibilityStatus',
    ].forEach(id => {
      const status = targetDocument.getElementById(id);
      if (status) status.textContent = '待测评';
    });
    const body = targetDocument.getElementById('evaluationCategoryBody');
    if (body) body.replaceChildren();
    const message = targetDocument.getElementById('evaluationMessage');
    if (message) {
      message.textContent = '';
      message.hidden = true;
    }
  }

  return { buildViewModel, render, reset };
});
