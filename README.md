# TraceRadar

**Autonomous Event Tracking with Persistent Evidence**

**COMP7608B Final Project · Group 65 · The University of Hong Kong**

**Members** Ruiyang Chen, Qinyuan Cui, Jingjia Feng, Mangjin Qian

TraceRadar helps users follow developing stories across approved sources. It decides which reports are relevant, whether they belong to existing events, and whether a change warrants an alert. It stores the evidence, event history, decisions and review requests in a local workspace.

**Submitted version** [TraceRadar on GitHub (`main`)](https://github.com/7608Alchemy-Factory/TraceRadar/tree/main). In a checkout, run `git rev-parse HEAD`. In the submitted ZIP, the exact commit from `main` is recorded in [SUBMISSION_REVISION](SUBMISSION_REVISION). The application integrates [the final V2 frontend](https://github.com/7608Alchemy-Factory/TraceRadar/tree/feat/c-live-rss-frontend-1124-v2), [the frozen evaluation code and cases](https://github.com/7608Alchemy-Factory/TraceRadar/tree/codex/evaluation-freeze-v1), and [the reviewed results](https://github.com/7608Alchemy-Factory/TraceRadar/tree/codex/evaluation-results-v1).

## Install and run

Install Python 3.11+ and Node.js 22+ with npm. Internet access is needed for the initial dependency installation. The credential-free demo uses SQLite and a browser, with no GPU, notebooks, or external APIs. Verified using Windows, Python 3.12.3, Node.js 24.22.3 and npm 10.9.4. The [Python lockfile](event-tracker/backend/requirements.lock.txt) pins FastAPI 0.115.12, Uvicorn 0.34.2 and all other Python dependencies. The [npm lockfile](event-tracker/frontend/package-lock.json) pins React 19.1.0, TypeScript 5.8.3, Vite 6.4.6 and their dependencies.

Run from a checkout or the extracted submission folder in PowerShell.

```powershell
python -m venv .\event-tracker\backend\.venv
.\event-tracker\backend\.venv\Scripts\python.exe -m pip install -r .\event-tracker\backend\requirements.lock.txt
Push-Location .\event-tracker\frontend
npm ci
npm run build
Pop-Location
.\Start-TraceRadar-Submission.cmd
```

Open the [workspace](http://127.0.0.1:8630/) or [API docs](http://127.0.0.1:8630/docs). Use [Stop-TraceRadar-Submission.cmd](Stop-TraceRadar-Submission.cmd) to stop the service. The launcher serves the built frontend and API on loopback in a single process and uses `event-tracker/data/submission.db`. The Windows launcher leaves scheduled checks running when the browser is closed. The older V2 launcher uses port 8629 and its own database.

On macOS/Linux, run the following from the source root after installing Python and Node.js.

```sh
python3 -m venv event-tracker/backend/.venv
event-tracker/backend/.venv/bin/python -m pip install -r event-tracker/backend/requirements.lock.txt
(cd event-tracker/frontend && npm ci && npm run build)
event-tracker/backend/.venv/bin/python event-tracker/scripts/local_workspace.py --port 8630 --database-path data/submission.db
```

## Sample inputs and access

- **Ordinary task** Run the ExampleSoft demo below. The bundled synthetic inputs reconstruct two events across three evidence batches. Replaying the same batches leaves the event history and notification counts unchanged.
- **Changed request** In the ExampleSoft topic, open Edit title and replace the tracking goal with `Track confirmed ExampleSoft patches and affected releases.` Add the exclusion `unverified rumors`, save, and verify the new objective and exclusion on the topic. Use a configured model to judge new evidence against the revised goal. The Mock provider uses canned ExampleSoft decisions.
- **Failure and recovery** The included Hong Kong McDonald's replay supplies the input for two tests which make a stubbed model return HTTP 401 or 503 on the same evidence item. The tests verify the bounded retry policy, linked resume and notification idempotency. Reproduce them with the fault-test command below.
- **Further examples** Use the [15 example topic categories](event-tracker/docs/topic-examples.zh-CN.md), the [bundled replay fixtures](event-tracker/fixtures), or the [V2 user guide](event-tracker/README.md) for approval, alert and schedule workflows.

To process arbitrary requests or run scheduled live checks, open **Model settings** and enter your Chat Completions-compatible base URL, API key and model ID. You can also create a private `event-tracker/.env` using [.env.example](event-tracker/.env.example). The code's `qwen` provider label refers to the configurable endpoint. RSS needs a reachable feed. Bocha, Tavily and the Jina Reader fallback may need API access, and hosted models and search providers may incur fees. Hardware needs for local inference depend on the Ollama model. The application uses a single-user local workspace and in-app alerts.

## Evaluation and traces

Run the following from `event-tracker` in PowerShell. Start the workspace before running the API demo. These commands use bundled data and require no credentials.

```powershell
$py = ".\backend\.venv\Scripts\python.exe"
& $py scripts/demo.py --base-url http://127.0.0.1:8630 --output data/mock-demo-check.json
Push-Location backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -v tests/test_mcd_benchmark.py -k fault_report_and_resume
Pop-Location
& $py scripts/run_benchmark.py --provider mock --cases cedar_patch_v1 cve_2024_3400_v1 dual_route_notice_v1 returning_offer_v1
& $py scripts/score_search_benchmark.py --capture evaluation/results/freeze_v1/search_mcd_hk_capture_minimal.json --labels evaluation/search_cases/mcd_hk_nov2026_search_v1_assistant_labels.json --output data/search-score-check.json
```

The [evaluation report](event-tracker/evaluation/FINAL_REPORT_EVALUATION_DRAFT.md) documents the human-reviewed results, baseline, judgment rules and failure cases. The [frozen results](event-tracker/evaluation/results/freeze_v1) include human adjudication, the per-article baseline, a minimal search capture and reviewed scores. The [submission verification](event-tracker/evaluation/submission/README.md) links to the credential-free success and recovery traces.

For a new model replay, configure the provider in `event-tracker/.env` or your process environment and replace `--provider mock` with `--provider qwen` in the replay command. The reported runs used `deepseek-flash`, runtime prompt revision `assessment-v14`, a 30-second model timeout and up to two attempts including the initial call. The [model adapter](event-tracker/backend/app/llm.py) contains the prompt, tool instructions and endpoint-specific generation settings. Fresh model responses and live retrieval can vary. Mock replay verifies the pipeline; human review and saved model outputs support the published semantic scores.

See the [AI-use declaration and available prompt records](submission/AI-USE.md) for project assistance and known gaps in the prompt archive.
