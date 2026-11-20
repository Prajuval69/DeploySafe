# Definition of done

- [ ] `tools/verify_kit.py` passes.
- [ ] Installs/runs with stdlib Python 3.11+ only.
- [ ] No cloud account/Docker/admin/online dependency.
- [ ] Safe workspace and manifest/sha checks enforced.
- [ ] Real local subprocess and HTTP service deploy.
- [ ] Healthy version upgrades successfully.
- [ ] Bad version rejected, previous healthy service stays available.
- [ ] Automatic rollback event only after recovery verification.
- [ ] SQLite audit persists and cannot be silently edited.
- [ ] `doctor`, `init`, `status`, `history`, `deploy`, `demo`, `dashboard` work.
- [ ] Dashboard shows actual persisted status, not hardcoded text.
- [ ] At least 45 substantive standard-library unittests pass.
- [ ] Real localhost integration tests use bounded timeouts and clean processes.
- [ ] Windows/PowerShell and Linux/Bash wrappers delivered; untested OS disclosed.
- [ ] Runbooks, architecture, security limits and evidence provided.
- [ ] No fictitious AWS, Docker, CI/CD cloud deployment or enterprise claims.
