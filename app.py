"""SpecBridge - turn scattered documents and emails into one agreed list of requirements.

Run with:  streamlit run app.py

The app follows the SpecBridge flow. The AI does the work in the light steps; a person
decides at each of the three checkpoints, and the next step only unlocks after that approval.

  1. Inputs                - upload documents and emails, preview them
  2. AI extracts & checks  - requirements with sources, duplicates merged, problems flagged
  3. Analyst review        - CHECKPOINT 1: accept / edit / reject (with a reason), settle
                             conflicts, then approve
  4. AI drafts stories     - Given/When/Then acceptance criteria for each approved requirement
  5. Partner confirms      - CHECKPOINT 2: the partner lead confirms each agreed requirement
  6. Tests & export        - CHECKPOINT 3: AI drafts normal, error and edge cases plus a coverage
                             check; the tester approves the export (CSV + Word pack)

It always runs in demo mode: AI results are replayed from demo_data/*.json.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import ai
import checks
import export
from parsing import SUPPORTED_TYPES, ParsedDoc, parse_file

# Always demo mode: AI results are replayed from demo_data/ (see README).
DEMO_MODE = True

# Signed-in user (mock, no real login)
USER = {"name": "Mahendar", "designation": "Business Analyst", "emp_id": "EMP-10245"}

PROJECT = {
    "name": "Staff leave app",
    "company": "Larkfield Retail (5,000 staff)",
    "partner": "Partner A",
    "analyst": USER["name"],
    "email": "specbridge+leave-app@company.com",
}

# (name, kind) - kind: "input", "ai" (AI does the work) or "person" (a person decides)
STEPS = [
    ("Inputs", "input"),
    ("AI checks", "ai"),
    ("Analyst review", "person"),
    ("AI stories", "ai"),
    ("Partner confirms", "person"),
    ("Tests & export", "person"),
]
STEP_ICON = {"input": ":material/upload_file:", "ai": ":material/auto_awesome:",
             "person": ":material/person:"}
# step index -> (checkpoint key, label, title)
CHECKPOINTS = {2: ("cp1", "Checkpoint 1", "Analyst review"),
               4: ("cp2", "Checkpoint 2", "Partner confirms"),
               5: ("cp3", "Checkpoint 3", "Tests and export")}

STATUS_BADGE = {  # label, colour, icon
    "pending": ("To review", "gray", ":material/schedule:"),
    "accepted": ("Accepted", "green", ":material/check:"),
    "edited": ("Edited", "blue", ":material/edit:"),
    "rejected": ("Rejected", "red", ":material/close:"),
    "added": ("Added by analyst", "violet", ":material/add:"),
}
REJECT_REASONS = ["Noise - not a requirement", "Duplicate", "Out of scope", "Superseded", "Other"]
ISSUE_ICON = {"Conflict": ":red[:material/error:]", "Gap": ":orange[:material/help:]",
              "Unclear": ":orange[:material/contrast:]",
              "Not testable": ":orange[:material/rule:]"}
TEST_COLOR = {"positive": "#15803D", "negative": "#B91C1C", "boundary": "#B45309"}
TEST_LABEL = {"positive": "Normal", "negative": "Error", "boundary": "Edge"}

st.set_page_config(page_title="SpecBridge", page_icon=":material/hub:", layout="wide",
                   initial_sidebar_state=352)

st.markdown("""
<style>
.block-container {padding-top: 1.5rem; max-width: 100%;}
blockquote {margin: 0.2rem 0 0.4rem 0; padding: 0.1rem 0 0.1rem 0.8rem; opacity: 0.9;}
div[data-testid="stMetricValue"] {font-size: 1.6rem; font-weight: 600;}
h1 {letter-spacing: -0.02em;}
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"] {display: none !important;}

[data-testid="stSidebar"][data-collapsed="false"] {width: 22rem !important; min-width: 22rem !important; max-width: 22rem !important;}
[data-testid="stSidebarCollapseButton"] {visibility: visible !important;}
[data-testid="stExpandSidebarButton"] {visibility: visible !important; opacity: 1 !important;}
.stApp:has([data-testid="stSidebar"][data-collapsed="true"]) .block-container {padding-top: 3.6rem;}
/* Larger text for the main content only (the sidebar keeps the base size) */
[data-testid="stMain"] {zoom: 1.13;}
.brand {font-size: 1.6rem; font-weight: 700; letter-spacing: -0.03em; line-height: 1.2;}
.brand span {color: #2457D6;}
.test-line {margin-left: 1.2rem; font-size: 0.9rem; padding: 0.15rem 0;}
.test-tag {font-size: 0.68rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
           padding: 0.05rem 0.45rem; border-radius: 999px; border: 1px solid currentColor;
           margin: 0 0.35rem;}
[data-testid="stMetric"] {background: rgba(128,128,128,0.06); border: 1px solid rgba(128,128,128,0.18);
                          border-radius: 0.6rem; padding: 0.7rem 0.9rem;}
[data-testid="stMetricLabel"] {opacity: 0.7;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 0.65rem;}

/* Checkpoint labels (as in the SpecBridge flow diagram) */
.cp-pill {display: inline-block; font-size: 0.66rem; font-weight: 700; letter-spacing: 0.07em;
          background: #1E3A6E; color: #fff; padding: 0.12rem 0.55rem; border-radius: 999px;}
.cp-pill.done {background: #15803D;}
.cp-slot {height: 1.9rem; text-align: center; display: flex; align-items: flex-start; justify-content: center;}
/* Stepper: six steps on one row without cutting the labels */
[class*="st-key-nav_"] button {padding-left: 0.4rem; padding-right: 0.4rem; min-height: 2.6rem;}
[class*="st-key-nav_"] button p {font-size: 0.8rem; white-space: nowrap;}
[class*="st-key-nav_"] button [data-testid="stIconMaterial"] {font-size: 1rem;}
.legend {font-size: 0.8rem; opacity: 0.75;}
</style>
""", unsafe_allow_html=True)


# ====================================================================== state helpers

def init_state() -> None:
    defaults = {"docs": [], "reqs": [], "issues": [], "stories": [], "tests": [], "log": [],
                "notes": {}, "inputs_changed": False, "stories_basis": None, "stage": 0,
                "flash": None, "signoffs": {}, "partner_ok": {},
                "sent_to_partner": False}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def now() -> str:
    return datetime.now().strftime("%d %b %Y %H:%M")


def add_log(action: str, item: str, detail: str = "", by: str | None = None) -> None:
    st.session_state.log.append({"time": datetime.now().strftime("%H:%M:%S"),
                                 "by": by or USER["name"], "action": action, "item": item,
                                 "detail": detail})


def flash(message: str) -> None:
    """Show a toast after the next rerun."""
    st.session_state.flash = message


def docs() -> list[ParsedDoc]:
    return [ParsedDoc.from_dict(d) for d in st.session_state.docs]


def find_req(rid: str) -> dict | None:
    return next((r for r in st.session_state.reqs if r["id"] == rid), None)


def find_issue(iid: str) -> dict | None:
    return next((i for i in st.session_state.issues if i["id"] == iid), None)


def open_issues_for(rid: str) -> list[dict]:
    return [i for i in st.session_state.issues if i["status"] == "open" and rid in i["req_ids"]]


def open_issues() -> list[dict]:
    return [i for i in st.session_state.issues if i["status"] == "open"]


def open_conflicts() -> list[dict]:
    return [i for i in open_issues() if i["type"] == "Conflict"]


def approved_reqs() -> list[dict]:
    return [r for r in st.session_state.reqs if r["status"] in checks.APPROVED]


def pending_reqs() -> list[dict]:
    return [r for r in st.session_state.reqs if r["status"] == "pending"]


def approved_basis() -> list:
    return sorted((r["id"], r["text"]) for r in approved_reqs())


def source_files(req: dict) -> str:
    return ", ".join(s["file"] for s in req["sources"]) or "Added by analyst"


def story_for(rid: str) -> dict | None:
    return next((s for s in st.session_state.stories if rid in s["req_ids"]), None)


def partner_confirmed(req: dict) -> bool:
    return st.session_state.partner_ok.get(req["id"]) == req["text"]


# ---------------------------------------------------------------- checkpoints

def cp_valid(cp: str) -> bool:
    """A checkpoint counts only while what was approved is still unchanged."""
    s = st.session_state
    so = s.signoffs.get(cp)
    if not so or so["basis"] != approved_basis():
        return False
    if cp == "cp1":
        return True
    if cp == "cp2":
        return cp_valid("cp1") and s.stories_basis == approved_basis()
    return cp_valid("cp2") and bool(s.tests)


def record_signoff(cp: str, by: str, role: str) -> None:
    label, title = next((v[1], v[2]) for v in CHECKPOINTS.values() if v[0] == cp)
    st.session_state.signoffs[cp] = {"checkpoint": label, "title": title, "by": by, "role": role,
                                     "time": now(), "basis": approved_basis()}
    add_log("Checkpoint approved", f"{label} · {title}", f"Approved by {by} ({role})", by=by)


def approvals_for_export() -> list[dict]:
    return [st.session_state.signoffs[cp] for cp in ("cp1", "cp2", "cp3") if cp_valid(cp)]


def cp1_conditions() -> list[tuple[bool, str]]:
    s = st.session_state
    pending = len(pending_reqs())
    return [
        (not pending, "Every requirement accepted, edited or rejected"
         + (f" ({pending} still to review)" if pending else "")),
        (not open_issues(), "Every conflict and flagged issue decided"
         + (f" ({len(open_issues())} open)" if open_issues() else "")),
        (not s.inputs_changed, "Requirements match the current sources"),
        (bool(approved_reqs()), "At least one approved requirement"),
    ]


def cp2_conditions() -> list[tuple[bool, str]]:
    agreed = approved_reqs()
    done = sum(1 for r in agreed if partner_confirmed(r))
    return [
        (done == len(agreed) and bool(agreed),
         f"Partner lead confirmed every agreed requirement ({done} of {len(agreed)})"),
    ]


def cp3_conditions() -> list[tuple[bool, str]]:
    s = st.session_state
    cov = checks.coverage(s.stories, s.tests)
    covered = sum(1 for c in cov if c["tests"] > 0)
    return [
        (bool(s.tests), "Test cases drafted (normal, error and edge cases)"),
        (bool(cov) and covered == len(cov),
         f"Every acceptance criterion has a test ({covered} of {len(cov)})"),
    ]


# ---------------------------------------------------------------- steps

def step_unlocked(i: int) -> bool:
    s = st.session_state
    stories_ok = cp_valid("cp1") and bool(s.stories)
    return [True, bool(s.reqs), bool(s.reqs), stories_ok, stories_ok and s.sent_to_partner,
            cp_valid("cp2")][i]


def step_done(i: int) -> bool:
    s = st.session_state
    return [bool(s.reqs), bool(s.reqs), cp_valid("cp1"),
            cp_valid("cp1") and s.sent_to_partner, cp_valid("cp2"), cp_valid("cp3")][i]


def go(i: int) -> None:
    st.session_state.stage = i


# ====================================================================== actions (callbacks)

def add_doc(doc: ParsedDoc) -> None:
    st.session_state.docs = [d for d in st.session_state.docs if d["name"] != doc.name] + [doc.to_dict()]
    if st.session_state.reqs:
        st.session_state.inputs_changed = True


def on_upload() -> None:
    for f in st.session_state.get("uploader") or []:
        add_doc(parse_file(f.name, f.getvalue()))
        add_log("Input uploaded", f.name)


def remove_doc(name: str) -> None:
    st.session_state.docs = [d for d in st.session_state.docs if d["name"] != name]
    if st.session_state.reqs:
        st.session_state.inputs_changed = True


def start_over() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def set_status(rid: str, status: str, detail: str = "") -> None:
    req = find_req(rid)
    if req is None:
        return
    req["status"] = status
    st.session_state[f"editing_{rid}"] = False
    add_log(status.capitalize(), rid, detail or req["text"])
    if status == "rejected":
        _resolve_after_reject(rid)


def start_reject(rid: str) -> None:
    st.session_state[f"rejecting_{rid}"] = True


def cancel_reject(rid: str) -> None:
    st.session_state[f"rejecting_{rid}"] = False


def confirm_reject(rid: str) -> None:
    reason = st.session_state.get(f"rej_reason_{rid}") or REJECT_REASONS[0]
    extra = (st.session_state.get(f"rej_note_{rid}") or "").strip()
    note = reason + (f": {extra}" if extra else "")
    find_req(rid)["note"] = note
    st.session_state[f"rejecting_{rid}"] = False
    set_status(rid, "rejected", note)
    flash(f"{rid} rejected ({reason.lower()}).")


def _resolve_after_reject(rid: str) -> None:
    """If rejecting an item settles an open issue, mark that issue resolved."""
    for issue in open_issues_for(rid):
        alive = [i for i in issue["req_ids"] if (find_req(i) or {}).get("status") != "rejected"]
        if issue["type"] == "Conflict" and len(alive) == 1:
            _resolve(issue, f"{rid} was rejected, so {alive[0]} stands.")
        elif not alive:
            _resolve(issue, "The requirement was rejected.")


def _resolve(issue: dict, resolution: str) -> None:
    issue["status"] = "resolved"
    issue["resolution"] = resolution
    add_log("Issue resolved", issue["id"], resolution)


def start_edit(rid: str) -> None:
    st.session_state[f"editing_{rid}"] = True
    st.session_state[f"edit_text_{rid}"] = find_req(rid)["text"]


def cancel_edit(rid: str) -> None:
    st.session_state[f"editing_{rid}"] = False


def save_edit(rid: str) -> None:
    req = find_req(rid)
    new_text = (st.session_state.get(f"edit_text_{rid}") or "").strip()
    st.session_state[f"editing_{rid}"] = False
    if not new_text or new_text == req["text"]:
        return
    old = req["text"]
    req["text"] = new_text
    req["status"] = "edited"
    add_log("Edited", rid, f"\"{old}\" → \"{new_text}\"")
    for issue in open_issues_for(rid):
        if issue["type"] != "Conflict":
            _resolve(issue, f"{rid} was reworded: \"{new_text}\"")


def accept_remaining() -> None:
    blocked = {rid for i in open_conflicts() for rid in i["req_ids"]}
    n = 0
    for r in st.session_state.reqs:
        if r["status"] == "pending" and r["id"] not in blocked:
            set_status(r["id"], "accepted")
            n += 1
    flash(f"Accepted {n} requirement{'s' if n != 1 else ''}.")


def _conflict_note(iid: str) -> str:
    return (st.session_state.get(f"note_{iid}") or "").strip() or "Decided by analyst"


def _settle_conflict(issue: dict, winner: dict, note: str) -> None:
    winner["note"] = note
    add_log("Accepted", winner["id"], f"{winner['text']} ({note})")
    for rid in issue["req_ids"]:
        if rid != winner["id"]:
            loser = find_req(rid)
            loser["status"] = "rejected"
            loser["note"] = f"Superseded by {winner['id']}"
            add_log("Rejected", rid, f"Superseded by {winner['id']} ({note})")
    _resolve(issue, f"Kept {winner['id']}: \"{winner['text']}\" – {note}")
    flash(f"Conflict settled: kept {winner['id']}.")


def keep_in_conflict(iid: str, rid: str) -> None:
    winner = find_req(rid)
    winner["status"] = "accepted"
    _settle_conflict(find_issue(iid), winner, _conflict_note(iid))


def custom_conflict(iid: str) -> None:
    issue = find_issue(iid)
    custom = (st.session_state.get(f"custom_{iid}") or "").strip()
    if not custom:
        st.session_state.notes["review"] = ["Please type the agreed wording before saving it."]
        return
    winner = find_req(issue["req_ids"][0])
    old = winner["text"]
    winner["text"], winner["status"] = custom, "edited"
    add_log("Edited", winner["id"], f"\"{old}\" → \"{custom}\"")
    _settle_conflict(issue, winner, _conflict_note(iid))
    st.session_state.notes.pop("review", None)


def resolve_issue(iid: str) -> None:
    note = (st.session_state.get(f"res_note_{iid}") or "").strip() or "Dismissed by analyst"
    _resolve(find_issue(iid), note)


def add_requirement() -> None:
    text = (st.session_state.get("new_req_text") or "").strip()
    if not text:
        st.session_state.notes["review"] = ["Please type the requirement before adding it."]
        return
    numbers = [int(r["id"].split("-")[1]) for r in st.session_state.reqs] or [0]
    rid = f"REQ-{max(numbers) + 1:02d}"
    note = (st.session_state.get("new_req_note") or "").strip()
    st.session_state.reqs.append({
        "id": rid, "text": text, "type": st.session_state.get("new_req_type", "functional"),
        "topic": "", "sources": [], "status": "added", "origin": "analyst",
        "original_text": text, "note": note,
    })
    add_log("Added", rid, text + (f" ({note})" if note else ""))
    st.session_state["new_req_text"] = ""
    st.session_state["new_req_note"] = ""
    st.session_state.notes.pop("review", None)
    flash(f"Added {rid}.")


# ---------------------------------------------------------------- partner and tester

def partner_name() -> str:
    return (st.session_state.get("partner_name") or "").strip() or "Partner lead"


def tester_name() -> str:
    return (st.session_state.get("tester_name") or "").strip() or "Tester"


def partner_confirm(rid: str) -> None:
    req = find_req(rid)
    st.session_state.partner_ok[rid] = req["text"]
    add_log("Partner confirmed", rid, req["text"], by=partner_name())


def partner_confirm_all() -> None:
    n = 0
    for r in approved_reqs():
        if not partner_confirmed(r):
            partner_confirm(r["id"])
            n += 1
    flash(f"Partner lead confirmed {n} requirement{'s' if n != 1 else ''}.")


def send_to_partner() -> None:
    st.session_state.sent_to_partner = True
    st.session_state.stage = 4
    add_log("Sent to partner", "Agreed requirements",
            f"{len(approved_reqs())} requirements and {len(st.session_state.stories)} stories")
    flash("Sent to the partner lead for confirmation.")


def approve_cp2() -> None:
    record_signoff("cp2", partner_name(), "Partner lead")
    st.session_state.stage = 5
    flash("Checkpoint 2 approved: the partner lead confirmed the agreed requirements.")


def approve_cp3() -> None:
    record_signoff("cp3", tester_name(), "Tester")
    flash("Checkpoint 3 approved: the export is ready.")


# ====================================================================== AI runs

def spinner_step(label: str):
    return st.spinner(label)


def _provider():
    return None if DEMO_MODE else ai.OllamaProvider()


def run_find_requirements() -> bool:
    try:
        reqs, notes = ai.run_extraction(docs(), _provider(), DEMO_MODE, step=spinner_step)
        issues, issue_notes = ai.run_issue_check(reqs, _provider(), DEMO_MODE, step=spinner_step)
    except Exception as exc:  # never crash the demo
        st.session_state.notes["extract"] = [f"Something went wrong while reading the sources "
                                             f"({type(exc).__name__}). Please try again."]
        return False
    st.session_state.update(reqs=reqs, issues=issues, stories=[], tests=[], inputs_changed=False,
                            stories_basis=None, signoffs={}, partner_ok={},
                            sent_to_partner=False)
    st.session_state.notes["extract"] = notes + issue_notes
    for k in [k for k in st.session_state.keys()
              if str(k).startswith(("editing_", "rejecting_"))]:
        st.session_state[k] = False
    add_log("AI run", "Requirements", f"{len(reqs)} requirements and {len(issues)} issues found",
            by="SpecBridge AI")
    flash(f"Found {len(reqs)} requirements and {len(issues)} "
          f"issue{'s' if len(issues) != 1 else ''} to look at.")
    return True


def run_issue_recheck() -> None:
    try:
        issues, notes = ai.run_issue_check(st.session_state.reqs, _provider(), DEMO_MODE,
                                           step=spinner_step)
    except Exception as exc:
        st.session_state.notes["extract"] = [f"The problem check failed ({type(exc).__name__})."]
        return
    st.session_state.issues = issues
    st.session_state.notes["extract"] = notes
    add_log("AI run", "Issues", f"{len(issues)} issues found", by="SpecBridge AI")


def run_draft_stories() -> bool:
    s = st.session_state
    try:
        stories, notes = ai.run_stories(s.reqs, _provider(), DEMO_MODE, step=spinner_step)
    except Exception as exc:
        s.notes["stories"] = [f"Drafting failed ({type(exc).__name__}). Please try again."]
        return False
    s.update(stories=stories, tests=[], stories_basis=approved_basis(), sent_to_partner=False)
    s.signoffs.pop("cp2", None)
    s.signoffs.pop("cp3", None)
    s.notes["stories"] = notes
    add_log("AI run", "User stories", f"{len(stories)} stories drafted", by="SpecBridge AI")
    return True


def run_draft_tests() -> bool:
    s = st.session_state
    try:
        tests, notes = ai.run_tests(s.stories, _provider(), DEMO_MODE, step=spinner_step)
    except Exception as exc:
        s.notes["tests"] = [f"Drafting the tests failed ({type(exc).__name__}). Please try again."]
        return False
    s.tests = tests
    s.signoffs.pop("cp3", None)
    s.notes["tests"] = notes
    add_log("AI run", "Test cases", f"{len(tests)} test cases drafted", by="SpecBridge AI")
    flash(f"Drafted {len(tests)} test cases.")
    return True


def show_notes(key: str) -> None:
    for note in st.session_state.notes.get(key) or []:
        st.warning(note, icon=":material/warning:")


# ====================================================================== sidebar

def render_sidebar() -> None:
    sb = st.sidebar

    s = st.session_state
    reqs, issues = s.reqs, s.issues
    count = lambda status: sum(1 for r in reqs if r["status"] == status)  # noqa: E731
    open_n = len(open_issues())
    rows = [
        ("Requirements found", sum(1 for r in reqs if r["origin"] == "ai"), ""),
        ("Issues flagged", len(issues), f"{open_n} open" if open_n else ""),
        ("Accepted", count("accepted"), ""),
        ("Edited", count("edited"), ""),
        ("Rejected", count("rejected"), ""),
        ("Added by hand", count("added"), ""),
    ]
    row_html = ("<div style='display:flex;justify-content:space-between;padding:0.28rem 0;"
                "border-bottom:1px solid rgba(128,128,128,0.15)'><span>{label}</span>"
                "<span><b>{value}</b>{extra}</span></div>")
    extra_html = " <small style='color:#c2410c'>({})</small>"
    html = "".join(row_html.format(label=label, value=value,
                                   extra=extra_html.format(extra) if extra else "")
                   for label, value, extra in rows)
    sb.markdown("#### Summary")
    sb.markdown(f"<div style='font-size:0.9rem'>{html}</div>", unsafe_allow_html=True)

    sb.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    sb.markdown("#### Checkpoints")
    cp_rows = []
    for cp, label, title in CHECKPOINTS.values():
        so = s.signoffs.get(cp)
        if cp_valid(cp):
            status = f"<small style='opacity:0.7'>{so['by']} · {so['time']}</small>"
            mark = ":green[:material/check_circle:]"
        elif so:
            status = "<small style='color:#c2410c'>changed - approve again</small>"
            mark = ":orange[:material/warning:]"
        else:
            status = "<small style='opacity:0.6'>waiting</small>"
            mark = ":gray[:material/schedule:]"
        cp_rows.append((mark, label, title, status))
    for mark, label, title, status in cp_rows:
        with sb.container():
            st.markdown(f"{mark} **{label}** · {title}")
            st.markdown(f"<div style='margin:-0.6rem 0 0.2rem 1.6rem;font-size:0.8rem'>{status}</div>",
                        unsafe_allow_html=True)

    ready = checks.ready_to_build(s.stories, s.tests, reqs, issues)
    sb.metric("Stories ready to build", f"{len(ready)} of {len(s.stories)}",
              help="Approved, every acceptance criterion has a test, and no open issues.")

    with sb.expander("How SpecBridge works"):
        st.markdown(
            "1. **Inputs**: emails copied to one tagged address, or files uploaded.\n"
            "2. **AI extracts and checks**: requirements with their source, duplicates merged, "
            "conflicts, gaps and vague words flagged.\n"
            "3. **Analyst review** · *Checkpoint 1*: accept, edit or reject each item, dismiss "
            "noise with a reason.\n"
            "4. **AI drafts user stories** with Given/When/Then criteria.\n"
            "5. **Partner confirms** · *Checkpoint 2*: the partner lead confirms each agreed "
            "requirement.\n"
            "6. **Tests and export** · *Checkpoint 3*: AI drafts normal, error and edge cases "
            "with a coverage check; the tester approves the export.")
    if s.docs:
        sb.button("Start over", key="start_over", on_click=start_over, width="stretch",
                  icon=":material/restart_alt:")


# ====================================================================== stepper

def render_stepper() -> None:
    s = st.session_state
    cols = st.columns(len(STEPS), gap="small")
    for i, (col, (name, kind)) in enumerate(zip(cols, STEPS)):
        cp = CHECKPOINTS.get(i)
        if cp:
            done = cp_valid(cp[0])
            col.markdown(f"<div class='cp-slot'><span class='cp-pill{' done' if done else ''}'>"
                         f"{cp[1].upper()}</span></div>", unsafe_allow_html=True)
        else:
            col.markdown("<div class='cp-slot'></div>", unsafe_allow_html=True)
        done = step_done(i) and i != s.stage
        col.button(f"{i + 1} · {name}", key=f"nav_{i}", on_click=go, args=(i,),
                   icon=":material/check_circle:" if done else STEP_ICON[kind],
                   type="primary" if i == s.stage else "secondary",
                   disabled=not step_unlocked(i), width="stretch")
    st.markdown("<div class='legend'>Sparkle icon: the AI does the work &nbsp;·&nbsp; person icon: "
                "a person decides &nbsp;·&nbsp; each checkpoint must be approved before the next "
                "step unlocks</div>", unsafe_allow_html=True)


def next_hint() -> str:
    s = st.session_state
    if not s.docs:
        return "Start by uploading the documents and emails for this project."
    if not s.reqs:
        return "Next: let the AI find the requirements in your sources."
    if not cp_valid("cp1"):
        if s.stage < 2:
            return "Next: start the analyst review."
        if open_conflicts():
            return f"Next: decide {len(open_conflicts())} conflict(s)."
        if pending_reqs():
            return f"Next: review {len(pending_reqs())} requirement(s)."
        if open_issues():
            return f"Next: resolve or dismiss {len(open_issues())} flagged issue(s)."
        return "Review finished. Next: approve Checkpoint 1."
    if not s.sent_to_partner:
        return "Next: check the drafted stories and send them to the partner lead."
    if not cp_valid("cp2"):
        return "Next: the partner lead confirms each requirement (Checkpoint 2)."
    if not s.tests:
        return "Next: let the AI draft the test cases."
    if not cp_valid("cp3"):
        return "Next: the tester approves the export (Checkpoint 3)."
    return "All three checkpoints approved. Download the export pack."


# ====================================================================== shared pieces

def render_checkpoint(cp_key: str, conditions: list[tuple[bool, str]], approver: str,
                      button_label: str, button_key: str, on_click=None) -> bool:
    """Checkpoint card: what must be true, who approves, and the approve button."""
    label, title = next((v[1], v[2]) for v in CHECKPOINTS.values() if v[0] == cp_key)
    so = st.session_state.signoffs.get(cp_key)
    clicked = False
    with st.container(border=True):
        done = cp_valid(cp_key)
        st.markdown(f"<span class='cp-pill{' done' if done else ''}'>{label.upper()}</span> "
                    f"&nbsp;<b>{title}</b>", unsafe_allow_html=True)
        if done:
            st.success(f"Approved by **{so['by']}** ({so['role']}) · {so['time']}",
                       icon=":material/verified:")
            return False
        if so:
            st.warning("Something changed after this checkpoint was approved, so it has to be "
                       "approved again.", icon=":material/history:")
        for ok, text in conditions:
            st.markdown(f"{':green[:material/check_circle:]' if ok else ':gray[:material/radio_button_unchecked:]'} {text}")
        all_ok = all(ok for ok, _ in conditions)
        left, right = st.columns([3, 2], vertical_alignment="center")
        left.caption(f"Approver: **{approver}**" + ("" if all_ok else
                     " · the button unlocks when every item above is ticked."))
        clicked = right.button(button_label, key=button_key, type="primary", disabled=not all_ok,
                               icon=":material/verified:", width="stretch", on_click=on_click)
    return clicked


def render_log() -> None:
    if st.session_state.log:
        df = pd.DataFrame(st.session_state.log)
        st.dataframe(df[["time", "by", "action", "item", "detail"]], hide_index=True,
                     width="stretch")
    else:
        st.caption("Nothing recorded yet.")


def render_requirements_table() -> None:
    rows = [{
        "ID": r["id"],
        "Requirement": r["text"],
        "Type": r["type"],
        "Status": STATUS_BADGE[r["status"]][0],
        "Source file": "\n".join(s["file"] for s in r["sources"]) or "Added by analyst",
        "Quoted source text": "\n".join(f"“{s['quote']}”" for s in r["sources"]),
        "Source check": ("Verified" if checks.is_verified(r) else "Source not verified")
        if r["sources"] else "—",
    } for r in st.session_state.reqs]
    styled = pd.DataFrame(rows).style.map(
        lambda v: "color: #c2410c; font-weight: 600" if "not verified" in str(v) else "",
        subset=["Source check"])
    st.dataframe(styled, hide_index=True, width="stretch",
                 column_config={"Requirement": st.column_config.TextColumn(width="large"),
                                "Quoted source text": st.column_config.TextColumn(width="large")})


def footer(left_text: str) -> tuple:
    st.divider()
    left, right = st.columns([3, 2], vertical_alignment="center")
    left.caption(left_text)
    return left, right


# ====================================================================== 1 · inputs

def step_inputs() -> None:
    st.markdown("### Where are the requirements?")
    st.caption("Emails copied to the project's tagged address arrive here automatically; private "
               "inboxes are never scanned. You can also upload files: .docx, .pdf, .txt, .eml, .msg")
    with st.container(border=True):
        st.file_uploader("**Upload files**", type=SUPPORTED_TYPES,
                         accept_multiple_files=True, key="uploader", on_change=on_upload)
    show_notes("inputs")

    loaded = docs()
    if loaded:
        st.markdown(f"#### Sources ({len(loaded)})")
        cols = st.columns(2, gap="medium")
        for n, doc in enumerate(loaded):
            with cols[n % 2].container(border=True):
                render_source(doc)

    ready = [d for d in loaded if not d.error]
    if st.session_state.inputs_changed:
        st.warning("The sources changed since the requirements were found. "
                   "Find them again to include the changes.")
    _, right = footer("The AI reads each file separately, pulls out every requirement with its "
                      "exact source words, and checks them for problems.")
    if right.button("Find requirements  →", key="find_reqs", type="primary", disabled=not ready,
                    width="stretch", icon=":material/auto_awesome:"):
        if run_find_requirements():
            st.session_state.stage = 1
        st.rerun()
    show_notes("extract")


def render_source(doc: ParsedDoc) -> None:
    icon = ":material/mail:" if doc.kind == "email" else ":material/description:"
    h1, h2 = st.columns([5, 1])
    h1.markdown(f"{icon} **{doc.name}**")
    h2.button("", key=f"remove_{doc.name}", on_click=remove_doc, args=(doc.name,),
              icon=":material/delete:", help="Remove this source")
    if doc.error:
        st.error(doc.error)
        return
    if doc.kind == "email":
        m = doc.meta
        st.caption(f"**From:** {m.get('from', '')}  \n**Date:** {m.get('date', '')}  \n"
                   f"**Subject:** {m.get('subject', '')}")
    else:
        st.caption(f"Document · {len(doc.text.split())} words")
    with st.expander("Preview text"):
        if doc.kind == "email":
            st.caption(f"To: {doc.meta.get('to', '')}  \nCc: {doc.meta.get('cc', '')}")
        st.text(doc.text)


# ====================================================================== 2 · AI extracts and checks

def step_ai_checks() -> None:
    s = st.session_state
    if not s.reqs:
        st.info("Add your sources and let the AI find the requirements first.")
        return
    show_notes("extract")
    st.markdown("### What the AI found")
    st.caption("Nothing here is agreed yet. The analyst decides on every item at Checkpoint 1.")
    merged = sum(1 for r in s.reqs if len({x["file"] for x in r["sources"]}) > 1)
    unverified = sum(1 for r in s.reqs if r["sources"] and not checks.is_verified(r))
    m = st.columns(4)
    m[0].metric("Sources read", len([d for d in s.docs if not d.get("error")]))
    m[1].metric("Requirements found", len(s.reqs))
    m[2].metric("Duplicates merged", merged)
    m[3].metric("Problems flagged", len(s.issues))
    if unverified:
        st.warning(f"{unverified} quote(s) could not be found in the source. They are marked "
                   "**Source not verified**.", icon=":material/warning:")

    st.markdown("#### Problems flagged")
    if not s.issues:
        st.caption("No conflicts, gaps or vague wording found.")
    for i in s.issues:
        with st.container(border=True):
            st.markdown(f"{ISSUE_ICON.get(i['type'], '')} **{i['type']}** · "
                        f"{', '.join(i['req_ids'])}  \n{i['explanation']}")
            st.caption(f":material/lightbulb: {i['suggestion']}")

    st.markdown("#### Requirements with their sources")
    render_requirements_table()

    _, right = footer("Next, the analyst accepts, edits or rejects each item.")
    right.button("Start analyst review  →", key="start_review", type="primary", on_click=go,
                 args=(2,), width="stretch", icon=":material/person:")


# ====================================================================== 3 · analyst review (CP1)

def step_review() -> None:
    s = st.session_state
    reqs = s.reqs
    if not reqs:
        st.info("Find the requirements first.")
        return
    show_notes("review")
    if s.inputs_changed:
        st.warning("The sources changed since these requirements were found. "
                   "Go back to **Inputs** and find them again.")

    m = st.columns(4)
    m[0].metric("Requirements", len(reqs))
    m[1].metric("Decisions needed", len(open_conflicts()))
    m[2].metric("Open issues", len(open_issues()))
    m[3].metric("Waiting for review", len(pending_reqs()))

    # ---- conflicts first: they block everything else
    conflicts = [i for i in s.issues if i["type"] == "Conflict"]
    if conflicts:
        st.markdown(f"### Conflicts ({len(open_conflicts())} to decide)")
        for issue in conflicts:
            render_conflict(issue)

    # ---- the requirement list: requirements in an open conflict are decided above, not here
    in_open_conflict = {rid for i in open_conflicts() for rid in i["req_ids"]}
    st.markdown("### Requirements without conflicts" if in_open_conflict else "### Requirements")
    if in_open_conflict:
        st.caption(f"{', '.join(sorted(in_open_conflict))} are decided in the conflict above "
                   "and appear here once it is settled.")
    bar_l, bar_r = st.columns([3, 2])
    with bar_l:
        choice = st.segmented_control("Show", ["All", "To review", "Approved", "Rejected"],
                                      default="All", key="req_filter", label_visibility="collapsed")
    bar_r.button("Accept all remaining", key="accept_rest", on_click=accept_remaining,
                 icon=":material/done_all:", width="stretch", disabled=not pending_reqs(),
                 help="Accepts every requirement still waiting, except ones in an open conflict.")
    wanted = {"To review": ("pending",), "Approved": checks.APPROVED,
              "Rejected": ("rejected",)}.get(choice or "All")
    shown = [r for r in reqs if (wanted is None or r["status"] in wanted)
             and r["id"] not in in_open_conflict]
    if not shown:
        st.caption("Nothing to show here.")
    for r in shown:
        render_requirement(r)

    # ---- extras
    with st.expander("Add a requirement the AI missed", icon=":material/add_circle:"):
        st.text_area("Requirement", key="new_req_text",
                     placeholder="e.g. Managers get a reminder when a leave request is waiting.")
        c1, c2 = st.columns([1, 2])
        c1.selectbox("Type", ["functional", "rule", "non-functional"], key="new_req_type")
        c2.text_input("Where did it come from?", key="new_req_note",
                      placeholder="e.g. Agreed with HR lead on a call")
        st.button("Add requirement", key="add_req", on_click=add_requirement, type="primary")

    resolved = [i for i in s.issues if i["status"] == "resolved"]
    with st.expander(f"Resolved issues ({len(resolved)})", icon=":material/task_alt:"):
        if not resolved:
            st.caption("None yet.")
        for i in resolved:
            st.markdown(f"{ISSUE_ICON.get(i['type'], '')} **{i['id']} · {i['type']}** "
                        f"({', '.join(i['req_ids'])}): {i['explanation']}  \n"
                        f":green[Decision: {i['resolution']}]")
        st.button("Run the checks again", key="recheck_issues", on_click=run_issue_recheck,
                  help="Runs the problem checks again. Earlier decisions on issues are cleared.")
    with st.expander(f"Decision log ({len(s.log)})", icon=":material/history:"):
        render_log()

    # ---- checkpoint 1
    st.divider()
    clicked = render_checkpoint("cp1", cp1_conditions(), f"{USER['name']} ({USER['designation']})",
                                "Approve review", "approve_cp1")
    if clicked:
        record_signoff("cp1", USER["name"], USER["designation"])
        if s.stories_basis == approved_basis() and s.stories:
            flash("Checkpoint 1 approved again. The stories still match.")
        elif run_draft_stories():
            flash(f"Checkpoint 1 approved. The AI drafted {len(s.stories)} user stories.")
        s.stage = 4 if (s.sent_to_partner and s.stories_basis == approved_basis()) else 3
        st.rerun()
    if cp_valid("cp1"):
        target = 4 if s.sent_to_partner else 3
        _, right = st.columns([3, 2])
        right.button(f"Continue to {STEPS[target][0]}  →", key="review_next", type="primary",
                     on_click=go, args=(target,), width="stretch")


def render_conflict(issue: dict) -> None:
    iid = issue["id"]
    involved = [r for r in (find_req(i) for i in issue["req_ids"]) if r]
    with st.container(border=True):
        if issue["status"] == "resolved":
            st.markdown(f"{ISSUE_ICON['Conflict']} **{iid} · Conflict settled**")
            st.caption(issue["resolution"])
            return
        st.markdown("### Decision needed: which one is right?")
        st.markdown(issue["explanation"])
        if f"note_{iid}" not in st.session_state:
            st.session_state[f"note_{iid}"] = "Confirmed by HR"
        st.text_input("Who confirmed the decision?", key=f"note_{iid}")
        cols = st.columns(len(involved), gap="medium")
        for col, r in zip(cols, involved):
            with col.container(border=True):
                st.markdown(f"**{r['id']}** · {source_files(r)}")
                for src in r["sources"]:
                    st.markdown(f"> “{src['quote']}”")
                st.caption(r["text"])
                st.button(f"Keep {r['id']}", key=f"keep_{iid}_{r['id']}", type="primary",
                          on_click=keep_in_conflict, args=(iid, r["id"]), width="stretch")
        with st.expander("Neither: type the agreed wording"):
            st.text_input("Agreed wording", key=f"custom_{iid}")
            st.button("Use this wording", key=f"custom_save_{iid}", on_click=custom_conflict,
                      args=(iid,))
        st.caption(f":material/lightbulb: {issue['suggestion']}  ·  Found by "
                   + ("the AI check" if issue["origin"] == "ai" else "the built-in check"))


def render_requirement(r: dict) -> None:
    rid = r["id"]
    label, color, icon = STATUS_BADGE[r["status"]]
    in_conflict = any(i["type"] == "Conflict" for i in open_issues_for(rid))
    with st.container(border=True):
        head, badges = st.columns([3, 2])
        head.markdown(f"**{rid}** · :gray[{r['type']}]")
        with badges:
            row = st.container(horizontal=True, horizontal_alignment="right")
            row.badge(label, color=color, icon=icon)
            n_files = len({x["file"] for x in r["sources"]})
            if n_files > 1:
                row.badge(f"Merged from {n_files} files", color="blue", icon=":material/merge:")
            if r["sources"] and not checks.is_verified(r):
                row.badge("Source not verified", color="orange", icon=":material/warning:")
        st.markdown(f"**{r['text']}**" if r["status"] != "rejected" else f"~~{r['text']}~~")
        for src in r["sources"]:
            mark = "" if src["verified"] else " · :orange[quote not found in source]"
            st.markdown(f"> “{src['quote']}”  \n> <small>— {src['file']}</small>{mark}",
                        unsafe_allow_html=True)
        if r.get("note"):
            st.caption(f"Note: {r['note']}")

        for issue in open_issues_for(rid):
            if issue["type"] == "Conflict":
                st.caption(f":orange[:material/error: Part of conflict {issue['id']}: decide it above.]")
                continue
            with st.container(border=True):
                st.markdown(f"{ISSUE_ICON.get(issue['type'], '')} **{issue['type']}** · "
                            f"{issue['explanation']}")
                st.caption(f":material/lightbulb: {issue['suggestion']}")
                c1, c2 = st.columns([4, 1])
                c1.text_input("Reason", key=f"res_note_{issue['id']}",
                              label_visibility="collapsed",
                              placeholder="Resolved how, or why dismiss it? (or edit the requirement)")
                c2.button("Resolve / dismiss", key=f"resolve_{issue['id']}", on_click=resolve_issue,
                          args=(issue["id"],), width="stretch")

        if st.session_state.get(f"editing_{rid}"):
            st.text_area("New wording", key=f"edit_text_{rid}")
            e = st.container(horizontal=True)
            e.button("Save", key=f"save_{rid}", on_click=save_edit, args=(rid,), type="primary")
            e.button("Cancel", key=f"cancel_{rid}", on_click=cancel_edit, args=(rid,))
            return
        if st.session_state.get(f"rejecting_{rid}"):
            c1, c2 = st.columns([1, 2])
            c1.selectbox("Reason for rejecting", REJECT_REASONS, key=f"rej_reason_{rid}")
            c2.text_input("Details (optional)", key=f"rej_note_{rid}")
            e = st.container(horizontal=True)
            e.button("Reject", key=f"confirm_reject_{rid}", on_click=confirm_reject, args=(rid,),
                     type="primary", icon=":material/close:")
            e.button("Cancel", key=f"cancel_reject_{rid}", on_click=cancel_reject, args=(rid,))
            return
        actions = st.container(horizontal=True)
        if r["status"] != "added":
            actions.button("Accept", key=f"accept_{rid}", on_click=set_status, args=(rid, "accepted"),
                           icon=":material/check:",
                           disabled=r["status"] == "accepted" or in_conflict)
        actions.button("Edit", key=f"edit_{rid}", on_click=start_edit, args=(rid,),
                       icon=":material/edit:")
        actions.button("Reject", key=f"reject_{rid}", on_click=start_reject, args=(rid,),
                       icon=":material/close:", disabled=r["status"] == "rejected")


# ====================================================================== 4 · AI drafts stories

def step_stories() -> None:
    s = st.session_state
    if not s.stories:
        st.info("Approve Checkpoint 1 in **Analyst review** first.")
        return
    show_notes("stories")
    st.markdown("### User stories drafted by the AI")
    st.caption("One story per approved requirement, with Given/When/Then acceptance criteria. "
               "Only requirements approved at Checkpoint 1 were used.")
    m = st.columns(3)
    m[0].metric("User stories", len(s.stories))
    m[1].metric("Acceptance criteria", sum(len(x["criteria"]) for x in s.stories))
    m[2].metric("Approved requirements", len(approved_reqs()))
    for story in s.stories:
        render_story(story, show_tests=False)

    _, right = footer("Next, the partner lead confirms the agreed requirements before anything "
                      "is built.")
    if s.sent_to_partner:
        right.button("Go to Partner confirms  →", key="to_partner", type="primary", on_click=go,
                     args=(4,), width="stretch")
    else:
        right.button("Send to partner lead  →", key="send_partner", type="primary",
                     on_click=send_to_partner, width="stretch", icon=":material/send:")


def render_story(story: dict, show_tests: bool, ready: bool = False) -> None:
    s = st.session_state
    with st.container(border=True):
        head, badges = st.columns([3, 2])
        head.markdown(f"#### {story['id']} · {story['title']}")
        if show_tests:
            with badges:
                row = st.container(horizontal=True, horizontal_alignment="right")
                if ready:
                    row.badge("Ready to build", color="green", icon=":material/rocket_launch:")
                else:
                    row.badge("Not ready yet", color="orange", icon=":material/pending:")
        st.markdown(f"_{story['description']}_")
        drafted = {"live": "the local AI", "demo": "AI (demo run)",
                   "template": "placeholder - please review"}.get(story.get("drafted_by"), "")
        st.caption(f"Linked to {', '.join(story['req_ids'])} · Source: {'; '.join(story['sources'])}"
                   + (f" · Drafted by {drafted}" if drafted else ""))
        for c in story["criteria"]:
            st.markdown(f"**{c['id']}** · **Given** {c['given']}, **when** {c['when']}, "
                        f"**then** {c['then']}")
            if not show_tests:
                continue
            tests = [t for t in s.tests if t["criterion_id"] == c["id"]]
            if not tests:
                st.markdown(":orange[:material/warning: No test for this criterion yet.]")
                continue
            for t in tests:
                st.markdown(
                    f"<div class='test-line'><b>{t['id']}</b>"
                    f"<span class='test-tag' style='color:{TEST_COLOR.get(t['type'], '#555')}'>"
                    f"{TEST_LABEL.get(t['type'], t['type'])}</span>"
                    f"{t['steps']} &rarr; <i>{t['expected']}</i></div>", unsafe_allow_html=True)


# ====================================================================== 5 · partner confirms (CP2)

def step_partner() -> None:
    s = st.session_state
    if not (cp_valid("cp1") and s.sent_to_partner):
        st.info("The analyst sends the agreed requirements here after Checkpoint 1.")
        return
    show_notes("partner")
    st.markdown("### Partner lead review")
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    c1.caption("The build partner's lead reviews the agreed requirements and confirms each one.")
    if "partner_name" not in s:
        s.partner_name = "Partner lead"
    c2.text_input("Reviewing as", key="partner_name")

    agreed = approved_reqs()
    confirmed = sum(1 for r in agreed if partner_confirmed(r))
    m = st.columns(2)
    m[0].metric("Agreed requirements", len(agreed))
    m[1].metric("Confirmed", f"{confirmed} of {len(agreed)}")

    _, bar_r = st.columns([3, 2])
    bar_r.button("Confirm all remaining", key="pconfirm_all", on_click=partner_confirm_all,
                 icon=":material/done_all:", width="stretch",
                 disabled=confirmed == len(agreed) or cp_valid("cp2"))
    for r in agreed:
        render_partner_item(r)

    st.divider()
    render_checkpoint("cp2", cp2_conditions(), f"{partner_name()} (Partner lead)",
                      "Confirm agreed requirements", "approve_cp2", on_click=approve_cp2)
    if cp_valid("cp2"):
        _, right = st.columns([3, 2])
        right.button("Continue to Tests & export  →", key="partner_next", type="primary",
                     on_click=go, args=(5,), width="stretch")


def render_partner_item(r: dict) -> None:
    rid = r["id"]
    story = story_for(rid)
    with st.container(border=True):
        head, badges = st.columns([3, 2])
        head.markdown(f"**{rid}**" + (f" · {story['id']} {story['title']}" if story else ""))
        with badges:
            row = st.container(horizontal=True, horizontal_alignment="right")
            if partner_confirmed(r):
                row.badge("Confirmed", color="green", icon=":material/check:")
            else:
                row.badge("To confirm", color="gray", icon=":material/schedule:")
        st.markdown(f"**{r['text']}**")
        st.caption(f"Source: {source_files(r)}" + (f" · Note: {r['note']}" if r.get("note") else ""))
        if story:
            with st.expander("Story and acceptance criteria"):
                st.markdown(f"_{story['description']}_")
                for c in story["criteria"]:
                    st.markdown(f"- **Given** {c['given']}, **when** {c['when']}, "
                                f"**then** {c['then']}")
        if partner_confirmed(r) or cp_valid("cp2"):
            return
        st.button("Confirm", key=f"pconfirm_{rid}", on_click=partner_confirm, args=(rid,),
                  icon=":material/check:", type="primary")


# ====================================================================== 6 · tests and export (CP3)

def step_tests_export() -> None:
    s = st.session_state
    if not cp_valid("cp2"):
        st.info("This step unlocks after the partner lead approves Checkpoint 2.")
        return
    show_notes("tests")
    st.markdown("### Test cases")
    if not s.tests:
        with st.container(border=True):
            st.markdown(":material/auto_awesome: **The AI drafts the test cases**")
            st.caption("For every acceptance criterion: a normal case, an error case and an edge "
                       "case, followed by a coverage check.")
            if st.button("Draft test cases", key="draft_tests", type="primary",
                         icon=":material/auto_awesome:"):
                run_draft_tests()
                st.rerun()
        return

    cov = checks.coverage(s.stories, s.tests)
    covered = sum(1 for c in cov if c["tests"] > 0)
    ready = checks.ready_to_build(s.stories, s.tests, s.reqs, s.issues)
    kinds = {k: sum(1 for t in s.tests if t["type"] == k) for k in TEST_LABEL}
    m = st.columns(4)
    m[0].metric("Test cases", len(s.tests))
    m[1].metric("Normal · Error · Edge", f"{kinds['positive']} · {kinds['negative']} · "
                                          f"{kinds['boundary']}")
    m[2].metric("Criteria with a test", f"{covered} of {len(cov)}")
    m[3].metric("Ready to build", f"{len(ready)} of {len(s.stories)}")

    for story in s.stories:
        render_story(story, show_tests=True, ready=story["id"] in ready)

    with st.expander("Coverage check", icon=":material/fact_check:"):
        cdf = pd.DataFrame([{
            "Criterion": c["criterion_id"], "Story": c["story_id"],
            "Acceptance criterion": c["criterion"], "Tests": c["tests"],
            "Status": "Covered" if c["tests"] else "No test yet",
        } for c in cov])
        styled = cdf.style.apply(
            lambda row: ["background-color: rgba(234, 88, 12, 0.18)" if row["Tests"] == 0 else ""]
            * len(row), axis=1)
        st.dataframe(styled, hide_index=True, width="stretch")
        if st.button("Draft the tests again", key="make_tests",
                     help="Replaces the tests. Checkpoint 3 has to be approved again."):
            run_draft_tests()
            st.rerun()
    with st.expander("All test cases (table)", icon=":material/table_rows:"):
        st.dataframe(pd.DataFrame([{
            "Test ID": t["id"], "Story": t["story_id"], "Criterion": t["criterion_id"],
            "Type": TEST_LABEL.get(t["type"], t["type"]), "Steps": t["steps"],
            "Expected result": t["expected"],
        } for t in s.tests]), hide_index=True, width="stretch")

    # ---- checkpoint 3
    st.divider()
    if "tester_name" not in s:
        s.tester_name = "Tester"
    if not cp_valid("cp3"):
        _, c2 = st.columns([3, 2])
        c2.text_input("Approving as", key="tester_name")
    render_checkpoint("cp3", cp3_conditions(), f"{tester_name()} (Tester)", "Approve export",
                      "approve_cp3", on_click=approve_cp3)
    render_export()


def render_export() -> None:
    s = st.session_state
    st.markdown("### Export pack")
    if not cp_valid("cp3"):
        with st.container(border=True):
            st.markdown(":material/lock: **Locked until the tester approves Checkpoint 3.**")
            st.caption("Then download the stories and tests for Jira (CSV) and the agreed "
                       "version (Word), each item with its source ID.")
        return
    try:
        stories_bytes = export.stories_csv(s.stories)
        tests_bytes = export.tests_csv(s.tests, s.stories)
        docx_bytes = export.agreed_docx(PROJECT, s.reqs, s.issues, s.stories, s.tests, s.log,
                                        approvals_for_export())
    except Exception as exc:
        st.error(f"The export could not be built ({type(exc).__name__}). Please try again.")
        return

    ready = checks.ready_to_build(s.stories, s.tests, s.reqs, s.issues)
    st.success(f"**All three checkpoints approved.** {len(ready)} of {len(s.stories)} stories are "
               f"ready to build, each with its source requirement ID.", icon=":material/verified:")
    files = [
        (":material/table_chart:", "stories.csv", "One row per user story with acceptance criteria and source "
         "requirement IDs. Ready for Jira import.", stories_bytes, "text/csv", "dl_stories"),
        (":material/science:", "test_cases.csv", "One row per test, linked to its story and criterion.",
         tests_bytes, "text/csv", "dl_tests"),
        (":material/article:", "agreed_version.docx", "Agreed requirements with sources, approvals, "
         "stories, tests and every decision.",
         docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "dl_docx"),
    ]
    cols = st.columns(3, gap="medium")
    for col, (icon, name, desc, data, mime, key) in zip(cols, files):
        with col.container(border=True, height=210):
            st.markdown(f"{icon} **{name}**")
            st.caption(desc)
            st.download_button("Download", data, name, mime, key=key, type="primary",
                               icon=":material/download:", width="stretch")

    with st.expander("Preview stories.csv"):
        st.dataframe(pd.read_csv(io.BytesIO(stories_bytes), encoding="utf-8-sig"),
                     hide_index=True, width="stretch")
    with st.expander("Preview test_cases.csv"):
        st.dataframe(pd.read_csv(io.BytesIO(tests_bytes), encoding="utf-8-sig"),
                     hide_index=True, width="stretch")
    with st.expander(f"Decision log ({len(s.log)})"):
        render_log()


# ====================================================================== page

def scroll_to_top() -> None:
    """Open each step at the top of the page (Streamlit keeps the old scroll position)."""
    components.html(
        "<script>const d = window.parent.document;"
        "d.querySelectorAll('[data-testid=\"stMain\"], [data-testid=\"stAppViewContainer\"], "
        "section.main').forEach(e => e.scrollTo({top: 0}));</script>", height=0)


# ---------------------------------------------------------------- theme switching
# Streamlit applies its light/dark theme (see .streamlit/config.toml) when the page loads, and
# remembers the choice in the browser. Switching therefore saves the work in progress on the
# server, stores the choice in the browser and reloads the page, which then restores the work.

THEMES = {"Light": ":material/light_mode: Light", "Dark": ":material/dark_mode: Dark"}
SAVED_KEYS = ("docs", "reqs", "issues", "stories", "tests", "log", "notes", "inputs_changed",
              "stories_basis", "stage", "flash", "signoffs", "partner_ok",
              "sent_to_partner")


@st.cache_resource
def _saved_sessions() -> dict:
    return {}


def session_token() -> str:
    token = st.query_params.get("s")
    if not token:
        token = uuid.uuid4().hex[:12]
        st.query_params["s"] = token
    return token


def restore_after_reload() -> None:
    saved = _saved_sessions().pop(session_token(), None)
    if saved and not st.session_state.get("_restored"):
        st.session_state.update(saved)
    st.session_state["_restored"] = True


def current_theme() -> str:
    return "Dark" if st.context.theme.type == "dark" else "Light"


def choose_theme() -> None:
    choice = st.session_state.get("theme_choice")
    if choice and choice != current_theme():
        st.session_state.pending_theme = choice


def apply_pending_theme() -> None:
    choice = st.session_state.pop("pending_theme", None)
    if not choice:
        return
    saved = {k: st.session_state[k] for k in SAVED_KEYS}
    saved["flash"] = f"{choice} theme on."  # shown after the reload
    _saved_sessions()[session_token()] = saved
    components.html(
        "<script>const w = window.parent;"
        f"w.localStorage.setItem('stActiveTheme-' + w.location.pathname + '-v2', "
        f"JSON.stringify('{choice}'));"
        "w.location.reload();</script>", height=0)


def log_out() -> None:
    start_over()
    st.session_state.signed_out = True


def sign_in() -> None:
    st.session_state.signed_out = False
    flash(f"Welcome back, {USER['name']}.")


def render_signed_out() -> None:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown("<div style='height:12vh'></div>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("<div class='brand'>Spec<span>Bridge</span></div>", unsafe_allow_html=True)
            st.markdown("You have been logged out.")
            st.button(f"Log in as {USER['name']}", key="sign_in", on_click=sign_in,
                      type="primary", width="stretch", icon=":material/login:")


def main() -> None:
    init_state()
    restore_after_reload()
    if st.session_state.get("signed_out"):
        render_signed_out()
        return
    apply_pending_theme()
    if st.session_state.flash:
        st.toast(st.session_state.flash, icon=":material/check_circle:")
        st.session_state.flash = None
    render_sidebar()

    st.markdown("<div class='brand' style='font-size:2.1rem'>Spec<span>Bridge</span></div>", unsafe_allow_html=True)
    s = st.session_state
    while s.stage > 0 and not step_unlocked(s.stage):  # e.g. a checkpoint was undone
        s.stage -= 1
    render_stepper()
    st.caption(f":material/arrow_forward: {next_hint()}")
    if s.get("_shown_stage") != s.stage:
        s["_shown_stage"] = s.stage
        scroll_to_top()

    [step_inputs, step_ai_checks, step_review, step_stories, step_partner,
     step_tests_export][s.stage]()


main()
