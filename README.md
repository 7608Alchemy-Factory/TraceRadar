# TraceRadar

**Autonomous Event Tracking with Persistent Evidence**

**COMP7608B Final Project · The University of Hong Kong · Group 65**

**Members** Ruiyang Chen, Qinyuan Cui, Jingjia Feng, Mangjin Qian

TraceRadar helps readers and analysts follow developing stories without rereading the same reports. Set a tracking goal and approve sources. On a manual or scheduled check, TraceRadar collects evidence, skips duplicates and decides which reports are relevant, which event each report belongs to and what has changed. It saves event updates and their evidence in SQLite. Uncertain or conflicting updates go to human review.

**Source version** The [submitted source branch](https://github.com/7608Alchemy-Factory/TraceRadar/tree/main) is `main`. In a Git checkout, run `git rev-parse HEAD`. In a submission ZIP exported with `git archive`, [SUBMISSION_REVISION](SUBMISSION_REVISION) contains the exact commit SHA.

## 1. Install and run

Use Python 3.11+, Node.js 22+ with npm, and a web browser. Install dependencies from the [Python lockfile](event-tracker/backend/requirements.lock.txt) and [npm lockfile](event-tracker/frontend/package-lock.json). The Windows package check used Python 3.12.4, Node.js 24.22.1 and npm 11.19.1. Key dependency versions are FastAPI 0.115.12, Uvicorn 0.34.2, Pydantic 2.11.4, pytest 8.3.5, React 19.1.0, TypeScript 5.8.3 and Vite 6.4.6.

The initial dependency installation needs internet access. After installation, the bundled demo and offline checks run with local SQLite and included fixtures. They require no API keys, paid services, GPU, separate database server or notebook.

### Windows PowerShell

Run from the repository root or the root of the extracted submission ZIP.

```powershell
python -m venv .\event-tracker\backend\.venv
.\event-tracker\backend\.venv\Scripts\python.exe -m pip install -r .\event-tracker\backend\requirements.lock.txt
Push-Location .\event-tracker\frontend
npm ci
npm run build
Pop-Location
.\Start-TraceRadar-Submission.cmd
```

Open the [workspace](http://127.0.0.1:8630/) or [API documentation](http://127.0.0.1:8630/docs). The launcher keeps the local service running after you close the browser. Run `.\Stop-TraceRadar-Submission.cmd` to stop it.

### macOS or Linux

Run from the same source root. Keep the terminal open while using the workspace and press `Ctrl+C` to stop.

```sh
python3 -m venv event-tracker/backend/.venv
event-tracker/backend/.venv/bin/python -m pip install -r event-tracker/backend/requirements.lock.txt
(cd event-tracker/frontend && npm ci && npm run build)
event-tracker/backend/.venv/bin/python event-tracker/scripts/local_workspace.py --port 8630 --database-path data/submission.db
```

Both commands serve the built React workspace and FastAPI from one local process on port `8630`. The database is `event-tracker/data/submission.db`. Read the [workspace user guide](event-tracker/README.md) for the interface and schedule settings. Its separate V2 launcher uses port `8629`.

## 2. Try the supplied tasks

| Task | Input and expected behavior |
| --- | --- |
| Ordinary task | Follow ExampleSoft disclosures and patches with the included [three evidence batches](event-tracker/fixtures/cybersecurity). The demo should produce two events, four state transitions and four notifications. Repeating all three batches should add no changes. |
| Changed request | Open **ExampleSoft security demo → Edit title** after the demo. Set the tracking goal to `Track confirmed ExampleSoft patches and affected releases.` Add `unverified rumors` as an exclusion, save it and check the updated topic. The Mock provider uses fixed rules for ExampleSoft. To test how a changed goal affects new evidence, configure a real model, approve a reachable source and run another check. |
| Failure and recovery | The [Hong Kong McDonald's replay](event-tracker/evaluation/cases/mcd_hk_campaigns_v1/case.json) is used in two automated tests. A stubbed model returns HTTP 401 or 503. Expect the authentication error to stop after one attempt and the service error to stop after two attempts. The tests then resume unfinished evidence and check that notifications are not duplicated. |

See the [recorded success, changed-request and failure traces](event-tracker/evaluation/submission/README.md). The [topic examples](event-tracker/docs/topic-examples.zh-CN.md) and [replay fixtures](event-tracker/fixtures) provide more inputs.

## 3. Configure a real model and sources

Open **Model settings** and enter a Chat Completions-compatible base URL, API key and model ID. The API uses `qwen` as its provider label for this configurable endpoint. For command-line runs, set `QWEN_BASE_URL`, `QWEN_API_KEY` and `QWEN_MODEL` in the process environment or in a private `event-tracker/.env` using [.env.example](event-tracker/.env.example). Keep credentials out of Git and shared archives. The model timeout defaults to 30 seconds. The assessment retry budget defaults to two attempts, including the first attempt.

Approve at least one reachable live source for a scheduled check. Public RSS needs network access. Bocha, Tavily and the Jina Reader fallback may need service credentials. Hosted inference and search can consume paid quota. A local Ollama model can use a loopback endpoint, and its CPU, RAM and GPU needs depend on the model.

The current assessment prompt is `assessment-v15`. In **Agent mode**, the model can load the assessment guide, look up linked evidence and prior status changes, and check an assessment draft. The backend validates the final response again before saving state. The [model adapter](event-tracker/backend/app/llm.py) defines the prompts and tools. The [API contract](event-tracker/docs/api_contracts.md) describes the request and response formats.

## 4. Reproduce the evaluation

Run these commands from `event-tracker` in a second PowerShell terminal. Leave the workspace running for the first command. On macOS/Linux, use `backend/.venv/bin/python` in place of `& $py`, and run the pytest commands from `backend` with `.venv/bin/python`.

```powershell
$py = (Resolve-Path .\backend\.venv\Scripts\python.exe).Path
& $py scripts/demo.py --base-url http://127.0.0.1:8630 --output data/mock-demo-check.json

Push-Location backend
& $py -m pytest -q
& $py -m pytest -v tests/test_mcd_benchmark.py -k fault_report_and_resume
Pop-Location

& $py scripts/run_benchmark.py --provider mock --cases cedar_patch_v1 cve_2024_3400_v1 dual_route_notice_v1 returning_offer_v1 --output data/mock-replay-check
& $py scripts/score_search_benchmark.py --capture evaluation/results/freeze_v1/search_mcd_hk_capture_minimal.json --labels evaluation/search_cases/mcd_hk_nov2026_search_v1_assistant_labels.json --output data/search-score-check.json
```

The Mock demo exercises event updates and notifications. The four Mock benchmark replays verify fixture loading and report generation. Their reports show zero events because the Mock provider only recognizes the ExampleSoft demo. The search scoring command recomputes the published score from the archived search capture and reviewed labels. For a fresh model replay, configure the provider as above and rerun the benchmark with `--provider qwen` and a new output folder. New model responses and live search results can vary.

The [evaluation report](event-tracker/evaluation/FINAL_REPORT_EVALUATION_DRAFT.md) gives the reference outcomes, human review criteria, baseline and observed failures. Its published replay results used `deepseek-flash`, `assessment-v14`, a 30-second timeout and up to two request attempts. Across two reviewed runs, the system matched 44 of 46 expected event checkpoints and saved 51 of 52 required facts at the expected stage. The [frozen results](event-tracker/evaluation/results/freeze_v1/README.md) include the reviewed findings, baseline and search inputs. These figures describe the recorded `assessment-v14` runs.

## AI assistance

The [AI-use declaration](submission/AI-USE.md) lists tools and models, their roles, available prompt records and gaps in the archive. OpenAI Codex (GPT-6 family) also assisted with this README revision and verification. The requests for this revision were `重写main分支下最新版本的readme，按照这个文件里的要求` and `写好了推上去`.
