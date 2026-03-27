"""
Morning briefing generator.

Takes classified emails and draft replies, and produces a formatted
HTML email that serves as the daily inbox briefing.
"""

from datetime import datetime
from zoneinfo import ZoneInfo


class BriefingGenerator:
    """Generate a formatted morning briefing email."""

    def __init__(self, timezone: str = "Europe/Amsterdam",
                 max_fyi_items: int = 10,
                 show_noise_count: bool = True):
        self.timezone = ZoneInfo(timezone)
        self.max_fyi_items = max_fyi_items
        self.show_noise_count = show_noise_count

    def generate(self, classifications: list[dict],
                 drafts: list[dict],
                 attachment_summaries: dict | None = None) -> tuple[str, str]:
        """Generate the briefing email.

        Args:
            classifications:     Output of EmailClassifier.classify_batch().
            drafts:              Output of DraftComposer.compose_batch().
            attachment_summaries: Optional dict mapping email_id → list of
                                  attachment summary dicts from AttachmentHandler.

        Returns (subject, html_body).
        """
        now = datetime.now(self.timezone)
        date_str = now.strftime("%A %-d %B %Y")

        urgent = [c for c in classifications if c["category"] == "URGENT"]
        action = [c for c in classifications if c["category"] == "ACTION"]
        fyi = [c for c in classifications if c["category"] == "FYI"]
        noise = [c for c in classifications if c["category"] == "NOISE"]

        draft_lookup = {d["email_id"]: d for d in drafts}
        att_lookup = attachment_summaries or {}

        subject = self._make_subject(date_str, len(urgent), len(action))
        html = self._render_html(
            date_str, urgent, action, fyi, noise, draft_lookup, att_lookup
        )

        return subject, html

    def _make_subject(self, date_str: str,
                      urgent_count: int, action_count: int) -> str:
        """Create a descriptive subject line."""
        parts = []
        if urgent_count:
            parts.append(f"⚡ {urgent_count} urgent")
        if action_count:
            parts.append(f"{action_count} to reply")
        
        if parts:
            return f"📬 Inbox Briefing — {', '.join(parts)} — {date_str}"
        return f"📬 Inbox Briefing — All clear — {date_str}"

    def _render_html(self, date_str: str,
                     urgent: list, action: list, fyi: list, noise: list,
                     draft_lookup: dict, att_lookup: dict | None = None) -> str:
        """Render the briefing as HTML email."""
        sections = []

        # Header
        sections.append(f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 
                     Roboto, sans-serif; max-width: 600px; margin: 0 auto; 
                     color: #333; line-height: 1.5;">
        <h1 style="font-size: 20px; border-bottom: 2px solid #2563eb; 
                   padding-bottom: 8px; color: #1e293b;">
            📬 Inbox Briefing — {date_str}
        </h1>
        <p style="color: #64748b; font-size: 14px;">
            {len(urgent)} urgent · {len(action)} need reply · 
            {len(fyi)} FYI · {len(noise)} noise
        </p>
        """)

        # Urgent section
        att_lookup = att_lookup or {}

        if urgent:
            sections.append(self._render_section(
                "⚡ Needs attention today", urgent, draft_lookup,
                color="#dc2626", bg="#fef2f2", att_lookup=att_lookup
            ))

        # Action section
        if action:
            sections.append(self._render_section(
                "📋 Reply needed (not urgent)", action, draft_lookup,
                color="#d97706", bg="#fffbeb", att_lookup=att_lookup
            ))

        # FYI section
        if fyi:
            display_fyi = fyi[:self.max_fyi_items]
            remaining = len(fyi) - len(display_fyi)

            sections.append(self._render_section(
                "🔵 For your information", display_fyi, draft_lookup,
                color="#2563eb", bg="#eff6ff", compact=True,
                att_lookup=att_lookup
            ))
            if remaining > 0:
                sections.append(
                    f'<p style="color: #94a3b8; font-size: 13px;">'
                    f'  ...and {remaining} more FYI items</p>'
                )

        # Noise summary
        if noise and self.show_noise_count:
            sections.append(f"""
            <div style="background: #f8fafc; border-radius: 6px; 
                        padding: 12px 16px; margin-top: 16px;">
                <p style="color: #94a3b8; font-size: 13px; margin: 0;">
                    ⚪ <strong>{len(noise)} noise items</strong> — 
                    newsletters, notifications, promotions
                </p>
            </div>
            """)

        # Footer
        sections.append("""
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin-top: 24px;">
        <p style="color: #94a3b8; font-size: 12px;">
            Generated by Inbox Briefing Assistant. 
            Draft replies are saved in your Gmail Drafts folder — 
            review before sending.
        </p>
        </div>
        """)

        return "\n".join(sections)

    def _render_section(self, title: str, items: list,
                        draft_lookup: dict,
                        color: str, bg: str,
                        compact: bool = False,
                        att_lookup: dict | None = None) -> str:
        """Render a single section of the briefing."""
        html = f"""
        <div style="margin-top: 20px;">
            <h2 style="font-size: 16px; color: {color}; margin-bottom: 8px;">
                {title}
            </h2>
        """

        att_lookup = att_lookup or {}

        for item in items:
            email_id = item.get("email_id", "")
            has_draft = email_id in draft_lookup
            draft_badge = (
                ' <span style="background: #dcfce7; color: #166534; '
                'font-size: 11px; padding: 2px 6px; border-radius: 3px;">'
                '✎ Draft ready</span>'
                if has_draft else ""
            )

            deadline_badge = ""
            if item.get("deadline"):
                deadline_badge = (
                    f' <span style="background: #fef3c7; color: #92400e; '
                    f'font-size: 11px; padding: 2px 6px; border-radius: 3px;">'
                    f'⏰ {item["deadline"]}</span>'
                )

            # Attachment summaries for this email
            att_html = ""
            attachments = att_lookup.get(email_id, [])
            if attachments:
                att_lines = []
                for att in attachments:
                    icon = {"paper": "📄", "submission": "📝", "form": "📋",
                            "invoice": "🧾", "calendar": "📅",
                            "document": "📃"}.get(att.get("category", ""), "📎")
                    att_lines.append(
                        f'{icon} <em>{att.get("summary", att.get("filename", ""))}</em>'
                    )
                att_html = (
                    '<div style="font-size: 12px; color: #64748b; '
                    'margin-top: 4px; padding-top: 4px; '
                    'border-top: 1px dashed #e2e8f0;">'
                    + " &nbsp;·&nbsp; ".join(att_lines)
                    + "</div>"
                )

            if compact:
                html += f"""
                <div style="padding: 6px 0; border-bottom: 1px solid #f1f5f9;">
                    <strong style="font-size: 14px;">{item.get('summary', '')}</strong>
                </div>
                """
            else:
                html += f"""
                <div style="background: {bg}; border-radius: 6px;
                            padding: 12px 16px; margin-bottom: 8px;
                            border-left: 3px solid {color};">
                    <div style="font-size: 14px; font-weight: 600;">
                        {item.get('summary', '')} {draft_badge} {deadline_badge}
                    </div>
                    <div style="font-size: 13px; color: #64748b; margin-top: 4px;">
                        {item.get('suggested_action', '')}
                    </div>
                    {att_html}
                </div>
                """

        html += "</div>"
        return html
