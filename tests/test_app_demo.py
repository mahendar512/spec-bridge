"""Full end-to-end run of the Streamlit app (headless, no browser, no AI - always demo mode)."""

import io

import docx
from streamlit.testing.v1 import AppTest

import export
from checks import ready_to_build
from conftest import ROOT

NEW_BALANCE = "Staff can see their remaining leave balance on the home screen at any time."
REMINDER = "Managers get a reminder when a leave request is waiting for their approval."


def _click(at, key):
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def test_full_demo_run():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert at.session_state["stage"] == 0
    assert at.button(key="nav_1").disabled  # Review is locked until requirements exist
    sidebar_text = " ".join(m.value for m in at.sidebar.markdown)
    assert "Demo mode" not in sidebar_text and "Ollama" not in sidebar_text

    # Step 1: sources
    _click(at, "load_samples")
    names = [d["name"] for d in at.session_state["docs"]]
    assert names == ["HR_Leave_Policy.docx", "HR_email_carry_over.eml"]

    # Find requirements -> moves on to Review automatically
    _click(at, "find_reqs")
    assert at.session_state["stage"] == 1
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    issues = at.session_state["issues"]
    assert list(reqs) == ["REQ-01", "REQ-02", "REQ-03", "REQ-04"]
    assert all(s["verified"] for r in reqs.values() for s in r["sources"])
    assert [(i["type"], i["req_ids"]) for i in issues] == [("Conflict", ["REQ-02", "REQ-04"])]
    assert at.button(key="accept_REQ-02").disabled  # in an open conflict: decide it first

    # Settle the conflict in one click: keep 10 days, confirmed by HR
    _click(at, "keep_ISS-01_REQ-04")
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    assert reqs["REQ-04"]["status"] == "accepted" and reqs["REQ-04"]["note"] == "Confirmed by HR"
    assert reqs["REQ-02"]["status"] == "rejected"
    assert at.session_state["issues"][0]["status"] == "resolved"

    # Edit one, add one, accept the rest
    _click(at, "edit_REQ-03")
    at.text_area(key="edit_text_REQ-03").input(NEW_BALANCE).run()
    _click(at, "save_REQ-03")
    at.text_area(key="new_req_text").input(REMINDER).run()
    at.text_input(key="new_req_note").input("Agreed with HR lead").run()
    _click(at, "add_req")
    _click(at, "accept_rest")
    statuses = {r["id"]: r["status"] for r in at.session_state["reqs"]}
    assert statuses == {"REQ-01": "accepted", "REQ-02": "rejected", "REQ-03": "edited",
                        "REQ-04": "accepted", "REQ-05": "added"}
    actions = {e["action"] for e in at.session_state["log"]}
    assert {"Accepted", "Edited", "Rejected", "Added", "Issue resolved"} <= actions

    # Draft stories and tests in one go -> moves on to Stories & tests
    _click(at, "make_stories")
    assert at.session_state["stage"] == 2
    stories = at.session_state["stories"]
    assert [s["req_ids"] for s in stories] == [["REQ-01"], ["REQ-03"], ["REQ-04"], ["REQ-05"]]
    assert [s["drafted_by"] for s in stories] == ["demo", "demo", "demo", "template"]
    tests = at.session_state["tests"]
    criteria = {c["id"] for s in stories for c in s["criteria"]}
    assert criteria == {t["criterion_id"] for t in tests}  # full coverage
    carry_story = next(s for s in stories if s["req_ids"] == ["REQ-04"])
    assert any(t["type"] == "boundary" and "11" in t["expected"]
               for t in tests if t["story_id"] == carry_story["id"])
    assert len(ready_to_build(stories, tests, at.session_state["reqs"],
                              at.session_state["issues"])) == 4

    # Export
    _click(at, "go_export")
    assert at.session_state["stage"] == 3
    s = at.session_state
    assert export.stories_csv(s["stories"]).count(b"US-") == 4
    assert export.tests_csv(s["tests"], s["stories"]).count(b"TC-") == len(tests)
    data = export.agreed_docx({"name": "Staff leave app", "company": "Larkfield Retail",
                               "partner": "Partner A", "analyst": "Alex Morgan"},
                              s["reqs"], s["issues"], s["stories"], s["tests"], s["log"])
    doc = docx.Document(io.BytesIO(data))
    assert doc.paragraphs[0].text == "Agreed requirements – Staff leave app – v1.0"

    # The stepper can jump back to any unlocked step
    _click(at, "nav_1")
    assert at.session_state["stage"] == 1

    # Start over clears everything
    _click(at, "start_over")
    assert at.session_state["docs"] == [] and at.session_state["stage"] == 0


def test_custom_wording_settles_conflict():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    _click(at, "load_samples")
    _click(at, "find_reqs")
    at.text_input(key="custom_ISS-01").input("Staff can carry over up to 8 unused days.").run()
    _click(at, "custom_save_ISS-01")
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    assert reqs["REQ-02"]["status"] == "edited"
    assert reqs["REQ-02"]["text"] == "Staff can carry over up to 8 unused days."
    assert reqs["REQ-04"]["status"] == "rejected"
