"""Full end-to-end run of the Streamlit app in demo mode (headless, no browser, no AI)."""

import io

import docx
from streamlit.testing.v1 import AppTest

import export
from checks import ready_to_build
from conftest import ROOT

NEW_BALANCE = "Staff can see their remaining leave balance on the home screen at any time."


def _click(at, key):
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def test_full_demo_run():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.session_state["demo"] = True
    at.run()
    assert not at.exception

    # Step 2: inputs
    _click(at, "load_samples")
    names = [d["name"] for d in at.session_state["docs"]]
    assert names == ["HR_Leave_Policy.docx", "HR_email_carry_over.eml"]

    # Step 3 + 4: 4 requirements, 1 issue (the 5 vs 10 days conflict)
    _click(at, "find_reqs")
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    issues = at.session_state["issues"]
    assert list(reqs) == ["REQ-01", "REQ-02", "REQ-03", "REQ-04"]
    assert all(s["verified"] for r in reqs.values() for s in r["sources"])
    assert [(i["type"], i["req_ids"]) for i in issues] == [("Conflict", ["REQ-02", "REQ-04"])]

    # Step 5: settle the conflict: 10 days, confirmed by HR
    radio = at.radio(key="pick_ISS-01")
    radio.set_value(next(o for o in radio.options if o.startswith("REQ-04"))).run()
    _click(at, "decide_ISS-01")
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    assert reqs["REQ-04"]["status"] == "accepted" and reqs["REQ-04"]["note"] == "Confirmed by HR"
    assert reqs["REQ-02"]["status"] == "rejected"
    assert at.session_state["issues"][0]["status"] == "resolved"

    # Edit one requirement, accept the rest
    _click(at, "edit_REQ-03")
    at.text_area(key="edit_text_REQ-03").input(NEW_BALANCE).run()
    _click(at, "save_REQ-03")
    _click(at, "accept_rest")
    statuses = {r["id"]: r["status"] for r in at.session_state["reqs"]}
    assert statuses == {"REQ-01": "accepted", "REQ-02": "rejected",
                        "REQ-03": "edited", "REQ-04": "accepted"}
    actions = {e["action"] for e in at.session_state["log"]}
    assert {"Accepted", "Edited", "Rejected", "Issue resolved"} <= actions

    # Step 6: stories only from approved requirements
    _click(at, "make_stories")
    stories = at.session_state["stories"]
    assert [s["req_ids"] for s in stories] == [["REQ-01"], ["REQ-03"], ["REQ-04"]]
    assert all(s["drafted_by"] == "demo" for s in stories)

    # Step 7: tests, full coverage, boundary test on 10 vs 11 days
    _click(at, "make_tests")
    tests = at.session_state["tests"]
    criteria = {c["id"] for s in stories for c in s["criteria"]}
    assert criteria == {t["criterion_id"] for t in tests}
    carry_story = next(s for s in stories if s["req_ids"] == ["REQ-04"])
    assert any(t["type"] == "boundary" and "11" in t["expected"]
               for t in tests if t["story_id"] == carry_story["id"])
    assert len(ready_to_build(stories, tests, at.session_state["reqs"],
                              at.session_state["issues"])) == 3

    # Step 8: exports
    s = at.session_state
    assert export.stories_csv(s["stories"]).count(b"US-") == 3
    assert export.tests_csv(s["tests"], s["stories"]).count(b"TC-") == len(tests)
    data = export.agreed_docx({"name": "Staff leave app", "company": "Larkfield Retail",
                               "partner": "Partner A", "analyst": "Alex Morgan"},
                              s["reqs"], s["issues"], s["stories"], s["tests"], s["log"])
    doc = docx.Document(io.BytesIO(data))
    assert doc.paragraphs[0].text == "Agreed requirements – Staff leave app – v1.0"
    assert not at.exception
