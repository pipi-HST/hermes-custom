# Hermes Knowledge local data reset runbook

This runbook is for the intentional destructive reset that clears local Hermes Knowledge personal data and keeps operational configuration.

## Scope

Reset clears:

- `<repos>/hermes-custom/data/captures.db` Knowledge rows and legacy `life_*` residual rows.
- Legacy Knowledge Store: `~/Obsidian/hermes-vault`.
- Knowledge content backups under the allowlist reported by `scripts/reset_hermes_local_data.py --dry-run`.
- Existing files under the new Knowledge File storage root before recreating the empty skeleton.

Reset preserves:

- `<repos>/hermes-custom/config.yaml`.
- API keys, model settings, LaunchAgent plist files, and normal logs.
- `<repos>/hermes-life/data/life.db`.

## Preflight

Run:

```bash
cd <repos>/hermes-custom
.venv/bin/python scripts/reset_hermes_local_data.py --dry-run
```

Do not apply reset while `automation_preflight.blockers` is non-empty.

On this machine the expected blockers before the reset window are:

- `watch_vault.py process is running`
- `enabled cron jobs: daily-review`

The current `daily-review` cron job id is `b0b01cd8a05b`; verify with:

```bash
cd <repos>/hermes-agent-main
.venv/bin/python -m hermes_cli.main cron list
```

## Stop write automation

Stop the Knowledge file watcher:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.pipi.hermes-vault-watcher.plist"
```

Pause the Knowledge daily review cron:

```bash
cd <repos>/hermes-agent-main
.venv/bin/python -m hermes_cli.main cron pause b0b01cd8a05b
```

Re-run preflight:

```bash
cd <repos>/hermes-custom
.venv/bin/python scripts/reset_hermes_local_data.py --dry-run
```

Continue only when `automation_preflight.blockers` is empty.

## Apply reset

```bash
cd <repos>/hermes-custom
.venv/bin/python scripts/reset_hermes_local_data.py --apply --confirm 'RESET HERMES LOCAL DATA'
```

## Verify reset

Run the post-reset empty-state verifier:

```bash
cd <repos>/hermes-custom
.venv/bin/python scripts/reset_hermes_local_data.py --verify-empty
```

It exits `0` only when the legacy store and content backups are gone, target DB tables are empty, schema is queryable, the new storage skeleton exists, the new storage has no files, and preserved paths such as `config.yaml` and `<repos>/hermes-life/data/life.db` still exist.

Check empty RAG behavior:

```bash
cd <repos>/hermes-custom
.venv/bin/python -m pytest tests/test_rag_bridge.py::test_search_knowledge_base_empty_store_returns_no_knowledge_material -q
```

## Restart runtime

Reload watcher after the reset. The plist should point at `<vault>`.

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.pipi.hermes-vault-watcher.plist"
launchctl kickstart -k "gui/$(id -u)/com.pipi.hermes-vault-watcher"
```

Restart the Hermes gateway:

```bash
launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway"
```

Resume daily review only after smoke verification:

```bash
cd <repos>/hermes-agent-main
.venv/bin/python -m hermes_cli.main cron resume b0b01cd8a05b
```

## Smoke sample and cleanup

Run the non-destructive smoke in an isolated temp workspace:

```bash
cd <repos>/hermes-custom
.venv/bin/python scripts/reset_smoke.py --include-card
```

The smoke verifies:

1. capture draft creation
2. commit to Knowledge File storage
3. identity index creation
4. search/RAG citation
5. optional structured Knowledge Card object without creating `garden/` Markdown

This smoke uses a temporary DB and temporary Knowledge File storage, so it should not leave rows or files in the real empty store. Re-run `scripts/reset_hermes_local_data.py --verify-empty` after runtime restart to confirm the final real store is still empty.
