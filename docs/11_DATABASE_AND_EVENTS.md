# SQLite audit schema

Use SQLite `sqlite3` with foreign keys, WAL and schema version. Tables: `releases` (id/checksum/created), `deployments` (run id/from/to/status/timestamps), `deployment_events` (append-only id/deployment/timestamp/stage/code/message), `health_checks` (deploy/attempt/status/latency/reason), `active_history` (old/new/timestamp/reason), `app_meta`. Add useful indexes. Keep UTC ISO8601 and `CHECK` constraints. Never update/delete audit events; only append. Testing should verify audit integrity and persistence across application restarts. Data remains in sandbox.
