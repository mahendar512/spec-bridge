"""Full end-to-end runs of the Streamlit app (headless, no browser, no AI - always demo mode)."""

import io

import docx
from streamlit.testing.v1 import AppTest

import export
from checks import ready_to_build
from conftest import ROOT
from create_samples import build_all
from parsing import parse_path

NEW_EMAIL = "Staff get an email and an in-app message when their leave request is decided."
REMINDER = "Managers get a reminder when a leave request is waiting for their approval."


def _app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def _upload_samples(at):
    """Same result as uploading the two sample files in the file uploader."""
    at.session_state["docs"] = [parse_path(str(p)).to_dict() for p in build_all()]
    at.run()
    assert not at.exception, at.exception


def _click(at, key):
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def _keys(at):
    return {b.key for b in at.button}


def _to_review(at):
    _upload_samples(at)
    _click(at, "find_reqs")
    assert at.session_state["stage"] == 1  # 2 · AI extracts and checks
    _click(at, "start_review")
    assert at.session_state["stage"] == 2  # 3 · Analyst review


def _through_cp1(at):
    _to_review(at)
    _click(at, "keep_ISS-01_REQ-04")
    _click(at, "accept_rest")
    _click(at, "approve_cp1")
    _click(at, "send_partner")
    assert at.session_state["stage"] == 4  # 5 · Partner confirms


def test_full_demo_run():
    at = _app()
    assert at.session_state["stage"] == 0
    assert all(at.button(key=f"nav_{i}").disabled for i in range(1, 6))  # all locked at the start
    sidebar_text = " ".join(m.value for m in at.sidebar.markdown)
    assert "Demo mode" not in sidebar_text and "Ollama" not in sidebar_text
    assert "5,000" not in sidebar_text and "Partner A" not in sidebar_text
    assert "load_samples" not in _keys(at)  # upload only

    # 1 · Inputs
    _upload_samples(at)
    names = [d["name"] for d in at.session_state["docs"]]
    assert names == ["HR_Leave_Policy.docx", "HR_email_carry_over.eml"]

    # 2 · AI extracts and checks
    _click(at, "find_reqs")
    assert at.session_state["stage"] == 1
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    issues = at.session_state["issues"]
    assert list(reqs) == ["REQ-01", "REQ-02", "REQ-03", "REQ-04"]
    assert all(s["verified"] for r in reqs.values() for s in r["sources"])
    assert [(i["type"], i["req_ids"]) for i in issues] == [("Conflict", ["REQ-02", "REQ-04"])]
    assert at.button(key="nav_3").disabled  # stories locked until Checkpoint 1

    # 3 · Analyst review (Checkpoint 1)
    _click(at, "start_review")
    assert at.session_state["stage"] == 2
    # Requirements in an open conflict are only shown in the conflict card, not in the list
    assert {"accept_REQ-02", "accept_REQ-04"}.isdisjoint(_keys(at))
    assert {"accept_REQ-01", "accept_REQ-03"} <= _keys(at)
    assert at.button(key="approve_cp1").disabled     # nothing decided yet
    _click(at, "keep_ISS-01_REQ-04")
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    assert reqs["REQ-04"]["status"] == "accepted" and reqs["REQ-04"]["note"] == "Confirmed by HR"
    assert reqs["REQ-02"]["status"] == "rejected"
    assert at.session_state["issues"][0]["status"] == "resolved"
    assert "accept_REQ-04" in _keys(at)  # settled: back in the list

    assert not any(k.startswith(("ask_", "pask_")) for k in _keys(at))  # no question buttons
    _click(at, "edit_REQ-03")
    at.text_area(key="edit_text_REQ-03").input(NEW_EMAIL).run()
    _click(at, "save_REQ-03")
    at.text_area(key="new_req_text").input(REMINDER).run()
    at.text_input(key="new_req_note").input("Agreed with HR lead").run()
    _click(at, "add_req")
    _click(at, "accept_rest")
    statuses = {r["id"]: r["status"] for r in at.session_state["reqs"]}
    assert statuses == {"REQ-01": "accepted", "REQ-02": "rejected", "REQ-03": "edited",
                        "REQ-04": "accepted", "REQ-05": "added"}
    assert not at.button(key="approve_cp1").disabled

    _click(at, "approve_cp1")  # approving lets the AI draft the stories (step 4)
    s = at.session_state
    assert s["stage"] == 3 and s["signoffs"]["cp1"]["by"] == "Mahendar"
    assert [x["req_ids"] for x in s["stories"]] == [["REQ-01"], ["REQ-03"], ["REQ-04"], ["REQ-05"]]
    assert [x["drafted_by"] for x in s["stories"]] == ["demo", "demo", "demo", "template"]
    assert s["tests"] == []  # tests come after the partner confirms
    assert at.button(key="nav_4").disabled

    # 4 · AI drafts stories -> 5 · Partner confirms (Checkpoint 2)
    _click(at, "send_partner")
    assert at.session_state["stage"] == 4
    assert at.button(key="approve_cp2").disabled
    assert at.button(key="nav_5").disabled
    assert not any(k.startswith("pask_") for k in _keys(at))
    _click(at, "pconfirm_REQ-01")
    _click(at, "pconfirm_all")
    assert not at.button(key="approve_cp2").disabled
    _click(at, "approve_cp2")
    s = at.session_state
    assert s["stage"] == 5 and s["signoffs"]["cp2"]["role"] == "Partner lead"

    # 6 · Tests and export (Checkpoint 3)
    assert "dl_docx" not in _keys(at)  # export locked
    _click(at, "draft_tests")
    s = at.session_state
    stories, tests = s["stories"], s["tests"]
    criteria = {c["id"] for x in stories for c in x["criteria"]}
    assert criteria == {t["criterion_id"] for t in tests}  # full coverage
    carry_story = next(x for x in stories if x["req_ids"] == ["REQ-04"])
    assert any(t["type"] == "boundary" and "11" in t["expected"]
               for t in tests if t["story_id"] == carry_story["id"])
    assert len(ready_to_build(stories, tests, s["reqs"], s["issues"])) == 4
    assert "dl_docx" not in _keys(at)  # still locked: the tester approves first
    _click(at, "approve_cp3")
    assert at.session_state["signoffs"]["cp3"]["role"] == "Tester"
    assert {"dl_stories", "dl_tests", "dl_docx"} <= {d.key for d in at.get("download_button")}

    s = at.session_state
    assert export.stories_csv(s["stories"]).count(b"US-") == 4
    data = export.agreed_docx({"name": "Staff leave app", "company": "Larkfield Retail",
                               "partner": "Partner A", "analyst": "Mahendar"},
                              s["reqs"], s["issues"], s["stories"], s["tests"], s["log"],
                              [s["signoffs"][k] for k in ("cp1", "cp2", "cp3")])
    doc = docx.Document(io.BytesIO(data))
    assert doc.paragraphs[0].text == "Agreed requirements – Staff leave app – v1.0"
    actions = {e["action"] for e in s["log"]}
    assert {"Checkpoint approved", "Partner confirmed", "Sent to partner"} <= actions

    # The stepper can jump back to any unlocked step
    _click(at, "nav_1")
    assert at.session_state["stage"] == 1

    # Start over clears everything
    _click(at, "start_over")
    assert at.session_state["docs"] == [] and at.session_state["stage"] == 0


def test_going_back_to_review_keeps_checkpoint_1():
    at = _app()
    _through_cp1(at)
    _click(at, "pconfirm_REQ-04")
    # Look back at Analyst review without changing anything: Checkpoint 1 still stands
    _click(at, "nav_2")
    _click(at, "review_next")
    assert at.session_state["stage"] == 4
    assert "REQ-04" in at.session_state["partner_ok"]
    _click(at, "pconfirm_all")
    _click(at, "approve_cp2")
    assert at.session_state["stage"] == 5


def test_changing_a_requirement_after_approval_reopens_the_checkpoint():
    at = _app()
    _through_cp1(at)
    _click(at, "nav_2")
    _click(at, "edit_REQ-01")
    at.text_area(key="edit_text_REQ-01").input("Staff request leave in the app and can add a comment for their manager.").run()
    _click(at, "save_REQ-01")
    assert at.button(key="nav_3").disabled and at.button(key="nav_4").disabled
    assert not at.button(key="approve_cp1").disabled  # approve again
    _click(at, "approve_cp1")
    s = at.session_state
    assert s["stage"] == 3 and not s["sent_to_partner"]
    assert any(x["req_ids"] == ["REQ-01"] for x in s["stories"])


def test_reject_needs_a_reason():
    at = _app()
    _to_review(at)
    _click(at, "reject_REQ-03")
    at.selectbox(key="rej_reason_REQ-03").set_value("Out of scope").run()
    at.text_input(key="rej_note_REQ-03").input("Phase 2").run()
    _click(at, "confirm_reject_REQ-03")
    r = next(r for r in at.session_state["reqs"] if r["id"] == "REQ-03")
    assert r["status"] == "rejected" and r["note"] == "Out of scope: Phase 2"


def test_custom_wording_settles_conflict():
    at = _app()
    _to_review(at)
    at.text_input(key="custom_ISS-01").input("Staff can carry over up to 8 unused days.").run()
    _click(at, "custom_save_ISS-01")
    reqs = {r["id"]: r for r in at.session_state["reqs"]}
    assert reqs["REQ-02"]["status"] == "edited"
    assert reqs["REQ-02"]["text"] == "Staff can carry over up to 8 unused days."
    assert reqs["REQ-04"]["status"] == "rejected"


def test_upload_order_does_not_move_the_conflict():
    """Email uploaded first: still one conflict, and it is the carry-over pair."""
    at = _app()
    at.session_state["docs"] = [parse_path(str(p)).to_dict() for p in reversed(build_all())]
    at.run()
    _click(at, "find_reqs")
    reqs = {r["id"]: r["text"] for r in at.session_state["reqs"]}
    issues = at.session_state["issues"]
    assert len(issues) == 1 and issues[0]["type"] == "Conflict"
    assert all("carry over" in reqs[rid] for rid in issues[0]["req_ids"])
    assert "start date" in reqs["REQ-01"]  # numbering is the same whatever the upload order
