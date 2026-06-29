# Changelog

## 2026-06-27 — Summer mode (reduced schedule)

After a usage audit, disabled the costly/under-used automations for the summer break
(restore in the last week of August). Goal: keep the local email archive current and
queryable while cutting paid API usage to roughly pennies per month.

- **Active cron reduced to one archive-only job:** `fetch_and_triage.py --backfill --hours 4`,
  every 2h 07:00–21:00. Classifies with Haiku, archives non-noise, regenerates `_index.json`.
  No briefing email, Telegram, drafts, tasks, or Sonnet.
- **Disabled (commented out in crontab):** full 06:30 briefing run, `dashboard.py`,
  `urgent_check.py`, `update_tasks.py`, `--regenerate-style`, `project_discover.py`. Crontab
  backed up to `cron/backups/crontab-20260627-163944.bak`.
- **Config flags set to `false`:** `drafts.enabled`, `tasks.enabled`, `alerts.enabled`
  (`archive.enabled` stays `true`).
- **Docs updated** to reflect summer mode and to correct pre-existing drift: the `--mini`
  afternoon runs and `draft_on_demand.py` job were documented but never installed;
  `update_tasks.py`'s every-15-min job was installed but undocumented.
- **Restore:** uncomment the six crontab lines, revert the active line to the plain daily
  `fetch_and_triage.py`, and flip the three config flags back to `true`.

## 2026-04-02 — Unified Email Archive

Major redesign of the email archiving system. Replaces per-project subdirectories
with a single flat archive of all non-noise emails, queryable via YAML frontmatter,
Obsidian Bases, and a JSON index.

### New: `email_archiver.py`

Central module for all email archiving. Replaces the duplicate archiving logic
that previously lived in `project_fetch.py`.

- `EmailArchiver` class: `archive_email()`, `archive_batch()`, `regenerate_index()`
- `build_archive_frontmatter()`: rich YAML with category, priority, contacts, projects, tags
- `find_context_emails()`: searches archive by sender/thread for draft composition
- `generate_index()`: writes `_index.json` with frontmatter from all archived emails
- `matches_project()`, `find_matching_projects()`: project-matching logic (moved here)
- `load_archive_state()`, `save_archive_state()`: deduplication via `data/archive-state.json`

### New: `archive_cleanup.py`

Maintenance utility for the email archive:
- `--report`: category/project/age/contact breakdown, asset sizes
- `--deduplicate`: find and optionally remove duplicate archived emails
- `--prune-attachments`: preview/remove old large attachments (requires `--confirm`)

### New: `migrate_project_archive.py`

One-off migration script for moving project archives into the unified directory:
- Transforms old frontmatter (`project: "Name"`, `tags: [project-email]`) to new schema
- Copies assets, registers gmail_ids in archive-state.json
- `--source DIR --project-id ID` flags for migrating custom source directories
  (used for Wicked Problems migration from `Wicked Problems/email-threads/`)
- `--delete` flag to remove old directories after successful migration

### Changed: `fetch_and_triage.py`

- Added step 4c: archives non-noise emails via `EmailArchiver.archive_batch()` after classification
- Index regeneration (`_index.json`) happens automatically after each batch
- New `--backfill` mode for retroactive archiving with `--project`, `--contact`, `--keyword` filters
- Renamed `archive:` config key (Gmail noise archiving) to `gmail_archive:` to avoid collision

### Changed: `project_fetch.py`

- Removed all duplicate archiving functions (moved to `email_archiver.py`)
- Now imports from `email_archiver` and uses `EmailArchiver.archive_email()`
- Calls `regenerate_index()` after writing
- Used for manual retroactive backfill only; hourly cron removed

### Changed: `draft_on_demand.py`

- Replaced project-folder-based context loading with archive-based search
- Uses `find_context_emails()` to search by sender email and thread ID across the full archive

### Changed: `menu.py`

- Renamed "Project Archive" group to "Email Archive"
- Added "Archive report" and "Deduplicate archive (preview)" to Maintenance group

### Config changes

- `archive:` (old Gmail noise archiving) renamed to `gmail_archive:`
- New `archive:` section added:
  - `enabled`, `vault_folder`, `mail_folder`, `categories`, `attachments` config
  - `assets_folder` for attachment subdirectory
- Project `vault_folder` values updated to point to unified archive

### Obsidian vault changes

**New directory:** `inbox-emails/`
```
inbox-emails/
  CLAUDE.md              — agent instructions for working with the archive
  _index.json            — JSON index of all email frontmatter (auto-regenerated)
  _views/*.base          — 11 Obsidian Base views (all-emails, urgent, action, fyi, 7 projects)
  mail/*.md              — 644 archived emails with rich YAML frontmatter
  mail/assets/           — saved email attachments
```

**Embedded Base views:** Created `Email Overview.md` files in:
- `Wicked Problems/` (embeds `wicked-problems.base`)
- `Tolerance & Beliefs/` (embeds `tolerance-beliefs.base`)

**New skill:** `.claude/skills/email-context/` — vault-wide skill for agents to find
and use email context via the JSON index.

**Wicked Problems migration:** 170 emails + 23 assets migrated from
`Wicked Problems/email-threads/` to `inbox-emails/mail/`. Frontmatter transformed
to new schema. Old directory preserved as legacy.

### Instruction file updates

- `inbox-emails/CLAUDE.md`: documented `_index.json` and querying workflow
- `Wicked Problems/CLAUDE.md`: replaced email-threads section with unified archive instructions
- `Wicked Problems/AGENTS.md`, `GEMINI.md`: same updates as CLAUDE.md
- Vault root `CLAUDE.md`: added Email Archive section
- Project root `CLAUDE.md`: updated architecture diagram, added `_index.json` to conventions, added new entry points
- `README.md`: updated architecture diagram, project structure, cron schedule, script docs
- `config.example.yaml`: updated project section to reflect unified archive

### Migration path from per-project directories

The old `inbox-projects/` subdirectories were migrated in an earlier session using
`migrate_project_archive.py`. The Wicked Problems emails (stored separately in
`Wicked Problems/email-threads/`) were migrated today using the new `--source` flag.

To complete cleanup:
- Run `migrate_project_archive.py --delete` to remove old `inbox-projects/` subdirectories
- Remove the hourly `project_fetch.py` cron job if still present in crontab
- The `Wicked Problems/email-threads/` directory is preserved as legacy reference

### YAML frontmatter schema (new)

```yaml
date, subject, from, to, cc, thread_id, gmail_id    # core email fields
category: URGENT | ACTION | FYI                       # triage classification
priority: high | normal | low                         # priority level
inbox-projects: [project-id, ...]                     # project membership (list)
contacts: [email@example.com, ...]                    # all addresses involved
language: en | nl                                     # detected language
has_attachments: true | false
saved_attachments: ["file.pdf", ...]                  # filenames in assets/
task_extracted: true | false                           # whether a task was created
tags: [inbox-email, inbox-action, project-id, ...]   # Obsidian tags
```
