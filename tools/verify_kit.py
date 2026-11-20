#!/usr/bin/env python3
"""Check kit assets; safe and fully offline."""
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
required=['AGENTS.md','README_FIRST.md','prompts/MASTER_CODEX_PROMPT.md',
          'prompts/CODEX_START_MESSAGE.md','docs/00_INDEX.md','config/default.json',
          'tools/generate_demo_fixtures.py','fixtures/scenario_manifest.csv']
errors=[]
for name in required:
    if not (ROOT/name).is_file():errors.append('Missing: '+name)
folders=sorted((ROOT/'fixtures/demo_releases').iterdir()) if (ROOT/'fixtures/demo_releases').exists() else []
if len(folders)!=8:errors.append('Expected 8 demo releases, found '+str(len(folders)))
for folder in folders:
    try:
        manifest=json.loads((folder/'release.json').read_text(encoding='utf-8'))
        config=json.loads((folder/'app_config.json').read_text(encoding='utf-8'))
        assert manifest['release_id']==folder.name
        assert config['version']==folder.name
        for file, expected in manifest['sha256'].items():
            real=hashlib.sha256((folder/file).read_bytes()).hexdigest()
            if real!=expected and folder.name!='1.5.0-tampered':
                errors.append(f'Unexpected checksum mismatch: {folder.name}/{file}')
            if real==expected and folder.name=='1.5.0-tampered' and file=='app.py':
                errors.append('Tampered fixture must have mismatching checksum')
    except (OSError,KeyError,ValueError,AssertionError) as exc:
        errors.append(f'Invalid release fixture {folder.name}: {exc}')
scenarios=list((ROOT/'fixtures/scenarios').glob('*.json'))
if len(scenarios)!=8:errors.append(f'Expected 8 scenario JSON files, found {len(scenarios)}')
for name in scenarios:
    try:
        j=json.loads(name.read_text(encoding='utf-8'))
        assert j['expected_outcome'] in ('SUCCESS','REJECTED_AND_RETAINED')
        assert j['release_id'] and j['expected_active_version']
    except (ValueError,KeyError,AssertionError) as exc:
        errors.append(f'Invalid scenario {name.name}: {exc}')
docs=list((ROOT/'docs').glob('[0-9][0-9]_*.md'))
if len(docs)!=23:errors.append(f'Expected 23 numbered docs, found {len(docs)}')
if errors:
    print('FAILED kit validation:')
    for err in errors:print(' -',err)
    sys.exit(1)
print(f'PASS: kit assets validated — {len(docs)} docs, {len(folders)} releases, {len(scenarios)} scenarios')
print('NOTE: This does not test DeploySafe; app still must be implemented by Codex.')
