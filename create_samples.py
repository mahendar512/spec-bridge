"""Build the two demo input files in sample_inputs/.

    python create_samples.py          # create them if missing
    python create_samples.py --force  # rebuild them

Kept deliberately small: 4 requirements in total, and 1 planted problem.
  Policy: request leave in the app, carry over max 5 days, email when a request is decided
  Email:  carry over up to 10 days  ->  Conflict with the policy (5 vs 10)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_inputs"
POLICY_FILE = SAMPLE_DIR / "HR_Leave_Policy.docx"
EMAIL_FILE = SAMPLE_DIR / "HR_email_carry_over.eml"

COMPANY = "Larkfield Retail"
PROJECT_EMAIL = "specbridge+leave-app@company.com"


def build_policy(path: Path = POLICY_FILE) -> Path:
    import docx
    from docx.shared import Pt

    d = docx.Document()
    style = d.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    d.add_heading(f"{COMPANY} - Staff Leave Policy", level=0)
    table = d.add_table(rows=3, cols=2)
    table.style = "Light List Accent 1"
    for row, (k, v) in zip(table.rows, [
        ("Document owner", "HR Department"),
        ("Version", "3.2"),
        ("Applies to", "All 5,000 staff"),
    ]):
        row.cells[0].text, row.cells[1].text = k, v

    d.add_heading("1. Requesting leave", level=1)
    d.add_paragraph(
        "Staff request annual leave in the app by choosing a start date and an end date.")

    d.add_heading("2. Carrying over unused leave", level=1)
    d.add_paragraph(
        "Staff can carry over a maximum of 5 unused annual leave days into the next calendar year.")

    d.add_heading("3. Notifications", level=1)
    d.add_paragraph(
        "Staff receive an email when their line manager approves or declines their leave request.")

    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(path)
    return path


EMAIL_BODY = """Hi Alex,

Quick update from Monday's leadership meeting about the Staff leave app.

Leadership agreed that staff can carry over up to 10 unused leave days into the next year, instead of the limit in the current policy. Please make sure the app reflects this.

Thanks,
Sam

Sam Okafor
HR Lead, Larkfield Retail
"""


def build_email(path: Path = EMAIL_FILE) -> Path:
    msg = EmailMessage()
    msg["From"] = "Sam Okafor <sam.okafor@company.com>"
    msg["To"] = "Alex Morgan <alex.morgan@company.com>"
    msg["Cc"] = f"SpecBridge - Staff leave app <{PROJECT_EMAIL}>"
    msg["Date"] = format_datetime(datetime(2026, 11, 17, 10, 42, tzinfo=timezone.utc))
    msg["Subject"] = "Leave app - carry-over decision"
    msg["Message-ID"] = "<leave-app-2026-11-17@company.com>"
    msg.set_content(EMAIL_BODY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(msg))
    return path


def build_all(force: bool = False) -> list[Path]:
    out = []
    for path, builder in ((POLICY_FILE, build_policy), (EMAIL_FILE, build_email)):
        if force or not path.exists():
            builder(path)
        out.append(path)
    return out


if __name__ == "__main__":
    for p in build_all(force="--force" in sys.argv):
        print(f"Ready: {p}")
