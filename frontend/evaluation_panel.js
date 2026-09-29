(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EvaluationPanel = api;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  const categoryOrder = ['organization', 'model', 'material', 'parameter', 'time', 'other'];
  const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
  const requirementState = met => met === true ? 'pass' : met === false ? 'fail' : 'neutral';

  const CARD_IDS = [
    'evaluationEntityPrecision',
    'evaluationEntityRecallF1',
    'evaluationLinkAccessibility',
    'evaluationClaimSupport',
  ];
  const STATUS_IDS = CARD_IDS.map(id => `${id}Status`);

  function linkValue(source, rateKey) {
    const status = source?.status;
    if (status === 'no_public_urls' || status === 'no_public_relationships') return '无公开链接';
    if (status === 'evaluation_failed') return '测评未完成';
    if (status === 'invalid_ground_truth') return '标准答案错误';
    return percent(source?.[rateKey]);
  }

  function linkState(source) {
    const status = source?.status;
    if (status === 'evaluation_failed') return 'fail';
    return requirementState(source?.requirement_met);
  }

  function linkStatusText(source) {
    const status = source?.status;
    if (status === 'no_public_urls' || status === 'no_public_relationships') return '不适用';
    if (status === 'evaluation_failed') return '需处理';
    if (source?.requirement_met === true) return '达标';
    if (source?.requirement_met === false) return '未达标';
    return '无可计算样本';
  }

  function buildViewModel(summary, context) {
    const options = context || {};
    const entity = summary?.entity || {};
    const links = summary?.public_links || {};
    // Nested structure is authoritative; flat aliases stay for older records.
    const accessibility = links.accessibility || { rate: links.accessibility_rate, requirement_met: links.requirement_met, status: links.status };
    const support = links.claim_support || {};
    const paths = summary?.evaluation_report_paths || {};

    const invalid = entity.mode === 'invalid' || entity.status === 'invalid_ground_truth';
    const failed = entity.status === 'evaluation_failed';
    const strict = entity.mode === 'strict' && Boolean(entity.overall) && !invalid && !failed;
    const overall = entity.overall || {};

    const entityValue = metric => {
      if (invalid) return '标准答案错误';
      if (failed) return '测评未完成';
      if (!strict) return '待标准答案';
      return percent(overall[metric]);
    };
    const recallF1Value = () => {
      if (!strict) return entityValue('f1');
      return `Recall ${percent(overall.recall)} · F1 ${percent(overall.f1)}`;
    };
    const entityState = invalid || failed ? 'fail' : strict ? requirementState(overall.requirement_met) : 'neutral';
    const entityStatusText = () => {
      if (invalid) return '需修正标准答案';
      if (failed) return '需处理';
      if (!strict) return '待标准答案';
      if (overall.requirement_met === true) return '达标';
      if (overall.requirement_met === false) return '未达标';
      return '无可计算样本';
    };

    const cards = [
      {
        id: 'evaluationEntityPrecision', statusId: 'evaluationEntityPrecisionStatus',
        value: entityValue('precision'), state: entityState, statusText: entityStatusText(),
      },
      {
        id: 'evaluationEntityRecallF1', statusId: 'evaluationEntityRecallF1Status',
        value: recallF1Value(), state: entityState, statusText: entityStatusText(),
      },
      {
        id: 'evaluationLinkAccessibility', statusId: 'evaluationLinkAccessibilityStatus',
        value: linkValue(accessibility, 'rate'), state: linkState(accessibility),
        statusText: linkStatusText(accessibility),
      },
      {
        id: 'evaluationClaimSupport', statusId: 'evaluationClaimSupportStatus',
        value: linkValue(support, 'accuracy'), state: linkState(support),
        statusText: linkStatusText(support),
      },
    ];

    const categories = entity.categories || {};
    const rows = categoryOrder
      .filter(key => categories[key])
      .map(key => ({ key, ...categories[key] }));

    const messages = (summary?.errors || []).map(item => item?.message).filter(Boolean);
    if (!strict && !invalid && !failed && Number.isFinite(entity.proxy_evidence_support_rate)) {
      messages.unshift(
        `证据支撑率（代理指标）：${percent(entity.proxy_evidence_support_rate)}；该指标不能替代实体准确率。`
      );
    }

    const groundTruth = options.groundTruth && typeof options.groundTruth === 'object'
      ? options.groundTruth
      : null;
    const downloads = {
      word: safeHref(paths.word),
      pdf: safeHref(paths.pdf),
      markdown: safeHref(paths.markdown),
    };

    return {
      cards,
      rows,
      message: messages.join(' '),
      downloads,
      groundTruth,
      canRerun: Boolean(options.canRerun),
      actionStatus: typeof options.actionStatus === 'string' ? options.actionStatus : '',
      groundTruthText: groundTruth?.name
        ? `当前标准答案：${groundTruth.name}（${groundTruth.entityCount ?? 0} 个实体）`
        : '当前标准答案：未上传（实体严格指标暂不可用）',
      visible: Boolean(summary),
    };
  }

  // 只接受服务端归一化后的静态测评报告路径，杜绝 javascript: 等外部注入
  const SAFE_REPORT_HREF = /^\/outputs\/evaluations\/[^/?#\\]+\.(md|docx|pdf)$/i;
  const safeHref = href => (typeof href === 'string' && SAFE_REPORT_HREF.test(href)) ? href : '';

  function applyDownload(element, href, name) {
    if (!element) return;
    const safe = safeHref(href);
    if (safe) {
      element.href = safe;
      element.setAttribute('download', name || '');
      element.classList.remove('disabled');
      element.setAttribute('aria-disabled', 'false');
      element.removeAttribute('tabindex');
      element.onclick = null;
    } else {
      element.href = '#';
      element.classList.add('disabled');
      element.setAttribute('aria-disabled', 'true');
      element.setAttribute('tabindex', '-1');
      element.removeAttribute('download');
      element.onclick = event => event.preventDefault();
    }
  }

  function render(summary, doc, context) {
    const targetDocument = doc && doc.getElementById ? doc : (typeof document !== 'undefined' ? document : null);
    const model = buildViewModel(summary, context);
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

    applyDownload(targetDocument.getElementById('evaluationDownloadWord'), model.downloads.word, '');
    applyDownload(targetDocument.getElementById('evaluationDownloadPdf'), model.downloads.pdf, '');

    const rerun = targetDocument.getElementById('evaluationRerunButton');
    if (rerun) rerun.disabled = !model.canRerun;

    const groundTruthStatus = targetDocument.getElementById('evaluationGroundTruthStatus');
    if (groundTruthStatus) groundTruthStatus.textContent = model.groundTruthText;

    const actionStatus = targetDocument.getElementById('evaluationActionStatus');
    if (actionStatus) actionStatus.textContent = model.actionStatus;

    return model;
  }

  function reset(doc) {
    const targetDocument = doc && doc.getElementById ? doc : (typeof document !== 'undefined' ? document : null);
    if (!targetDocument) return;
    const panel = targetDocument.getElementById('evaluationPanel');
    if (panel) panel.hidden = true;

    CARD_IDS.forEach(id => {
      const value = targetDocument.getElementById(id);
      if (value) value.textContent = '—';
      if (value && value.parentElement) value.parentElement.dataset.state = 'neutral';
    });
    STATUS_IDS.forEach(id => {
      const status = targetDocument.getElementById(id);
      if (status) status.textContent = '待测评';
    });

    const body = targetDocument.getElementById('evaluationCategoryBody');
    if (body) body.replaceChildren();
    const details = panel && panel.querySelector ? panel.querySelector('.evaluation-details') : null;
    if (details) details.hidden = true;

    const message = targetDocument.getElementById('evaluationMessage');
    if (message) {
      message.textContent = '';
      message.hidden = true;
    }
    applyDownload(targetDocument.getElementById('evaluationDownloadWord'), '', '');
    applyDownload(targetDocument.getElementById('evaluationDownloadPdf'), '', '');

    const rerun = targetDocument.getElementById('evaluationRerunButton');
    if (rerun) rerun.disabled = true;

    const input = targetDocument.getElementById('evaluationGroundTruthInput');
    if (input && 'value' in input) input.value = '';

    const groundTruthStatus = targetDocument.getElementById('evaluationGroundTruthStatus');
    if (groundTruthStatus) groundTruthStatus.textContent = '当前标准答案：未上传（实体严格指标暂不可用）';

    const actionStatus = targetDocument.getElementById('evaluationActionStatus');
    if (actionStatus) actionStatus.textContent = '';
  }

  return { buildViewModel, render, reset, CARD_IDS };
});
