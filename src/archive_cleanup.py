#!/usr/bin/env python3
"""
Archive Cleanup — maintenance utility for the Obsidian email archive.

Provides reporting and maintenance tools for managing archive growth:
- Report on archive size, age distribution, and attachments
- Prune old/large attachments while keeping the markdown notes
- Remove duplicate emails (same gmail_id)
- Summarize old FYI emails to compress storage

All destructive operations require --confirm. Without it, actions are previewed only.

Usage:
    python src/archive_cleanup.py --report                    # Archive overview
    python src/archive_cleanup.py --prune-attachments --older-than 180 --min-size 5
    python src/archive_cleanup.py --deduplicate
    python src/archive_cleanup.py --summarize-fyi --older-than 90
"""

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        print("✗ config.yaml not found.")
        sys.exit(1)
    return yaml.safe_load(config_path.read_text())


def parse_frontmatter(path: Path) -> dict | None:
    """Quick YAML frontmatter extraction from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if not text.startswith("---"):
        return None
    end_idx = text.find("---", 3)
    if end_idx == -1:
        return None
    try:
        fm = yaml.safe_load(text[3:end_idx])
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def scan_archive(archive_dir: Path) -> list[tuple[Path, dict]]:
    """Scan all markdown files in the archive, returning (path, frontmatter) pairs."""
    results = []
    for path in sorted(archive_dir.glob("*.md")):
        fm = parse_frontmatter(path)
        if fm:
            results.append((path, fm))
    return results


def cmd_report(archive_dir: Path, assets_dir: Path):
    """Generate an overview report of the archive."""
    files = scan_archive(archive_dir)
    print(f"\n── Archive Report: {archive_dir}")
    print(f"   Total email files: {len(files)}")

    if not files:
        return

    # Category breakdown
    categories = Counter(fm.get("category", "unknown") for _, fm in files)
    print(f"\n   Categories:")
    for cat, count in categories.most_common():
        icon = {"URGENT": "⚡", "ACTION": "📋", "FYI": "🔵", "NOISE": "⚪"}.get(cat, "?")
        print(f"     {icon} {cat}: {count}")

    # Project breakdown
    projects = Counter()
    no_project = 0
    for _, fm in files:
        projs = fm.get("inbox-projects", [])
        if projs:
            for p in projs:
                projects[p] += 1
        else:
            no_project += 1
    if projects:
        print(f"\n   Projects:")
        for proj, count in projects.most_common():
            print(f"     📁 {proj}: {count}")
        if no_project:
            print(f"     (no project): {no_project}")

    # Age distribution
    today = datetime.now().date()
    age_buckets = {"< 7 days": 0, "7-30 days": 0, "30-90 days": 0,
                   "90-180 days": 0, "> 180 days": 0, "unknown": 0}
    for _, fm in files:
        date_str = fm.get("date", "")
        try:
            file_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            age = (today - file_date).days
            if age < 7:
                age_buckets["< 7 days"] += 1
            elif age < 30:
                age_buckets["7-30 days"] += 1
            elif age < 90:
                age_buckets["30-90 days"] += 1
            elif age < 180:
                age_buckets["90-180 days"] += 1
            else:
                age_buckets["> 180 days"] += 1
        except (ValueError, TypeError):
            age_buckets["unknown"] += 1
    print(f"\n   Age distribution:")
    for bucket, count in age_buckets.items():
        if count > 0:
            print(f"     {bucket}: {count}")

    # Markdown file sizes
    total_md_size = sum(p.stat().st_size for p, _ in files)
    print(f"\n   Total markdown size: {total_md_size / 1024 / 1024:.1f} MB")

    # Asset report
    if assets_dir.exists():
        asset_files = list(assets_dir.iterdir())
        asset_files = [f for f in asset_files if f.is_file()]
        total_asset_size = sum(f.stat().st_size for f in asset_files)
        print(f"\n   Assets:")
        print(f"     Files: {len(asset_files)}")
        print(f"     Total size: {total_asset_size / 1024 / 1024:.1f} MB")

        # Largest assets
        if asset_files:
            largest = sorted(asset_files, key=lambda f: f.stat().st_size, reverse=True)[:10]
            print(f"     Largest files:")
            for f in largest:
                size_mb = f.stat().st_size / 1024 / 1024
                print(f"       {f.name}: {size_mb:.1f} MB")

    # Contacts
    contacts = Counter()
    for _, fm in files:
        for c in fm.get("contacts", []):
            contacts[c] += 1
    if contacts:
        print(f"\n   Top contacts:")
        for contact, count in contacts.most_common(10):
            print(f"     {contact}: {count}")


def cmd_prune_attachments(
    archive_dir: Path, assets_dir: Path,
    older_than_days: int, min_size_mb: float, confirm: bool,
):
    """Delete attachments older than N days and larger than min_size_mb."""
    if not assets_dir.exists():
        print("No assets directory found.")
        return

    files = scan_archive(archive_dir)
    today = datetime.now().date()
    cutoff = today - timedelta(days=older_than_days)
    min_bytes = int(min_size_mb * 1024 * 1024)

    # Build set of attachment filenames referenced by old emails
    old_attachments = set()
    for path, fm in files:
        date_str = fm.get("date", "")
        try:
            file_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if file_date < cutoff:
            for att_name in fm.get("saved_attachments", []):
                old_attachments.add(att_name)

    # Find qualifying assets
    to_delete = []
    for asset_file in assets_dir.iterdir():
        if not asset_file.is_file():
            continue
        size = asset_file.stat().st_size
        if size >= min_bytes and asset_file.name in old_attachments:
            to_delete.append(asset_file)

    if not to_delete:
        print(f"No attachments match criteria (older than {older_than_days} days, "
              f"larger than {min_size_mb} MB).")
        return

    total_size = sum(f.stat().st_size for f in to_delete)
    print(f"\n── Would prune {len(to_delete)} attachment(s) "
          f"({total_size / 1024 / 1024:.1f} MB):")
    for f in sorted(to_delete, key=lambda x: x.stat().st_size, reverse=True):
        print(f"  {f.name}: {f.stat().st_size / 1024 / 1024:.1f} MB")

    if not confirm:
        print("\n  (Preview only. Add --confirm to delete.)")
        return

    deleted = 0
    for f in to_delete:
        try:
            f.unlink()
            deleted += 1
        except OSError as e:
            print(f"  ✗ Could not delete {f.name}: {e}")

    # Update saved_attachments in the markdown files
    for path, fm in files:
        saved = fm.get("saved_attachments", [])
        if not saved:
            continue
        deleted_names = {f.name for f in to_delete}
        new_saved = [n for n in saved if n not in deleted_names]
        if len(new_saved) != len(saved):
            # Rewrite the frontmatter
            text = path.read_text(encoding="utf-8")
            end_idx = text.find("---", 3)
            if end_idx > 0:
                fm["saved_attachments"] = new_saved if new_saved else []
                new_fm = "---\n" + yaml.dump(fm, allow_unicode=True, default_flow_style=False) + "---"
                body = text[end_idx + 3:]
                path.write_text(new_fm + body, encoding="utf-8")

    print(f"\n✓ Deleted {deleted} attachment(s).")


def cmd_deduplicate(archive_dir: Path, confirm: bool):
    """Find and remove emails with duplicate gmail_ids."""
    files = scan_archive(archive_dir)
    seen = {}  # gmail_id → first path
    duplicates = []

    for path, fm in files:
        gmail_id = fm.get("gmail_id", "")
        if not gmail_id:
            continue
        if gmail_id in seen:
            duplicates.append((path, gmail_id, seen[gmail_id]))
        else:
            seen[gmail_id] = path

    if not duplicates:
        print("No duplicate gmail_ids found.")
        return

    print(f"\n── Found {len(duplicates)} duplicate(s):")
    for dup_path, gmail_id, original_path in duplicates:
        print(f"  Duplicate: {dup_path.name}")
        print(f"    Original: {original_path.name}")
        print(f"    gmail_id: {gmail_id}")

    if not confirm:
        print("\n  (Preview only. Add --confirm to delete duplicates.)")
        return

    deleted = 0
    for dup_path, _, _ in duplicates:
        try:
            dup_path.unlink()
            deleted += 1
        except OSError as e:
            print(f"  ✗ Could not delete {dup_path.name}: {e}")

    print(f"\n✓ Deleted {deleted} duplicate(s).")


def main():
    parser = argparse.ArgumentParser(
        description="Archive cleanup and maintenance utility."
    )
    parser.add_argument("--report", action="store_true",
                        help="Show archive overview report")
    parser.add_argument("--prune-attachments", action="store_true",
                        help="Delete old, large attachments")
    parser.add_argument("--deduplicate", action="store_true",
                        help="Remove duplicate emails (same gmail_id)")
    parser.add_argument("--older-than", type=int, default=180,
                        help="Age threshold in days (default: 180)")
    parser.add_argument("--min-size", type=float, default=5.0,
                        help="Minimum attachment size in MB to prune (default: 5)")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually perform destructive operations (required)")
    args = parser.parse_args()

    config = load_config()
    obsidian_cfg = config.get("obsidian", {})
    vault_path = Path(obsidian_cfg.get("vault_path", "")).expanduser()
    archive_cfg = config.get("archive", {})
    archive_base = vault_path / archive_cfg.get("vault_folder", "inbox-emails")
    mail_folder = archive_cfg.get("mail_folder", "mail")
    archive_dir = archive_base / mail_folder
    assets_dir = archive_dir / archive_cfg.get("assets_folder", "assets")

    if not archive_dir.exists():
        print(f"✗ Archive directory not found: {archive_dir}")
        print("  Run the migration first or check archive.vault_folder in config.yaml.")
        sys.exit(1)

    if not any([args.report, args.prune_attachments, args.deduplicate]):
        args.report = True  # default action

    if args.report:
        cmd_report(archive_dir, assets_dir)

    if args.deduplicate:
        cmd_deduplicate(archive_dir, confirm=args.confirm)

    if args.prune_attachments:
        cmd_prune_attachments(
            archive_dir, assets_dir,
            older_than_days=args.older_than,
            min_size_mb=args.min_size,
            confirm=args.confirm,
        )

    print("\n✓ Done.")


if __name__ == "__main__":
    main()
