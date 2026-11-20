# DeploySafe — Local DevOps Deployment, Validation & Automatic Rollback

**Codex-ready BUILD KIT — not a completed deployment platform.** This archive contains the authoritative build prompt, project contracts, local demonstration applications, deterministic fault fixtures, sample configurations, architecture and test specifications. Codex should implement the actual deployment manager in this same repository.

## Why this project
Show Kyndryl-style infrastructure fundamentals: versioned deployments, safe automation, checks before releasing, service-health monitoring, diagnosing bad changes, rollback, Windows/Linux scripting, clear runbooks, and traceable audit events. A *local DevOps lab* is valuable without pretending to be AWS, Kubernetes, or enterprise cloud operations.

## Requirements
- **No AWS account, Docker, VMs, paid API, external AI model or cloud service.** No admin rights or network beyond localhost.
- Python **3.11+** and **standard library only** for the required runtime, CLI, dashboard, fixtures and `unittest` tests.
- Windows 10/11 PowerShell and Linux Bash entry scripts; actual execution on each OS must be verified separately.
- Strict sandbox: write only to an explicitly configured workspace within this repository by default; never manage real OS services or existing user applications.
- Deploy only trusted, versioned **bundled Python demo apps**; never accept arbitrary shell commands, scripts from URLs, or unsafe release paths.

## Quick start with Codex
1. Extract ZIP and open `DeploySafe_Codex_Starter/` in Codex.
2. Read `AGENTS.md`, `prompts/MASTER_CODEX_PROMPT.md`, and `docs/00_INDEX.md`.
3. Paste the complete contents of `prompts/CODEX_START_MESSAGE.md` into Codex.
4. Have Codex **build, test, and document** the actual `deploysafe` package, rather than stopping at design.

## Commands that work NOW in this starter kit
```bash
python tools/verify_kit.py
python tools/generate_demo_fixtures.py
# one of the included healthy demo releases can be served directly:
python fixtures/demo_releases/1.0.0/app.py --port 8765
# second terminal: curl http://127.0.0.1:8765/health
```
The bundled `app.py` programs are tiny test-target apps, **not** DeploySafe itself.

## Expected commands AFTER Codex implements DeploySafe
```bash
python -m deploysafe --help
python -m deploysafe doctor
python -m deploysafe init --config config/default.json
python -m deploysafe deploy --release fixtures/demo_releases/1.0.0
python -m deploysafe deploy --release fixtures/demo_releases/1.1.0
python -m deploysafe deploy --release fixtures/demo_releases/1.2.0-healthfail
python -m deploysafe status
python -m deploysafe history
python -m deploysafe demo --all
python -m deploysafe dashboard --port 8787
python -m unittest discover -s tests -v
```
These are **target interfaces**; they do not work until Codex implements the application.

## Deliverable honesty
On a resume say **local deployment and rollback lab**, not AWS/enterprise deployment. Claim Windows/Linux runtime support only for platforms you actually ran. Record test counts only after the full test suite is executed. See `templates/RESUME_BULLETS_CONDITIONAL.md`.
