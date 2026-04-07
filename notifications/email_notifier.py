"""Email notification with HTML template using Jinja2 + smtplib."""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from scrapers.base import ClassifiedListing
from utils.logging import get_logger


class EmailNotifier:
    def __init__(self, config: dict) -> None:
        self.log = get_logger("notifications.email")
        email_cfg = config.get("email", {})
        self.smtp_host = email_cfg.get("smtp_host", "smtp.gmail.com")
        self.smtp_port = email_cfg.get("smtp_port", 587)
        self.sender = os.environ.get("SMTP_SENDER", "")
        self.password = os.environ.get("SMTP_PASSWORD", "")
        self.recipient = os.environ.get("SMTP_RECIPIENT", "")
        self.threshold = config.get("price_threshold_brl", 600.0)

        templates_dir = Path(__file__).parent.parent / "templates"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=True,
        )

    def send(
        self,
        matches: list[ClassifiedListing],
        scraper_status: dict[str, str],
    ) -> None:
        if not matches:
            self.log.info("No matches to send.")
            return

        if not all([self.sender, self.password, self.recipient]):
            self.log.error(
                "Email credentials not configured. Set SMTP_SENDER, "
                "SMTP_PASSWORD, and SMTP_RECIPIENT in .env"
            )
            return

        subject = f"DDR5 CL30 Alert: {len(matches)} deal(s) found below R${self.threshold:.2f}"
        html_body = self._render_html(matches, scraper_status)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = self.recipient

        plain_text = self._render_plain(matches, scraper_status)
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.sender, self.password)
                server.send_message(msg)
            self.log.info(f"Email sent to {self.recipient}")
        except Exception as exc:
            self.log.error(f"Failed to send email: {exc}")
            raise

    def _render_html(
        self,
        matches: list[ClassifiedListing],
        scraper_status: dict[str, str],
    ) -> str:
        try:
            template = self.jinja_env.get_template("email.html")
            return template.render(
                matches=matches,
                scraper_status=scraper_status,
                threshold=self.threshold,
                match_count=len(matches),
            )
        except Exception as exc:
            self.log.warning(f"Template rendering failed, using fallback: {exc}")
            return self._fallback_html(matches, scraper_status)

    def _fallback_html(
        self,
        matches: list[ClassifiedListing],
        scraper_status: dict[str, str],
    ) -> str:
        rows = ""
        for m in matches:
            l = m.listing
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee">
                    <a href="{l.url}" style="color:#2563eb">{l.title}</a>
                </td>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;color:#16a34a">
                    R$ {l.price:,.2f}
                </td>
                <td style="padding:8px;border-bottom:1px solid #eee">{l.source}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{m.confidence:.0%}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{l.condition}</td>
            </tr>"""

        status_items = "".join(
            f"<li><strong>{k}</strong>: {v}</li>" for k, v in scraper_status.items()
        )

        return f"""
        <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto">
        <h2 style="color:#1e40af">DDR5 CL30 Deal Alert</h2>
        <p>Found <strong>{len(matches)}</strong> deal(s) below
        <strong>R$ {self.threshold:,.2f}</strong></p>
        <table style="width:100%;border-collapse:collapse">
            <thead>
                <tr style="background:#f1f5f9">
                    <th style="padding:8px;text-align:left">Product</th>
                    <th style="padding:8px;text-align:left">Price</th>
                    <th style="padding:8px;text-align:left">Store</th>
                    <th style="padding:8px;text-align:left">Confidence</th>
                    <th style="padding:8px;text-align:left">Condition</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        <hr style="margin-top:24px">
        <h4>Scraper Status</h4>
        <ul>{status_items}</ul>
        </body></html>
        """

    def _render_plain(
        self,
        matches: list[ClassifiedListing],
        scraper_status: dict[str, str],
    ) -> str:
        lines = [
            f"DDR5 CL30 Deal Alert - {len(matches)} deal(s) found below R${self.threshold:,.2f}",
            "=" * 60,
            "",
        ]
        for m in matches:
            l = m.listing
            lines.append(f"  {l.title}")
            lines.append(f"  Price: R$ {l.price:,.2f}" if l.price else "  Price: N/A")
            lines.append(f"  Store: {l.source} | Condition: {l.condition}")
            lines.append(f"  Confidence: {m.confidence:.0%}")
            lines.append(f"  Link: {l.url}")
            lines.append("")

        lines.append("-" * 60)
        lines.append("Scraper Status:")
        for k, v in scraper_status.items():
            lines.append(f"  {k}: {v}")

        return "\n".join(lines)
