# Shell wrappers to be implemented by Codex

`docs/15_WINDOWS_POWERSHELL.md` and `docs/16_LINUX_BASH.md` specify safe wrapper scripts. They MUST call the Python CLI with explicit argument arrays. No service installers, system changes, admin/sudo or downloaded binaries. The `tools/generate_demo_fixtures.py` and `tools/verify_kit.py` work immediately without these wrappers. Do not claim the wrappers were executed until verified on the corresponding OS.
