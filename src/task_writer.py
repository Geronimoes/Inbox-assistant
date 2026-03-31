"""
Task writer — extracts tasks from email classifications and writes them
to Obsidian as a structured TASKS.md file with optional per-project and
complex-task detail files.

The master file (TASKS.md) lives in the Obsidian vault root. Detail files
go into a Tasks/ subdirectory. All writes are append-only for new tasks —
manually checked-off items are preserved and moved to a "Recently Completed"
section.

Configuration (config.yaml):
    tasks:
      enabled: true
      obsidian_file: "TASKS.md"
      detail_folder: "Tasks"
      include_in_briefing: true
      completed_retention_days: 7
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


# ── Template for a new TASKS.md ──────────────────────────────────────────────

_TASKS_TEMPLATE = """\
---
tags:
  - inbox-tasks
last_updated: {date}
---

# Inbox Tasks

## ⚡ Urgent

_No urgent tasks._

## 📋 Action Required

_No action tasks._

## ✅ Recently Completed

"""

# ── Template for a project detail file ────────────────────────────────────────

_PROJECT_TEMPLATE = """\
---
tags:
  - inbox-task-detail
  - project/{project_id}
project: {project_id}
last_updated: {date}
---

# {project_name} Tasks

Active tasks for [[{project_name}]].

"""

# ── Template for a complex task detail file ───────────────────────────────────

_COMPLEX_TEMPLATE = """\
---
tags:
  - inbox-task-detail
source_briefing: "[[{date}]]"
project: {project}
created: {date}
---

# {title}

**Source:** {source}
**Deadline:** {deadline}
**Estimated time:** {time_est}

## Steps

{steps}
"""


class TaskWriter:
    """Extract tasks from classifications and write to Obsidian vault."""

    def __init__(self, config: dict, llm_client=None):
        """
        Args:
            config:     Full config dict. Reads obsidian.vault_path and tasks.*.
            llm_client: Optional LLMClient for generating complex task breakdowns.
        """
        self.llm = llm_client
        self.timezone = ZoneInfo(
            config.get("briefing", {}).get("timezone", "Europe/Amsterdam")
        )

        obsidian_cfg = config.get("obsidian", {})
        self.vault_path = Path(obsidian_cfg.get("vault_path", "")).expanduser()

        tasks_cfg = config.get("tasks", {})
        self.tasks_file = tasks_cfg.get("obsidian_file", "TASKS.md")
        self.detail_folder = tasks_cfg.get("detail_folder", "Tasks")
        self.retention_days = tasks_cfg.get("completed_retention_days", 7)

        # Build project lookup from config
        self.projects = {
            p["id"]: p for p in config.get("projects", [])
        }

    def extract_tasks(self, classifications: list[dict]) -> list[dict]:
        """Pull task dicts from classification results.

        Returns a flat list of task dicts, each enriched with:
            - source_date:  today's date string (YYYY-MM-DD)
            - source_from:  email sender (from classification)
            - email_id:     originating email ID
            - category:     URGENT or ACTION
        """
        now = datetime.now(self.timezone)
        date_str = now.strftime("%Y-%m-%d")

        all_tasks = []
        for cls in classifications:
            category = cls.get("category", "")
            if category not in ("URGENT", "ACTION"):
                continue

            tasks = cls.get("tasks", [])
            if not tasks:
                continue

            for task in tasks:
                task["source_date"] = date_str
                task["email_id"] = cls.get("email_id", "")
                task["category"] = category
                # Use the classification summary as source context
                task["source_summary"] = cls.get("summary", "")
                all_tasks.append(task)

        return all_tasks

    def write_tasks(self, tasks: list[dict]) -> int:
        """Write new tasks to the Obsidian vault.

        Appends tasks to TASKS.md, creates/updates project detail files,
        and generates complex task breakdowns as needed.

        Returns the number of tasks actually written (after dedup).
        """
        if not self.vault_path.exists():
            print(f"  ⚠ Vault path does not exist: {self.vault_path}")
            return 0

        tasks_path = self.vault_path / self.tasks_file
        detail_dir = self.vault_path / self.detail_folder
        detail_dir.mkdir(parents=True, exist_ok=True)

        # Read or create TASKS.md
        if tasks_path.exists():
            content = tasks_path.read_text(encoding="utf-8")
        else:
            now = datetime.now(self.timezone)
            content = _TASKS_TEMPLATE.format(date=now.strftime("%Y-%m-%d"))

        # Parse into sections
        urgent_section, action_section, completed_section, content = (
            self._parse_sections(content)
        )

        # Housekeeping: move checked tasks to completed, prune old completed
        urgent_section, action_section, completed_section = (
            self._housekeep(urgent_section, action_section, completed_section)
        )

        # Deduplicate: check which tasks already exist
        existing_text = urgent_section + action_section
        written = 0

        # Group tasks by project for detail files
        project_tasks: dict[str, list[dict]] = {}

        for task in tasks:
            desc = task.get("description", "")
            date = task.get("source_date", "")

            # Simple dedup: skip if description appears in existing content
            if desc and desc in existing_text:
                continue

            # Format the task line
            task_line = self._format_task(task)

            # Add to the correct section
            if task.get("category") == "URGENT":
                urgent_section = self._append_to_section(
                    urgent_section, task_line
                )
            else:
                action_section = self._append_to_section(
                    action_section, task_line
                )

            # Track for project detail files
            project_id = task.get("project")
            if project_id and project_id in self.projects:
                project_tasks.setdefault(project_id, []).append(task)

            # Generate complex task breakdown
            if task.get("complexity") == "complex":
                self._write_complex_detail(task, detail_dir)

            written += 1

        # Reassemble and write TASKS.md
        now = datetime.now(self.timezone)
        new_content = self._reassemble(
            now.strftime("%Y-%m-%d"),
            urgent_section, action_section, completed_section
        )
        self._atomic_write(tasks_path, new_content)

        # Update project detail files
        for project_id, proj_tasks in project_tasks.items():
            self._update_project_file(project_id, proj_tasks, detail_dir)

        return written

    def get_task_summary(self, tasks: list[dict]) -> list[dict]:
        """Return a summary of tasks suitable for inclusion in the briefing.

        Each item has: description, deadline, time_estimate_minutes, category,
        project.
        """
        return [
            {
                "description": t.get("description", ""),
                "deadline": t.get("deadline"),
                "time_estimate_minutes": t.get("time_estimate_minutes"),
                "category": t.get("category", "ACTION"),
                "project": t.get("project"),
                "task_type": t.get("task_type", "implicit"),
            }
            for t in tasks
        ]

    # ── Section parsing ───────────────────────────────────────────────────────

    def _parse_sections(self, content: str) -> tuple[str, str, str, str]:
        """Parse TASKS.md into urgent, action, and completed section bodies.

        Returns (urgent_body, action_body, completed_body, full_content).
        Section bodies include the lines between their heading and the next heading.
        """
        # Find section boundaries by heading
        urgent_match = re.search(r'^## ⚡ Urgent\s*$', content, re.MULTILINE)
        action_match = re.search(r'^## 📋 Action Required\s*$', content, re.MULTILINE)
        completed_match = re.search(r'^## ✅ Recently Completed\s*$', content, re.MULTILINE)

        def _extract(start_match, end_match):
            if not start_match:
                return ""
            start = start_match.end()
            end = end_match.start() if end_match else len(content)
            return content[start:end].strip()

        urgent_body = _extract(urgent_match, action_match) if urgent_match else ""
        action_body = _extract(action_match, completed_match) if action_match else ""
        completed_body = _extract(completed_match, None) if completed_match else ""

        return urgent_body, action_body, completed_body, content

    def _housekeep(self, urgent: str, action: str,
                   completed: str) -> tuple[str, str, str]:
        """Move checked tasks [x] to completed, prune old completed items."""
        now = datetime.now(self.timezone)
        date_str = now.strftime("%Y-%m-%d")

        # Find checked tasks in urgent and action sections
        checked_lines = []
        urgent = self._extract_checked(urgent, checked_lines)
        action = self._extract_checked(action, checked_lines)

        # Add newly completed tasks with completion date
        for line in checked_lines:
            completed += f"\n{line} _(completed {date_str})_"

        # Prune completed tasks older than retention period
        cutoff = now - timedelta(days=self.retention_days)
        if completed:
            completed = self._prune_old_completed(completed, cutoff)

        return urgent, action, completed

    def _extract_checked(self, section: str,
                         checked_out: list[str]) -> str:
        """Remove lines starting with '- [x]' from section, append to checked_out.

        Also removes indented sub-lines that follow a checked task.
        Returns the section with checked items removed.
        """
        if not section:
            return section

        lines = section.split("\n")
        result = []
        skip_indent = False

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
                checked_out.append(stripped)
                skip_indent = True
                continue

            # Skip indented sub-items of a checked task
            if skip_indent and stripped and (line.startswith("  ") or line.startswith("\t")):
                continue

            skip_indent = False
            result.append(line)

        return "\n".join(result)

    def _prune_old_completed(self, completed: str,
                             cutoff: datetime) -> str:
        """Remove completed tasks older than the cutoff date."""
        lines = completed.split("\n")
        result = []

        for line in lines:
            # Look for _(completed YYYY-MM-DD)_ pattern
            match = re.search(r'\(completed (\d{4}-\d{2}-\d{2})\)', line)
            if match:
                try:
                    completed_date = datetime.strptime(
                        match.group(1), "%Y-%m-%d"
                    ).replace(tzinfo=self.timezone)
                    if completed_date < cutoff:
                        continue  # Skip old completed tasks
                except ValueError:
                    pass
            result.append(line)

        return "\n".join(result)

    # ── Task formatting ───────────────────────────────────────────────────────

    def _format_task(self, task: dict) -> str:
        """Format a single task as Markdown checkbox lines."""
        desc = task.get("description", "Unknown task")
        deadline = task.get("deadline")
        time_est = task.get("time_estimate_minutes")
        project_id = task.get("project")
        source_date = task.get("source_date", "")
        source_summary = task.get("source_summary", "")
        task_type = task.get("task_type", "implicit")
        complexity = task.get("complexity", "simple")

        # Main line: - [ ] Description — _due date_ ⏱ N min
        parts = [f"- [ ] {desc}"]
        if deadline:
            parts.append(f"— _due {deadline}_")
        if time_est:
            parts.append(f"⏱ {time_est} min")

        main_line = " ".join(parts)

        # Sub-lines with metadata
        sub_lines = []
        if source_date:
            from_text = f"From: [[{source_date}]]"
            if source_summary:
                from_text += f" — {source_summary}"
            sub_lines.append(f"  - {from_text}")

        if project_id and project_id in self.projects:
            proj_name = self.projects[project_id].get("name", project_id)
            sub_lines.append(f"  - Project: [[{proj_name}|{project_id}]]")

        # Link to complex detail file
        if complexity == "complex":
            slug = self._slugify(desc)
            detail_name = f"{self.detail_folder}/{slug}"
            sub_lines.append(f"  - [[{detail_name}|Step-by-step breakdown →]]")

        return main_line + "\n" + "\n".join(sub_lines) if sub_lines else main_line

    def _append_to_section(self, section: str, task_block: str) -> str:
        """Append a task block to a section, removing placeholder text."""
        # Remove placeholder lines like "_No urgent tasks._"
        section = re.sub(r'^_No \w+ tasks?\._\s*$', '', section,
                         flags=re.MULTILINE).strip()
        if section:
            return section + "\n\n" + task_block
        return task_block

    def _reassemble(self, date_str: str, urgent: str, action: str,
                    completed: str) -> str:
        """Reassemble the full TASKS.md content from sections."""
        lines = [
            "---",
            "tags:",
            "  - inbox-tasks",
            f"last_updated: {date_str}",
            "---",
            "",
            "# Inbox Tasks",
            "",
            "## ⚡ Urgent",
            "",
            urgent.strip() if urgent.strip() else "_No urgent tasks._",
            "",
            "## 📋 Action Required",
            "",
            action.strip() if action.strip() else "_No action tasks._",
            "",
            "## ✅ Recently Completed",
            "",
            completed.strip() if completed.strip() else "",
            "",
        ]
        return "\n".join(lines)

    # ── Detail file generation ────────────────────────────────────────────────

    def _write_complex_detail(self, task: dict, detail_dir: Path) -> None:
        """Generate a detail file for a complex task with step breakdown."""
        desc = task.get("description", "")
        slug = self._slugify(desc)
        detail_path = detail_dir / f"{slug}.md"

        # Don't overwrite existing detail files (may have manual edits)
        if detail_path.exists():
            return

        deadline = task.get("deadline", "none")
        time_est = task.get("time_estimate_minutes")
        time_str = f"{time_est} min" if time_est else "varies"
        project_id = task.get("project")
        source_date = task.get("source_date", "")
        source_summary = task.get("source_summary", "")

        # Generate step breakdown via LLM if available
        steps = self._generate_steps(task)

        content = _COMPLEX_TEMPLATE.format(
            date=source_date,
            project=project_id or "null",
            title=desc,
            source=f"Email, {source_date} — {source_summary}" if source_summary else f"Email, {source_date}",
            deadline=deadline or "none",
            time_est=time_str,
            steps=steps,
        )

        self._atomic_write(detail_path, content)
        print(f"  ✓ Complex task detail: {detail_path.name}")

    def _generate_steps(self, task: dict) -> str:
        """Generate step-by-step breakdown for a complex task.

        Uses Haiku via LLM client if available, otherwise returns a placeholder.
        """
        if not self.llm:
            return "1. [Review the email for details]\n2. [Complete the task]\n3. [Follow up if needed]"

        desc = task.get("description", "")
        source = task.get("source_summary", "")
        deadline = task.get("deadline", "")

        prompt = (
            f"Break this task into 3-6 concrete steps for a university professor. "
            f"Be specific and actionable. Return only a numbered list, no other text.\n\n"
            f"Task: {desc}\n"
            f"Context: {source}\n"
        )
        if deadline:
            prompt += f"Deadline: {deadline}\n"

        try:
            result = self.llm.complete(
                "summarization",  # Use Haiku for cheap step generation
                system_prompt="You are a task planning assistant. Return only a numbered list of steps.",
                user_message=prompt,
                max_tokens=300,
            )
            return result.strip()
        except Exception as e:
            print(f"  ⚠ Could not generate task steps: {e}")
            return "1. [Review the email for details]\n2. [Complete the task]\n3. [Follow up if needed]"

    def _update_project_file(self, project_id: str,
                             tasks: list[dict],
                             detail_dir: Path) -> None:
        """Create or update a per-project task file in the detail folder."""
        project = self.projects.get(project_id, {})
        project_name = project.get("name", project_id)
        file_path = detail_dir / f"{project_id}.md"

        now = datetime.now(self.timezone)
        date_str = now.strftime("%Y-%m-%d")

        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            # Update last_updated in frontmatter
            content = re.sub(
                r'last_updated: \d{4}-\d{2}-\d{2}',
                f'last_updated: {date_str}',
                content
            )
        else:
            content = _PROJECT_TEMPLATE.format(
                project_id=project_id,
                project_name=project_name,
                date=date_str,
            )

        # Append new tasks (dedup by checking description)
        for task in tasks:
            desc = task.get("description", "")
            if desc and desc in content:
                continue

            deadline = task.get("deadline")
            time_est = task.get("time_estimate_minutes")
            source_date = task.get("source_date", "")

            parts = [f"- [ ] {desc}"]
            if deadline:
                parts.append(f"— _due {deadline}_")
            if time_est:
                parts.append(f"⏱ {time_est} min")
            main_line = " ".join(parts)
            sub_line = f"  - [[{source_date}]] — see briefing for full context"

            content = content.rstrip() + f"\n{main_line}\n{sub_line}\n"

        self._atomic_write(file_path, content)

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to a filesystem-safe slug for filenames."""
        slug = text.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = slug.strip('-')
        return slug[:60]  # Cap length

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write file atomically via .tmp → rename."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
