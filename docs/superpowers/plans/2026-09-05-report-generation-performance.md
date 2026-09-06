# Report Generation Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the current evidence and export quality gates while reducing ordinary report generation to a 15–30 minute target.

**Architecture:** The finalizer keeps one full review, then reviews only unresolved claim blocks and their neighbors for at most two further rounds. It stops when a repair makes no change or two consecutive reviewed rounds show no issue-count improvement, preserves the best strictly reviewed version, and records the reason and timing. The browser separately displays task elapsed time so connection uptime is no longer mistaken for generation time.

**Tech Stack:** Python 3, asyncio, unittest/pytest, browser JavaScript, Playwright.

---

### Task 1: Add bounded risk-driven finalization

**Files:**
- Modify: `backend/reporting/finalization.py:387-505`
- Test: `tests/test_report_finalization.py`

- [ ] **Step 1: Write failing policy tests**

Add tests that call `finalize_report` without `max_rounds`, return one unresolved issue on every review, and assert no more than three review calls. Add a test where the second-round repair returns an empty replacement list and assert the audit stops before another review with `stop_reason == "no_effective_change"`. Add a helper test with five claim blocks and one `B3` issue that expects `B2`, `B3`, and `B4` as the selected risk scope.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
.\portable_python\python.exe -c "import os,sys,unittest;sys.path.insert(0,os.getcwd());s=unittest.defaultTestLoader.discover('tests','test_report_finalization.py');r=unittest.TextTestRunner(verbosity=2).run(s);raise SystemExit(not r.wasSuccessful())"
```

Expected before implementation: the default policy performs more than three rounds, no-change repairs still trigger a review, and `select_risk_blocks` does not exist.

- [ ] **Step 3: Implement the bounded policy**

Change the default signature to `max_rounds=3`. Add this helper:

```python
def select_risk_blocks(blocks, issues, *, include_neighbors=True):
    indexes = set()
    by_id = {block['id']: index for index, block in enumerate(blocks)}
    for issue in issues:
        identity = issue.get('block') or issue.get('section')
        if identity in by_id:
            indexes.add(by_id[identity])
            continue
        section = issue.get('section')
        indexes.update(index for index, block in enumerate(blocks) if section and block.get('section') == section)
    if not indexes:
        return blocks
    if include_neighbors:
        indexes |= {neighbor for index in tuple(indexes) for neighbor in (index - 1, index + 1)
                    if 0 <= neighbor < len(blocks)}
    return [block for index, block in enumerate(blocks) if index in indexes]
```

Before each targeted repair, retain the prior `pending` list. If the repaired candidate equals the current report, set `audit['stop_reason'] = 'no_effective_change'`, log the stop, and break before review. On rounds after the first, pass `select_risk_blocks(all_blocks, prior_pending)` to `collect_review` and `validate_review`; continue to run `check_contract` against the entire report. Record `review_scope` with total and reviewed block counts. After every completed round, compare its effective issue count with the previous completed round; when it does not decline, set `stop_reason = 'no_issue_reduction'` and stop. Keep the existing best-round selector.

- [ ] **Step 4: Run focused tests**

Run the Task 1 command again. Expected: all finalization tests pass.

### Task 2: Record timing and show actual task elapsed time

**Files:**
- Modify: `backend/reporting/finalization.py:387-505`
- Modify: `three_agent_service.py:884-912`
- Modify: `frontend/scripts.js:1-460,1348-1515`
- Modify: `frontend/index.html:224-233,258`
- Test: `tests/test_report_finalization.py`
- Test: `work/check_connection_status.cjs`

- [ ] **Step 1: Add failing timing assertions**

Assert every saved round includes a nonnegative `duration_seconds`, the audit includes `max_rounds == 3`, and the browser changes `#taskElapsed` from `-` while a delayed report request is active.

- [ ] **Step 2: Implement timing metadata**

Use `time.perf_counter()` around each finalization round and save `duration_seconds`, `review_scope`, `issue_count`, and `stop_reason` in the audit. Pass an environment-controlled value with a bounded default:

```python
max_rounds = min(5, max(1, int(os.getenv('REPORT_EDITOR_MAX_ROUNDS', '3'))))
```

Store the chosen value in the audit as `max_rounds`.

- [ ] **Step 3: Implement browser task timing**

Add `taskStartTime`. Set it immediately before the report request and clear it after success or failure. Render `Task elapsed:` from `taskStartTime` every two seconds while Research is Active. Rename the existing duration label to `API online for:` and update the script cache version.

- [ ] **Step 4: Verify timing behavior**

Run:

```powershell
.\portable_python\python.exe -c "import os,sys,unittest;sys.path.insert(0,os.getcwd());s=unittest.defaultTestLoader.discover('tests','test_report_finalization.py');r=unittest.TextTestRunner(verbosity=2).run(s);raise SystemExit(not r.wasSuccessful())"
& 'C:\Users\吴烨\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check frontend/scripts.js
& 'C:\Users\吴烨\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' ..\..\..\check_connection_status.cjs
```

Expected: Python tests pass, JavaScript syntax is valid, and the browser test reports online, active-task, offline-probe, and task-elapsed checks as true.

### Task 3: Regression, package, and activate

**Files:**
- Modify: `outputs/工作台自动成稿改造说明.md`
- Update: `outputs/系统报告格式升级补丁.zip`

- [ ] **Step 1: Run the report regression suite**

Run the six existing report test modules and expect all 101 existing tests plus the new performance tests to pass.

- [ ] **Step 2: Run browser report regression**

Run `work/check_workbench_acceptance.cjs`. Expect the detailed report, both images, centered figure sources, and all three downloads to pass.

- [ ] **Step 3: Refresh the delivery package**

Run `work/package_report_upgrade.py` and verify the archive copies of `backend/reporting/finalization.py`, `three_agent_service.py`, `frontend/scripts.js`, and `frontend/index.html` match the installed files byte for byte.

- [ ] **Step 4: Activate without losing the current task**

Wait until the currently running report request has completed, then restart the local backend once. Verify `/`, `/.well-known/agent-discovery.json`, and `/api/intelligence-templates` return HTTP 200 and a fresh page shows `Connected` plus the new task timing row.

This workspace is not a Git repository, so commit steps are omitted; the verified upgrade archive is the rollback and transfer artifact.
