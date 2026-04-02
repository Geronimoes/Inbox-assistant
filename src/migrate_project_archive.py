#!/usr/bin/env python3
"""
Migration script — moves project email archives into the unified inbox-emails/ directory.

Reads existing markdown files from inbox-projects/{project}/, transforms their
YAML frontmatter to the new schema, copies them to inbox-emails/, and copies
asset files. Also registers migrated gmail_ids in archive-state.json so they
won't be re-archived by fetch_and_triage.py.

Usage:
    python src/migrate_project_archive.py --dry-run    # Preview (recommended first!)
    python src/migrate_project_archive.py              # Execute migration
    python src/migrate_project_archive.py --delete     # Execute + delete old directories

The migration is idempotent — files already in inbox-emails/ are skipped.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from email_archiver import (
    extract_all_contacts,
    load_archive_state,
    save_archive_state,
)


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body_text). Returns (None, full_text) if
    no valid frontmatter is found.
    """
    if not text.startswith("---"):
        return None, text

    end_idx = text.find("---", 3)
    if end_idx == -1:
        return None, text

    fm_text = text[3:end_idx]
    body = text[end_idx + 3:].lstrip("\n")

    try:
        fm = yaml.safe_load(fm_text)
        if not isinstance(fm, dict):
            return None, text
        return fm, body
    except yaml.YAMLError:
        return None, text


def transform_frontmatter(fm: dict, project_id: str) -> dict:
    """Transform old project-archive frontmatter to the new unified schema.

    Old format:
        project: "MaRBLe Undergraduate Research"
        tags: [project-email, marble]

    New format:
        category: ACTION
        priority: normal
        inbox-projects: [marble]
        contacts: [email@example.com]
        language: en
        has_attachments: false
        saved_attachments: []
        task_extracted: false
        tags: [inbox-email, inbox-action, marble]
    """
    new_fm = {}

    # Preserve core fields
    for key in ("date", "subject", "from", "to", "cc", "thread_id", "gmail_id"):
        if key in fm:
            new_fm[key] = fm[key]

    # Transform project → inbox-projects
    new_fm["category"] = "ACTION"  # default — we don't have original classification
    new_fm["priority"] = "normal"
    new_fm["inbox-projects"] = [project_id]

    # Extract contacts from from/to/cc
    # Build a fake email dict for extract_all_contacts
    email_like = {
        "from": fm.get("from", ""),
        "to": fm.get("to", ""),
        "cc": fm.get("cc", ""),
    }
    new_fm["contacts"] = extract_all_contacts(email_like)

    # Detect language from body or default to English
    new_fm["language"] = "en"

    # Attachment fields
    new_fm["has_attachments"] = False
    new_fm["task_extracted"] = False

    # Build new tags
    tags = ["inbox-email", "inbox-action", project_id]
    new_fm["tags"] = tags

    return new_fm


def main():
    parser = argparse.ArgumentParser(
        description="Migrate project email archives to unified inbox-emails/ directory."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview migration without copying files.",
    )
    parser.add_argument(
        "--delete", action="store_true",
        help="Delete old project directories after successful migration.",
    )
    parser.add_argument(
        "--source", type=str,
        help="Migrate from a specific directory (relative to vault root) instead of inbox-projects/.",
    )
    parser.add_argument(
        "--project-id", type=str,
        help="Project ID to use when --source is specified (must match a project in config.yaml).",
    )
    args = parser.parse_args()

    if args.source and not args.project_id:
        print("✗ --project-id is required when using --source.")
        sys.exit(1)

    # ── Load config ──────────────────────────────────────────────────────────
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        print("✗ config.yaml not found.")
        sys.exit(1)
    config = yaml.safe_load(config_path.read_text())

    # ── Resolve paths ────────────────────────────────────────────────────────
    vault_path = Path(config["obsidian"]["vault_path"]).expanduser()
    archive_cfg = config.get("archive", {})
    mail_folder = archive_cfg.get("mail_folder", "mail")
    dest_dir = vault_path / archive_cfg.get("vault_folder", "inbox-emails") / mail_folder
    assets_dest = dest_dir / archive_cfg.get("assets_folder", "assets")

    project_root = Path(__file__).parent.parent
    data_dir = project_root / "data"

    # Build directory name → config project ID mapping.
    projects_cfg = config.get("projects", [])
    dir_to_project_id = {}
    for p in projects_cfg:
        vault_folder = p.get("vault_folder", "")
        dir_name = Path(vault_folder).name
        dir_to_project_id[dir_name] = p["id"]

    # ── Discover source directories ──────────────────────────────────────────
    if args.source:
        # Single custom source directory
        source_dir = vault_path / args.source
        if not source_dir.exists():
            print(f"✗ Source directory not found: {source_dir}")
            sys.exit(1)
        project_dirs = [source_dir]
        dir_to_project_id[source_dir.name] = args.project_id
        print(f"Migrating custom source: {args.source} → {args.project_id}")
        count = len(list(source_dir.glob("*.md")))
        print(f"  {count} markdown files found")
    else:
        source_root = vault_path / "inbox-projects"
        if not source_root.exists():
            print(f"✗ Source directory not found: {source_root}")
            sys.exit(1)

        # Skip hidden dirs, CLAUDE.md, _index.md, style-profile.md
        skip_names = {"CLAUDE.md", "_index.md", "style-profile.md", ".claude"}
        project_dirs = [
            d for d in sorted(source_root.iterdir())
            if d.is_dir() and d.name not in skip_names and not d.name.startswith(".")
        ]

        if not project_dirs:
            print("No project directories found to migrate.")
            return

        print(f"Found {len(project_dirs)} project directories to migrate:")
        for d in project_dirs:
            count = len(list(d.glob("*.md")))
            mapped_id = dir_to_project_id.get(d.name, d.name)
            print(f"  {d.name} → {mapped_id}: {count} files")

    # ── Load archive state (to register migrated IDs) ────────────────────────
    archived_ids = load_archive_state(data_dir)

    # ── Process each project ─────────────────────────────────────────────────
    total_files = 0
    total_assets = 0
    total_skipped = 0
    migrated_gmail_ids = set()

    for project_dir in project_dirs:
        project_id = dir_to_project_id.get(project_dir.name, project_dir.name)
        print(f"\n── Migrating: {project_dir.name} → {project_id}")

        md_files = sorted(project_dir.glob("*.md"))
        source_assets = project_dir / "assets"

        for md_file in md_files:
            # Read and parse
            try:
                text = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                print(f"  ⚠ Cannot read {md_file.name}: {e}")
                continue

            fm, body = parse_frontmatter(text)

            # Check if this file already exists in destination
            dest_file = dest_dir / md_file.name
            if dest_file.exists():
                total_skipped += 1
                continue

            if fm:
                # Transform frontmatter
                new_fm = transform_frontmatter(fm, project_id)

                # Check for attachment references in body
                if "](assets/" in body:
                    new_fm["has_attachments"] = True
                    # Extract saved attachment filenames
                    att_refs = re.findall(r'\[([^\]]+)\]\(assets/[^)]+\)', body)
                    if att_refs:
                        new_fm["saved_attachments"] = att_refs

                # Rebuild the file
                fm_text = yaml.dump(new_fm, allow_unicode=True, default_flow_style=False)
                new_content = "---\n" + fm_text + "---\n\n" + body
            else:
                # No frontmatter — keep as-is (shouldn't happen for project archives)
                new_content = text

            # Track gmail_id
            if fm and fm.get("gmail_id"):
                migrated_gmail_ids.add(fm["gmail_id"])

            if args.dry_run:
                category = "ACTION"
                projects_tag = project_id
                print(f"  [DRY RUN] {md_file.name} → inbox-emails/ "
                      f"[{category}, project:{projects_tag}]")
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                tmp = dest_file.with_suffix(".tmp")
                tmp.write_text(new_content, encoding="utf-8")
                tmp.replace(dest_file)

            total_files += 1

        # Copy assets directory
        if source_assets.exists() and source_assets.is_dir():
            asset_files = list(source_assets.iterdir())
            for asset_file in asset_files:
                if not asset_file.is_file():
                    continue
                dest_asset = assets_dest / asset_file.name
                if dest_asset.exists():
                    continue  # skip existing

                if args.dry_run:
                    size_kb = asset_file.stat().st_size / 1024
                    print(f"  [DRY RUN] asset: {asset_file.name} ({size_kb:.0f} KB)")
                else:
                    assets_dest.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(asset_file, dest_asset)

                total_assets += 1

    # ── Update archive state ─────────────────────────────────────────────────
    if not args.dry_run and migrated_gmail_ids:
        archived_ids |= migrated_gmail_ids
        save_archive_state(data_dir, archived_ids)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n── Migration summary:")
    print(f"  {total_files} email files {'would be ' if args.dry_run else ''}migrated")
    print(f"  {total_assets} asset files {'would be ' if args.dry_run else ''}copied")
    print(f"  {total_skipped} files skipped (already in destination)")
    print(f"  {len(migrated_gmail_ids)} gmail_ids registered in archive state")

    if args.dry_run:
        print("\n  (Dry run — no files were modified.)")
        return

    # ── Delete old directories (if --delete flag) ────────────────────────────
    if args.delete:
        print("\n── Deleting old project directories...")
        for project_dir in project_dirs:
            try:
                shutil.rmtree(project_dir)
                print(f"  Deleted: {project_dir.name}/")
            except OSError as e:
                print(f"  ⚠ Could not delete {project_dir.name}: {e}")
        print("  Old directories removed.")
        print("  Note: inbox-projects/CLAUDE.md, _index.md, and .claude/ were preserved.")
    else:
        print("\n  Old directories were preserved. Run with --delete to remove them.")

    print("\n✓ Migration complete!")


if __name__ == "__main__":
    main()
