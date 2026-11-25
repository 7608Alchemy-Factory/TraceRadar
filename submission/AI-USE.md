# AI Use Declaration

**TraceRadar · COMP7608B Group 65 · 25 November 2026**

- **OpenAI Codex (GPT-6 family)** assisted with proposal formatting, English report editing, LaTeX layout and diagrams, and cross-checks against project files. Codex also inspected the source history, prepared the final branch and submission documentation, updated the build dependency, and ran local tests and packaging checks.
- **Evaluation assistant (tool and model unrecorded)** prepared preliminary comparisons to the frozen references and proposed search relevance judgments. The evaluation author reviewed required facts, disputed event grouping, notification decisions, failure cases, and search labels. The report uses the team's recorded measurements and benchmarks.
- **Runtime model** The reported application runs used the configured DeepSeek model ID `deepseek-flash` and the `assessment-v14` prompt revision. See [runtime prompts and tool instructions](../event-tracker/backend/app/llm.py) and [source recommendation and discovery prompts](../event-tracker/backend/app/connectors.py).

## Available prompt records

- [Proposal preparation](prompts/proposal-preparation-redacted.md)
- [Final report preparation](prompts/report-preparation-redacted.txt)
- [Final code integration and packaging request](prompts/code-submission-redacted.txt)
- [Final switch to the main branch](prompts/code-submission-main.txt)
- [Development prompt drafts](prompts/development-prompt-drafts.zh-CN.md) preserved as drafts from the local workspace. Their use in prior AI sessions could not be verified.

Local filesystem paths and personal information were redacted where applicable. Complete earlier development and evaluation prompt logs, specific AI tools and model IDs for those sessions, and any unavailable team records could not be recovered from the supplied materials. The group remains responsible for the correctness of the implementation, evaluation, and AI-assisted materials.
