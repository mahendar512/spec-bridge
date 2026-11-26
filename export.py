"""Exports: stories.csv, test_cases.csv (Jira-import friendly) and agreed_version.docx."""

from __future__ import annotations

import csv
import io
from datetime import date

from checks import APPROVED, coverage, criterion_text, ready_to_build

STORY_COLUMNS = ["Story ID", "Title", "Description", "Acceptance Criteria", "Requirement IDs", "Source"]
TEST_COLUMNS = ["Test ID", "Story ID", "Criterion", "Type", "Steps", "Expected Result"]


def _csv_bytes(columns: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens it correctly


def stories_csv(stories: list[dict]) -> bytes:
    rows = []
    for s in stories:
        criteria = "\n".join(f"{c['id']}: {criterion_text(c)}" for c in s["criteria"])
        rows.append([s["id"], s["title"], s["description"], criteria,
                     ", ".join(s["req_ids"]), "; ".join(s["sources"])])
    return _csv_bytes(STORY_COLUMNS, rows)


def tests_csv(tests: list[dict], stories: list[dict]) -> bytes:
    crit = {c["id"]: c for s in stories for c in s["criteria"]}
    rows = []
    for t in tests:
        c = crit.get(t["criterion_id"])
        label = f"{t['criterion_id']}: {criterion_text(c)}" if c else t["criterion_id"]
        rows.append([t["id"], t["story_id"], label, t["type"].capitalize(), t["steps"], t["expected"]])
    return _csv_bytes(TEST_COLUMNS, rows)


def agreed_docx(project: dict, reqs: list[dict], issues: list[dict], stories: list[dict],
                tests: list[dict], log: list[dict], approvals: list[dict] | None = None,
                questions: list[dict] | None = None) -> bytes:
    """approvals: [{checkpoint, title, by, role, time}]; questions: see app.py (Q-xx records)."""
    import docx
    from docx.shared import Pt, RGBColor

    d = docx.Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(10)

    d.add_heading(f"Agreed requirements – {project['name']} – v1.0", level=0)
    d.add_paragraph(f"{project['company']} · Build partner: {project['partner']} · "
                    f"Business analyst: {project['analyst']} · {date.today():%d %B %Y}")

    # ---- summary
    approved = [r for r in reqs if r["status"] in APPROVED]
    rejected = [r for r in reqs if r["status"] == "rejected"]
    resolved = [i for i in issues if i["status"] == "resolved"]
    open_issues = [i for i in issues if i["status"] == "open"]
    ready = ready_to_build(stories, tests, reqs, issues)
    d.add_heading("Summary", level=1)
    d.add_paragraph(
        f"{len(reqs)} requirements were reviewed: {len(approved)} agreed and {len(rejected)} rejected. "
        f"{len(issues)} issues were found, of which {len(resolved)} are resolved and "
        f"{len(open_issues)} are still open. {len(stories)} user stories and {len(tests)} test cases "
        f"were drafted; {len(ready)} stories are ready to build. "
        "The AI drafted this content; every item was reviewed by the business analyst.")

    # ---- checkpoint approvals
    if approvals is not None:
        d.add_heading("Approvals", level=1)
        t = d.add_table(rows=1, cols=4)
        t.style = "Light Grid Accent 1"
        for cell, h in zip(t.rows[0].cells, ["Checkpoint", "Approved by", "Role", "When"]):
            cell.text = h
        for a in approvals:
            row = t.add_row().cells
            row[0].text = f"{a['checkpoint']} · {a['title']}"
            row[1].text, row[2].text, row[3].text = a["by"], a["role"], a["time"]

    if questions:
        d.add_heading("Questions and answers", level=1)
        for q in questions:
            p = d.add_paragraph(style="List Bullet")
            p.add_run(f"{q['id']} ({', '.join(q['req_ids'])}) – {q['asked_by']} asked "
                      f"{q['asked_to']}: ").bold = True
            p.add_run(f"{q['text']} ")
            p.add_run(f"Answer: {q['answer']}" if q.get("answer") else "Not answered yet.")

    # ---- requirements
    d.add_heading("Requirements", level=1)
    t = d.add_table(rows=1, cols=5)
    t.style = "Light Grid Accent 1"
    for cell, h in zip(t.rows[0].cells, ["ID", "Requirement", "Type", "Status", "Source"]):
        cell.text = h
    for r in reqs:
        row = t.add_row().cells
        row[0].text = r["id"]
        row[1].text = r["text"]
        row[2].text = r["type"]
        row[3].text = r["status"].capitalize() + (f" – {r['note']}" if r.get("note") else "")
        row[4].text = "\n".join(f"{s['file']}: \"{s['quote']}\"" for s in r["sources"]) or "Added by analyst"
        if r["status"] == "rejected":
            for cell in row:
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    # ---- issues
    d.add_heading("Resolved issues", level=1)
    if not resolved:
        d.add_paragraph("No issues have been resolved yet.")
    for i in resolved:
        p = d.add_paragraph(style="List Bullet")
        p.add_run(f"{i['id']} {i['type']} ({', '.join(i['req_ids'])}): ").bold = True
        p.add_run(f"{i['explanation']} Decision: {i['resolution']}")
    if open_issues:
        d.add_heading("Open issues", level=2)
        for i in open_issues:
            p = d.add_paragraph(style="List Bullet")
            p.add_run(f"{i['id']} {i['type']} ({', '.join(i['req_ids'])}): ").bold = True
            p.add_run(f"{i['explanation']} Suggested: {i['suggestion']}")

    # ---- stories
    d.add_heading("User stories", level=1)
    for s in stories:
        d.add_heading(f"{s['id']} – {s['title']}", level=2)
        d.add_paragraph(s["description"])
        d.add_paragraph(f"Requirements: {', '.join(s['req_ids'])} · Source: {'; '.join(s['sources'])}"
                        + (" · Ready to build" if s["id"] in ready else ""))
        for c in s["criteria"]:
            d.add_paragraph(f"{c['id']}: {criterion_text(c)}", style="List Bullet")

    # ---- tests
    d.add_heading("Test cases", level=1)
    t = d.add_table(rows=1, cols=5)
    t.style = "Light Grid Accent 1"
    for cell, h in zip(t.rows[0].cells, ["Test ID", "Story / criterion", "Type", "Steps", "Expected result"]):
        cell.text = h
    for tc in tests:
        row = t.add_row().cells
        row[0].text = tc["id"]
        row[1].text = f"{tc['story_id']} / {tc['criterion_id']}"
        row[2].text = tc["type"].capitalize()
        row[3].text = tc["steps"]
        row[4].text = tc["expected"]
    missing = [c for c in coverage(stories, tests) if c["tests"] == 0]
    if missing:
        d.add_paragraph("Criteria without a test: " + ", ".join(c["criterion_id"] for c in missing))

    # ---- decisions
    d.add_heading("Analyst decisions", level=1)
    if not log:
        d.add_paragraph("No decisions recorded.")
    else:
        t = d.add_table(rows=1, cols=4)
        t.style = "Light Grid Accent 1"
        for cell, h in zip(t.rows[0].cells, ["Time", "Action", "Item", "Detail"]):
            cell.text = h
        for entry in log:
            row = t.add_row().cells
            row[0].text, row[1].text = entry["time"], entry["action"]
            row[2].text, row[3].text = entry["item"], entry["detail"]

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
