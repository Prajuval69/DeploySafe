# Pre-deployment checks

Before launching: parse JSON, validate release ID and manifest, verify file SHA256, verify allowed path/size/symlink policy, validate app_config shape, ensure ports available, confirm writable sandbox, ensure proper Python version, ensure no concurrent operation, and ensure previous active status can be recovered. Invalid input must fail BEFORE changing active version. Record each check outcome with a stable code and reason. An explicit `validate-release` command runs preflight non-destructively.
