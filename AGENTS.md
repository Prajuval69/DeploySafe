# AGENTS.md — Authoritative Codex instructions for DeploySafe

You are implementing a **real, safe, offline, local** DevOps deployment and rollback simulator. This is a portfolio project, not infrastructure administration software.

1. First inspect `prompts/MASTER_CODEX_PROMPT.md`, then every numbered `docs/*.md`, then `config/`, `fixtures/`, and `tools/`. Create `docs/IMPLEMENTATION_PROGRESS.md` as work proceeds.
2. Complete working functionality and tests; do not submit a pretty mock dashboard with inert controls. Do not stop after a plan or create only pseudocode.
3. Runtime is **Python 3.11+ standard library only**. Use `argparse`, `sqlite3`, `subprocess`, `http.server`, `urllib`, `pathlib`, `hashlib`, `logging`, `unittest`, etc. No Docker, AWS, cloud, external API, AI provider, Node/npm or privileged commands.
4. Only work inside the repo-root `.deploysafe_workspace/` unless a user explicitly configures an equally safe project-contained location. Do not touch real services, system directories, scheduled tasks, registry, firewalls, or processes not launched by DeploySafe.
5. Release apps bind to **127.0.0.1 only**; fixed localhost ports with bounded range and collision detection. No port scans, arbitrary URLs, host commands or file downloads.
6. Treat `release.json` and all user-controlled text as untrusted input: enforce version format, schema types, canonical paths within allowed roots, checksum verification, no symlink/path traversal, bounded sizes, and no shell interpolation. Use `subprocess.Popen([sys.executable, 'app.py', ...], shell=False)` with safe cwd and environment allowlist.
7. Transaction semantics: stage -> verify -> preflight -> start candidate -> health-check -> commit ACTIVE pointer atomically -> audit; on failure restore last known healthy deployment and verify recovery. Prevent concurrent deployments; preserve audit history.
8. No fake AWS experience, no made-up cloud screenshots. OS-specific PowerShell/Bash scripts must be optional, non-destructive, and honestly tested. Avoid asserting validation on an OS without actual execution there.
9. Implement 45+ substantive `unittest` methods with realistic assertions and deterministic fault cases. Never create filler tests. Test safe failure modes and rollback; don't kill anything except DeploySafe-owned subprocesses.
10. Keep setup simple on Windows. Ensure Python commands work without symlinks, admin rights, Docker or network downloads.
11. Provide complete `README.md`, `docs/OPERATIONS_RUNBOOK.md`, `docs/IMPLEMENTATION_PROGRESS.md`, `docs/EVIDENCE.md`, security limitations, and reproducible commands.
12. Verify fixtures with `python tools/verify_kit.py` before implementation; do not overwrite examples as though they were generated results. Run the app demo and tests before declaring success. Report incomplete features candidly.
