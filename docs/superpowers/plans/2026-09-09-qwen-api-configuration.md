# Qwen API Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the user-supplied Qwen configuration, verify the existing dual-provider integration, and leave this workspace's web application running with DeepSeek still selected by default.

**Architecture:** No production code changes are required because the repository already implements request-scoped DeepSeek/Qwen selection. Install the ignored root `.env`, create an ignored Python 3.11 virtual environment for this checkout, verify Qwen through both existing tests and one minimal live completion, then run this checkout on port 8001 because port 8000 is occupied by an unrelated working copy.

**Tech Stack:** PowerShell, Python 3.11, uv, unittest, FastAPI/Uvicorn, OpenAI-compatible DashScope API

## File map

- Create locally: `.env` — user-supplied credentials and model/runtime settings; ignored by Git and never committed.
- Create locally: `.venv/` — Python 3.11 runtime and dependencies; ignored by Git and never committed.
- Modify: `.gitignore` — exclude application runtime logs from version control.
- Create at runtime: `logs/server-8001.stdout.log` and `logs/server-8001.stderr.log` — local server diagnostics; ignored by Git.
- Verify without modification: `three_agent_service.py` — existing request-scoped provider resolution.
- Verify without modification: `frontend/index.html` and `frontend/scripts.js` — existing DeepSeek-default/Qwen-selectable UI.
- Verify without modification: `tests/test_model_provider_selection.py` — existing provider regression coverage.

### Task 1: Install the supplied environment configuration

- [ ] Confirm the attachment source exists and the destination is exactly the repository root `.env`. The absolute attachment source is taken from the current task context and intentionally excluded from this version-controlled plan because it contains a personal temporary-directory identifier.

- [ ] Copy the source file without printing its contents:

```powershell
Copy-Item -LiteralPath $sourceEnv -Destination $destinationEnv
```

- [ ] Verify byte-for-byte identity and Git exclusion without displaying any values:

```powershell
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $sourceEnv).Hash -ne
    (Get-FileHash -Algorithm SHA256 -LiteralPath $destinationEnv).Hash) {
    throw 'The installed .env differs from the supplied file.'
}
git check-ignore -v .env
```

Expected: the hashes match and `.gitignore` reports `.env` as ignored.

### Task 2: Exclude runtime logs from Git

- [ ] Demonstrate the missing ignore behavior before changing configuration:

```powershell
New-Item -ItemType Directory -Path logs -Force | Out-Null
New-Item -ItemType File -Path logs/ignore-check.log -Force | Out-Null
git check-ignore logs/ignore-check.log
```

Expected: `git check-ignore` exits with code 1 because the repository does not yet ignore `logs/`.

- [ ] Add this repository-root rule to `.gitignore`:

```gitignore
/logs/
```

- [ ] Verify the configuration change:

```powershell
git check-ignore -v logs/ignore-check.log
```

Expected: `.gitignore` reports `/logs/` as the matching rule.

- [ ] Commit the runtime hygiene change:

```powershell
git add .gitignore
git commit -m "chore: ignore local runtime logs"
```

### Task 3: Prepare this checkout's Python runtime

- [ ] Create the ignored Python 3.11 environment:

```powershell
uv venv .venv --python 3.11
```

Expected: `.venv/Scripts/python.exe` is created with Python 3.11 or newer.

- [ ] Install the repository dependencies:

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

Expected: dependency resolution and installation exit with code 0.

- [ ] Smoke-check required imports:

```powershell
.venv/Scripts/python.exe -c "import fastapi, uvicorn, dotenv, openai, httpx; print('runtime_dependencies=ok')"
```

Expected: `runtime_dependencies=ok`.

### Task 4: Verify the existing provider integration

- [ ] Run the complete provider-selection regression suite:

```powershell
.venv/Scripts/python.exe -m unittest tests.test_model_provider_selection -v
```

Expected: every test passes, including the DeepSeek default, Qwen runtime isolation, endpoint handling, frontend selector, and secret-redaction cases.

- [ ] Load `.env` and print only non-sensitive provider metadata:

```powershell
.venv/Scripts/python.exe -c "from dotenv import load_dotenv; load_dotenv(); from three_agent_service import get_model_provider_catalog; c=get_model_provider_catalog(); q=next(p for p in c['providers'] if p['id']=='qwen'); assert c['default']=='deepseek' and q['configured']; print('default=deepseek qwen_configured=true model='+q['model']+' endpoint_host='+q['endpoint_host'])"
```

Expected: default remains `deepseek`, Qwen is configured, and no credential is printed.

### Task 5: Verify the live Qwen API

- [ ] Send one minimal OpenAI-compatible chat completion using the installed Qwen settings:

```powershell
.venv/Scripts/python.exe -c "import os; from dotenv import load_dotenv; from openai import OpenAI; load_dotenv(); client=OpenAI(api_key=os.environ['DASHSCOPE_API_KEY'], base_url=os.environ['QWEN_BASE_URL']); response=client.chat.completions.create(model=os.environ.get('QWEN_MODEL','qwen-plus'), messages=[{'role':'user','content':'只回复 OK'}], max_tokens=8); assert response.choices and response.choices[0].message.content; print('qwen_api=ok model='+response.model)"
```

Expected: `qwen_api=ok` and a model identifier. No key or full response body is printed.

### Task 6: Start and verify this workspace's application

- [ ] Confirm port 8001 is free. Port 8000 is not used because inspection found it belongs to another checkout:

```powershell
if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8001 is already occupied.'
}
```

- [ ] Start Uvicorn in a hidden background process and retain the PID:

```powershell
New-Item -ItemType Directory -Path logs -Force | Out-Null
$serverProcess = Start-Process -FilePath (Resolve-Path '.venv/Scripts/python.exe') `
    -ArgumentList @('-m','uvicorn','main:app','--host','127.0.0.1','--port','8001') `
    -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput 'logs/server-8001.stdout.log' `
    -RedirectStandardError 'logs/server-8001.stderr.log'
```

Expected: a running Python process is returned.

- [ ] Poll the local health endpoints for up to 30 seconds and verify the public catalog:

```powershell
$catalog = $null
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $root = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/' -TimeoutSec 2
        $catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/model-providers' -TimeoutSec 2
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if ($root.StatusCode -ne 200 -or $catalog.default -ne 'deepseek') {
    throw 'Application health verification failed.'
}
$qwen = $catalog.providers | Where-Object id -eq 'qwen'
if (-not $qwen.configured) { throw 'Qwen is not configured in the running application.' }
"server_pid=$($serverProcess.Id) url=http://127.0.0.1:8001 qwen_configured=true"
```

Expected: root returns HTTP 200, `default=deepseek`, `qwen.configured=true`, and the new server PID is printed.

- [ ] Open `http://127.0.0.1:8001` in the Codex browser panel and leave the verified server process running for the user.

### Task 7: Final verification

- [ ] Re-run the provider test suite after startup:

```powershell
.venv/Scripts/python.exe -m unittest tests.test_model_provider_selection -v
```

Expected: all provider-selection tests still pass.

- [ ] Confirm local secrets and runtime artifacts remain excluded from Git:

```powershell
git status --short --ignored .env .venv logs
```

Expected: `.env`, `.venv/`, and `logs/` appear only as ignored entries; no secret-bearing file is staged or tracked.
