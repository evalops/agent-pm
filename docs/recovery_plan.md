## Connector & Sync Recovery Plan

### Objectives

1. Restore connector modules (GitHub, Slack, Gmail, Calendar, Google Drive, Notion) with dry-run behaviour and configuration-driven enablement.
2. Rebuild periodic sync manager with persistence hook recording sync runs.
3. Reintroduce database model, migration, and helper utilities for `connector_syncs`.
4. Reinstate API endpoint `/sync/status` plus settings additions for intervals and credentials.
5. Recreate automated tests covering connectors, sync manager, and API behaviour.
6. Run full test suite before committing and pushing the changes.

### Work Breakdown

1. **Settings Enhancements**
   - Re-add list parsing validators.
   - Define new environment-driven fields (repositories, channel lists, sync intervals, Gmail service account config, Notion IDs, etc.).

2. **Connector Modules**
   - Create `agent_pm/connectors` package with base class and six connector implementations.
   - Ensure each connector respects `settings.dry_run` and returns structured payloads.

3. **Sync Infrastructure**
   - Implement `PeriodicSyncManager` in `agent_pm/tasks/sync.py` to schedule async jobs and log results.
   - Integrate persistence helper (`agent_pm/storage/syncs.py`) and reuse in manager.

4. **Persistence Layer**
   - Extend `agent_pm/storage/database.py` with `ConnectorSync` model.
   - Add Alembic migration creating `connector_syncs` table and indices.
   - Write helper functions for recording/listing sync executions.

5. **API & App Lifecycle**
   - Register sync manager in `app.py` lifespan.
   - Add `/sync/status` endpoint returning recent syncs via storage helper.

6. **Testing**
   - Add `tests/test_connectors.py` covering dry-run payloads, manager scheduling, persistence checks.
   - Extend `tests/test_tasks_api.py` to validate `/sync/status`.
   - Ensure pytest passes.

7. **Git Workflow**
   - Run `pytest`.
   - Stage all files, craft commit message summarizing recovery.
   - Push changes to remote immediately after commit.

### Precautions

- Avoid using `git checkout -- .`; rely on targeted reverts or stashes.
- Verify file contents with Read tool post-edit.
- Maintain backups of critical files before major commands.
