import csv
import io

import docx

import export

STORIES = [{
    "id": "US-01", "title": "Carry over up to 10 days", "role": "staff member",
    "goal": "to carry over 10 days", "benefit": "I keep my leave",
    "description": "As a staff member, I want to carry over 10 days, so that I keep my leave.",
    "req_ids": ["REQ-09"], "sources": ["HR_email_carry_over.eml"],
    "criteria": [{"id": "AC-01-1", "given": "10 unused days", "when": "the year ends",
                  "then": "10 days are carried over, with a \"quote\", and a comma"}],
}]
TESTS = [{"id": "TC-001", "story_id": "US-01", "criterion_id": "AC-01-1", "type": "boundary",
          "steps": "1. Give 11 days\n2. Run year end", "expected": "Only 10 carried over"}]


def _read(data: bytes):
    assert data.startswith(b"\xef\xbb\xbf")  # BOM so Excel shows the text correctly
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))


def test_stories_csv_columns_and_row():
    rows = _read(export.stories_csv(STORIES))
    assert rows[0] == ["Story ID", "Title", "Description", "Acceptance Criteria",
                       "Requirement IDs", "Source"]
    assert rows[1][0] == "US-01"
    assert rows[1][3].startswith("AC-01-1: Given 10 unused days")
    assert 'with a "quote", and a comma' in rows[1][3]
    assert rows[1][4] == "REQ-09"
    assert rows[1][5] == "HR_email_carry_over.eml"


def test_tests_csv_columns_and_row():
    rows = _read(export.tests_csv(TESTS, STORIES))
    assert rows[0] == ["Test ID", "Story ID", "Criterion", "Type", "Steps", "Expected Result"]
    assert rows[1][:2] == ["TC-001", "US-01"]
    assert rows[1][2].startswith("AC-01-1: Given")
    assert rows[1][3] == "Boundary"
    assert rows[1][4] == "1. Give 11 days\n2. Run year end"


def test_agreed_docx_opens():
    project = {"name": "Staff leave app", "company": "Larkfield", "partner": "Partner A",
               "analyst": "Alex"}
    reqs = [{"id": "REQ-09", "text": "Carry over 10", "type": "rule", "status": "accepted",
             "note": "Confirmed by HR", "sources": [{"file": "a.eml", "quote": "q", "verified": True}]}]
    issues = [{"id": "ISS-01", "type": "Conflict", "req_ids": ["REQ-09"], "explanation": "e",
               "suggestion": "s", "status": "resolved", "resolution": "Kept REQ-09"}]
    log = [{"time": "10:00", "action": "Accepted", "item": "REQ-09", "detail": "x"}]
    data = export.agreed_docx(project, reqs, issues, STORIES, TESTS, log)
    d = docx.Document(io.BytesIO(data))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Agreed requirements – Staff leave app – v1.0" in text
    assert "Kept REQ-09" in text


def test_agreed_docx_lists_approvals_and_questions():
    project = {"name": "Staff leave app", "company": "Larkfield", "partner": "Partner A",
               "analyst": "Mahendar"}
    approvals = [{"checkpoint": "Checkpoint 1", "title": "Analyst review", "by": "Mahendar",
                  "role": "Business Analyst", "time": "26 Nov 2026 10:00"}]
    questions = [{"id": "Q-01", "req_ids": ["REQ-09"], "asked_by": "Partner lead",
                  "asked_to": "Analyst", "text": "Is it 10 working days?", "status": "answered",
                  "answer": "Calendar days."}]
    data = export.agreed_docx(project, [], [], STORIES, TESTS, [], approvals, questions)
    d = docx.Document(io.BytesIO(data))
    text = "\n".join(p.text for p in d.paragraphs)
    cells = "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    assert "Checkpoint 1 · Analyst review" in cells and "Mahendar" in cells
    assert "Is it 10 working days?" in text and "Answer: Calendar days." in text
