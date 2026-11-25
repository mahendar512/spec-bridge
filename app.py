"""SpecBridge - turn scattered documents and emails into one agreed list of requirements.

Run with:  streamlit run app.py

The app is a guided 4-step flow:
  1. Add sources      - load the sample project or upload files, preview them
  2. Review           - requirements, the problems found and the analyst's decisions, together
  3. Stories & tests  - drafted only from approved requirements, tests shown under each criterion
  4. Export           - stories.csv, test_cases.csv, agreed_version.docx

It always runs in demo mode: results are replayed from demo_data/*.json.
"""

from __future__ import annotations

import inspect
import io
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import ai
import checks
import export
import prompts
from create_samples import build_all
from parsing import SUPPORTED_TYPES, ParsedDoc, parse_file, parse_path

# Always demo mode: AI results are replayed from demo_data/ (see README).
DEMO_MODE = True

PROJECT = {
    "name": "Staff leave app",
    "company": "Larkfield Retail (5,000 staff)",
    "partner": "Partner A",
    "analyst": "Alex Morgan",
    "email": "specbridge+leave-app@company.com",
}

STEPS = ["Add sources", "Review", "Stories & tests", "Export"]

STATUS_BADGE = {  # label, colour, icon
    "pending": ("To review", "gray", ":material/schedule:"),
    "accepted": ("Accepted", "green", ":material/check:"),
    "edited": ("Edited", "blue", ":material/edit:"),
    "rejected": ("Rejected", "red", ":material/close:"),
    "added": ("Added by analyst", "violet", ":material/add:"),
}
ISSUE_ICON = {"Conflict": "🔴", "Gap": "🟡", "Unclear": "🟠", "Not testable": "🟠"}
TEST_ICON = {"positive": "✅", "negative": "⛔", "boundary": "📏"}

st.set_page_config(page_title="SpecBridge", page_icon="🌉", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2.2rem; max-width: 1150px;}
blockquote {margin: 0.2rem 0 0.4rem 0; padding: 0.1rem 0 0.1rem 0.8rem; opacity: 0.9;}
div[data-testid="stMetricValue"] {font-size: 1.7rem;}
</style>
""", unsafe_allow_html=True)


# ====================================================================== state helpers

def init_state() -> None:
    defaults = {"docs": [], "reqs": [], "issues": [], "stories": [], "tests": [], "log": [],
                "notes": {}, "inputs_changed": False, "stories_basis": None, "stage": 0,
                "flash": None}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def add_log(action: str, item: str, detail: str = "") -> None:
    st.session_state.log.append({"time": datetime.now().strftime("%H:%M:%S"),
                                 "action": action, "item": item, "detail": detail})


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


def open_conflicts() -> list[dict]:
    return [i for i in st.session_state.issues if i["type"] == "Conflict" and i["status"] == "open"]


def approved_reqs() -> list[dict]:
    return [r for r in st.session_state.reqs if r["status"] in checks.APPROVED]


def pending_reqs() -> list[dict]:
    return [r for r in st.session_state.reqs if r["status"] == "pending"]


def approved_basis() -> list:
    return sorted((r["id"], r["text"]) for r in approved_reqs())


def source_files(req: dict) -> str:
    return ", ".join(s["file"] for s in req["sources"]) or "Added by analyst"


def review_done() -> bool:
    return bool(st.session_state.reqs) and not pending_reqs() and not open_conflicts()


def step_unlocked(i: int) -> bool:
    s = st.session_state
    return [True, bool(s.reqs), bool(s.stories), bool(s.stories)][i]


def step_done(i: int) -> bool:
    s = st.session_state
    return [bool(s.reqs), review_done(), bool(s.tests), False][i]


def go(i: int) -> None:
    st.session_state.stage = i


# ====================================================================== actions (callbacks)

def add_doc(doc: ParsedDoc) -> None:
    st.session_state.docs = [d for d in st.session_state.docs if d["name"] != doc.name] + [doc.to_dict()]
    if st.session_state.reqs:
        st.session_state.inputs_changed = True


def load_samples() -> None:
    try:
        for path in build_all():
            add_doc(parse_path(str(path)))
        add_log("Inputs loaded", "Sample inputs", "HR_Leave_Policy.docx, HR_email_carry_over.eml")
        st.session_state.notes.pop("inputs", None)
    except Exception as exc:
        st.session_state.notes["inputs"] = [f"Could not create the sample files ({type(exc).__name__})."]


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
    note = (st.session_state.get(f"res_note_{iid}") or "").strip() or "Resolved by analyst"
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
                            stories_basis=None)
    st.session_state.notes["extract"] = notes + issue_notes
    for k in [k for k in st.session_state.keys() if str(k).startswith("editing_")]:
        st.session_state[k] = False
    add_log("AI run", "Requirements", f"{len(reqs)} requirements and {len(issues)} issues found")
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
    add_log("AI run", "Issues", f"{len(issues)} issues found")


def run_draft_stories_and_tests() -> bool:
    try:
        stories, notes = ai.run_stories(st.session_state.reqs, _provider(), DEMO_MODE, step=spinner_step)
        tests, test_notes = ai.run_tests(stories, _provider(), DEMO_MODE, step=spinner_step)
    except Exception as exc:
        st.session_state.notes["stories"] = [f"Drafting failed ({type(exc).__name__}). Please try again."]
        return False
    st.session_state.update(stories=stories, tests=tests, stories_basis=approved_basis())
    st.session_state.notes["stories"] = notes + test_notes
    add_log("AI run", "User stories", f"{len(stories)} stories drafted")
    add_log("AI run", "Test cases", f"{len(tests)} test cases drafted")
    flash(f"Drafted {len(stories)} user stories and {len(tests)} test cases.")
    return True


def show_notes(key: str) -> None:
    for note in st.session_state.notes.get(key) or []:
        st.warning(note, icon="⚠️")


# ====================================================================== sidebar

def render_sidebar() -> None:
    sb = st.sidebar
    sb.markdown("## 🌉 SpecBridge")
    sb.caption("Scattered requirements in. One agreed list out. "
               "The AI only drafts. A person approves everything.")

    with sb.container(border=True):
        st.markdown(f"**{PROJECT['name']}**")
        st.caption(f"{PROJECT['company']}  \nBuilt by {PROJECT['partner']}  \n"
                   f"Analyst: {PROJECT['analyst']}")
        st.caption(f"✉️ `{PROJECT['email']}`  \n_Simulated: emails copied here would arrive "
                   f"as new sources automatically._")

    reqs, issues = st.session_state.reqs, st.session_state.issues
    count = lambda status: sum(1 for r in reqs if r["status"] == status)  # noqa: E731
    open_n = sum(1 for i in issues if i["status"] == "open")
    ready = checks.ready_to_build(st.session_state.stories, st.session_state.tests, reqs, issues)
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
    sb.metric("Stories ready to build", f"{len(ready)} of {len(st.session_state.stories)}",
              help="Approved, every acceptance criterion has a test, and no open issues.")

    with sb.expander("How SpecBridge works"):
        st.markdown(
            "1. **Add sources**: documents and emails where requirements are scattered.\n"
            "2. **Review**: the AI pulls out each requirement with the exact words it came "
            "from, flags conflicts, gaps and unclear wording, and you decide.\n"
            "3. **Stories & tests**: drafted only from what you approved.\n"
            "4. **Export**: CSV files for Jira and a Word document of the agreed version.")
    with sb.expander("Show the AI prompts"):
        for name, value in inspect.getmembers(prompts):
            if name.isupper() and isinstance(value, str):
                st.markdown(f"**{name}**")
                st.code(value, language="text")
    if st.session_state.docs:
        sb.button("↺ Start over", key="start_over", on_click=start_over, width="stretch")


# ====================================================================== stepper

def render_stepper() -> None:
    cols = st.columns(len(STEPS))
    for i, (col, name) in enumerate(zip(cols, STEPS)):
        mark = "✓" if step_done(i) and i != st.session_state.stage else str(i + 1)
        col.button(f"{mark}  {name}", key=f"nav_{i}", on_click=go, args=(i,),
                   type="primary" if i == st.session_state.stage else "secondary",
                   disabled=not step_unlocked(i), width="stretch")


def next_hint() -> str:
    s = st.session_state
    if not s.docs:
        return "Start by loading the sample project or uploading your own files."
    if not s.reqs:
        return "Next: find the requirements in your sources."
    if open_conflicts():
        return f"Next: decide {len(open_conflicts())} conflict(s)."
    if pending_reqs():
        return f"Next: review {len(pending_reqs())} requirement(s)."
    if not s.stories:
        return "Review finished. Next: draft the user stories and tests."
    if s.stage == 3:
        return "Download your files below and share them with the build partner."
    return "Everything is drafted. Download your files in Export."


# ====================================================================== step 1: sources

def step_sources() -> None:
    st.markdown("### Where are the requirements?")
    st.caption("Add the documents and emails for this project. "
               "Accepted: .docx, .pdf, .txt, .eml, .msg")
    c1, c2 = st.columns(2, gap="medium")
    with c1.container(border=True, height=190):
        st.markdown("**📦 Use the sample project**")
        st.caption("An HR leave policy and a later email from the HR lead.")
        st.button("Load sample sources", key="load_samples", on_click=load_samples,
                  type="primary", icon=":material/folder_open:")
    with c2.container(border=True, height=190):
        st.file_uploader("**⬆️ Or upload your own files**", type=SUPPORTED_TYPES,
                         accept_multiple_files=True, key="uploader", on_change=on_upload)
    show_notes("inputs")

    loaded = docs()
    if loaded:
        st.markdown(f"#### Sources ({len(loaded)})")
        cols = st.columns(2, gap="medium")
        for n, doc in enumerate(loaded):
            with cols[n % 2].container(border=True):
                render_source(doc)

    st.divider()
    ready = [d for d in loaded if not d.error]
    if st.session_state.inputs_changed:
        st.warning("The sources changed since the requirements were found. "
                   "Find them again to include the changes.")
    left, right = st.columns([3, 2])
    left.caption("SpecBridge reads each file separately, pulls out every requirement with its "
                 "exact source words, and checks them for problems.")
    if right.button("Find requirements  →", key="find_reqs", type="primary", disabled=not ready,
                    width="stretch"):
        if run_find_requirements():
            st.session_state.stage = 1
        st.rerun()
    show_notes("extract")


def render_source(doc: ParsedDoc) -> None:
    icon = "✉️" if doc.kind == "email" else "📄"
    h1, h2 = st.columns([5, 1])
    h1.markdown(f"**{icon} {doc.name}**")
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


# ====================================================================== step 2: review

def step_review() -> None:
    reqs = st.session_state.reqs
    if not reqs:
        st.info("Find the requirements first.")
        return
    show_notes("extract")
    show_notes("review")
    if st.session_state.inputs_changed:
        st.warning("The sources changed since these requirements were found. "
                   "Go back to **Add sources** and find them again.")

    issues = st.session_state.issues
    open_n = sum(1 for i in issues if i["status"] == "open")
    m = st.columns(4)
    m[0].metric("Requirements", len(reqs))
    m[1].metric("Decisions needed", len(open_conflicts()))
    m[2].metric("Open issues", open_n)
    m[3].metric("Waiting for review", len(pending_reqs()))

    # ---- conflicts first: they block everything else
    for issue in [i for i in issues if i["type"] == "Conflict"]:
        render_conflict(issue)

    # ---- the requirement list
    st.markdown("### Requirements")
    bar_l, bar_r = st.columns([3, 2])
    with bar_l:
        choice = st.segmented_control("Show", ["All", "To review", "Approved", "Rejected"],
                                      default="All", key="req_filter", label_visibility="collapsed")
    bar_r.button("Accept all remaining", key="accept_rest", on_click=accept_remaining,
                 icon=":material/done_all:", width="stretch", disabled=not pending_reqs(),
                 help="Accepts every requirement still waiting, except ones in an open conflict.")
    wanted = {"To review": ("pending",), "Approved": checks.APPROVED,
              "Rejected": ("rejected",)}.get(choice or "All")
    shown = [r for r in reqs if wanted is None or r["status"] in wanted]
    if not shown:
        st.caption("Nothing to show here.")
    for r in shown:
        render_requirement(r)

    # ---- extras
    with st.expander("➕ Add a requirement the AI missed"):
        st.text_area("Requirement", key="new_req_text",
                     placeholder="e.g. Managers get a reminder when a leave request is waiting.")
        c1, c2 = st.columns([1, 2])
        c1.selectbox("Type", ["functional", "rule", "non-functional"], key="new_req_type")
        c2.text_input("Where did it come from?", key="new_req_note",
                      placeholder="e.g. Agreed with HR lead on a call")
        st.button("Add requirement", key="add_req", on_click=add_requirement, type="primary")

    with st.expander("📋 Table view with sources"):
        render_requirements_table()

    resolved = [i for i in issues if i["status"] == "resolved"]
    with st.expander(f"✅ Resolved issues ({len(resolved)})"):
        if not resolved:
            st.caption("None yet.")
        for i in resolved:
            st.markdown(f"**{i['id']} · {ISSUE_ICON.get(i['type'], '')} {i['type']}** "
                        f"({', '.join(i['req_ids'])}): {i['explanation']}  \n"
                        f":green[Decision: {i['resolution']}]")
        st.button("Run the checks again", key="recheck_issues", on_click=run_issue_recheck,
                  help="Runs the problem checks again. Earlier decisions on issues are cleared.")

    with st.expander(f"🕑 Decision log ({len(st.session_state.log)})"):
        render_log()

    # ---- next step
    st.divider()
    approved = approved_reqs()
    left, right = st.columns([3, 2])
    if open_conflicts():
        left.caption(":orange[Decide the conflict above first. "
                     "Stories are drafted only from requirements you approved.]")
    elif pending_reqs():
        left.caption(f"{len(pending_reqs())} requirement(s) still waiting for review will be left out.")
    else:
        left.caption(f"All set: {len(approved)} approved requirement(s) will become user stories.")
    if right.button("Draft stories and tests  →", key="make_stories", type="primary",
                    disabled=not approved, width="stretch"):
        if run_draft_stories_and_tests():
            st.session_state.stage = 2
        st.rerun()


def render_conflict(issue: dict) -> None:
    iid = issue["id"]
    involved = [r for r in (find_req(i) for i in issue["req_ids"]) if r]
    with st.container(border=True):
        if issue["status"] == "resolved":
            st.markdown(f"**{ISSUE_ICON['Conflict']} {iid} · Conflict settled**")
            st.caption(issue["resolution"])
            return
        st.markdown("### 🔴 Decision needed: which one is right?")
        st.markdown(issue["explanation"])
        if f"note_{iid}" not in st.session_state:
            st.session_state[f"note_{iid}"] = "Confirmed by HR"
        st.text_input("Who confirmed the decision?", key=f"note_{iid}")
        cols = st.columns(len(involved), gap="medium")
        for col, r in zip(cols, involved):
            with col.container(border=True):
                st.markdown(f"**{r['id']}** · {source_files(r)}")
                for s in r["sources"]:
                    st.markdown(f"> “{s['quote']}”")
                st.caption(r["text"])
                st.button(f"Keep {r['id']}", key=f"keep_{iid}_{r['id']}", type="primary",
                          on_click=keep_in_conflict, args=(iid, r["id"]), width="stretch")
        with st.expander("Neither: type the agreed wording"):
            st.text_input("Agreed wording", key=f"custom_{iid}")
            st.button("Use this wording", key=f"custom_save_{iid}", on_click=custom_conflict,
                      args=(iid,))
        st.caption(f"💡 {issue['suggestion']}  ·  Found by "
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
            n_files = len({s["file"] for s in r["sources"]})
            if n_files > 1:
                row.badge(f"Merged from {n_files} files", color="blue", icon=":material/merge:")
            if r["sources"] and not checks.is_verified(r):
                row.badge("Source not verified", color="orange", icon=":material/warning:")
        st.markdown(f"**{r['text']}**" if r["status"] != "rejected" else f"~~{r['text']}~~")
        for s in r["sources"]:
            mark = "" if s["verified"] else " · :orange[quote not found in source]"
            st.markdown(f"> “{s['quote']}”  \n> <small>— {s['file']}</small>{mark}",
                        unsafe_allow_html=True)
        if r.get("note"):
            st.caption(f"Note: {r['note']}")

        for issue in open_issues_for(rid):
            if issue["type"] == "Conflict":
                st.caption(f":orange[🔴 Part of conflict {issue['id']}: decide it above.]")
                continue
            with st.container(border=True):
                st.markdown(f"**{ISSUE_ICON.get(issue['type'], '')} {issue['type']}** · "
                            f"{issue['explanation']}")
                st.caption(f"💡 {issue['suggestion']}")
                c1, c2 = st.columns([4, 1])
                c1.text_input("How was it resolved?", key=f"res_note_{issue['id']}",
                              label_visibility="collapsed",
                              placeholder="How was it resolved? (or edit the requirement)")
                c2.button("Resolve", key=f"resolve_{issue['id']}", on_click=resolve_issue,
                          args=(issue["id"],), width="stretch")

        if st.session_state.get(f"editing_{rid}"):
            st.text_area("New wording", key=f"edit_text_{rid}")
            e = st.container(horizontal=True)
            e.button("Save", key=f"save_{rid}", on_click=save_edit, args=(rid,), type="primary")
            e.button("Cancel", key=f"cancel_{rid}", on_click=cancel_edit, args=(rid,))
            return
        actions = st.container(horizontal=True)
        if r["status"] != "added":
            actions.button("Accept", key=f"accept_{rid}", on_click=set_status, args=(rid, "accepted"),
                           icon=":material/check:",
                           disabled=r["status"] == "accepted" or in_conflict)
        actions.button("Edit", key=f"edit_{rid}", on_click=start_edit, args=(rid,),
                       icon=":material/edit:")
        actions.button("Reject", key=f"reject_{rid}", on_click=set_status, args=(rid, "rejected"),
                       icon=":material/close:", disabled=r["status"] == "rejected")


def render_requirements_table() -> None:
    rows = [{
        "ID": r["id"],
        "Requirement": r["text"],
        "Type": r["type"],
        "Status": STATUS_BADGE[r["status"]][0],
        "Source file": "\n".join(s["file"] for s in r["sources"]) or "Added by analyst",
        "Quoted source text": "\n".join(f"“{s['quote']}”" for s in r["sources"]),
        "Source check": ("✅ Verified" if checks.is_verified(r) else "🟠 Source not verified")
        if r["sources"] else "—",
    } for r in st.session_state.reqs]
    styled = pd.DataFrame(rows).style.map(
        lambda v: "color: #c2410c; font-weight: 600" if "not verified" in str(v) else "",
        subset=["Source check"])
    st.dataframe(styled, hide_index=True, width="stretch",
                 column_config={"Requirement": st.column_config.TextColumn(width="large"),
                                "Quoted source text": st.column_config.TextColumn(width="large")})


def render_log() -> None:
    if st.session_state.log:
        st.dataframe(pd.DataFrame(st.session_state.log), hide_index=True, width="stretch")
    else:
        st.caption("Nothing recorded yet.")


# ====================================================================== step 3: stories & tests

def step_stories() -> None:
    s = st.session_state
    if not s.stories:
        st.info("Approve some requirements in **Review** first.")
        return
    show_notes("stories")
    if s.stories_basis != approved_basis():
        c1, c2 = st.columns([3, 1])
        c1.info("The approved requirements changed since these were drafted.")
        if c2.button("Draft again", key="redraft", width="stretch"):
            run_draft_stories_and_tests()
            st.rerun()

    cov = checks.coverage(s.stories, s.tests)
    covered = sum(1 for c in cov if c["tests"] > 0)
    ready = checks.ready_to_build(s.stories, s.tests, s.reqs, s.issues)
    m = st.columns(4)
    m[0].metric("User stories", len(s.stories))
    m[1].metric("Test cases", len(s.tests))
    m[2].metric("Criteria with a test", f"{covered} of {len(cov)}")
    m[3].metric("Ready to build", f"{len(ready)} of {len(s.stories)}")

    for story in s.stories:
        render_story(story, story["id"] in ready)

    with st.expander("📋 All test cases (table)"):
        if s.tests:
            st.dataframe(pd.DataFrame([{
                "Test ID": t["id"], "Story": t["story_id"], "Criterion": t["criterion_id"],
                "Type": t["type"].capitalize(), "Steps": t["steps"], "Expected result": t["expected"],
            } for t in s.tests]), hide_index=True, width="stretch")
        else:
            st.caption("No test cases yet.")
    with st.expander("🧭 Coverage check"):
        cdf = pd.DataFrame([{
            "Criterion": c["criterion_id"], "Story": c["story_id"],
            "Acceptance criterion": c["criterion"], "Tests": c["tests"],
            "Status": "✅ Covered" if c["tests"] else "⚠️ No test yet",
        } for c in cov])
        styled = cdf.style.apply(
            lambda row: ["background-color: rgba(234, 88, 12, 0.18)" if row["Tests"] == 0 else ""]
            * len(row), axis=1)
        st.dataframe(styled, hide_index=True, width="stretch")
        st.button("Draft the tests again", key="make_tests", on_click=redo_tests)

    st.divider()
    left, right = st.columns([3, 2])
    left.caption("Stories and tests are drafts. Download them for Partner A in the next step.")
    right.button("Continue to export  →", key="go_export", type="primary", on_click=go, args=(3,),
                 width="stretch")


def redo_tests() -> None:
    s = st.session_state
    try:
        tests, notes = ai.run_tests(s.stories, _provider(), DEMO_MODE)
    except Exception as exc:
        s.notes["stories"] = [f"Drafting the tests failed ({type(exc).__name__})."]
        return
    s.tests = tests
    s.notes["stories"] = notes
    add_log("AI run", "Test cases", f"{len(tests)} test cases drafted")


def render_story(story: dict, ready: bool) -> None:
    s = st.session_state
    with st.container(border=True):
        head, badges = st.columns([3, 2])
        head.markdown(f"#### {story['id']} · {story['title']}")
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
        for rid in story["req_ids"]:
            for issue in open_issues_for(rid):
                st.caption(f":orange[⚠️ {rid} still has an open issue ({issue['id']} {issue['type']}).]")
        for c in story["criteria"]:
            st.markdown(f"**{c['id']}** · **Given** {c['given']}, **when** {c['when']}, "
                        f"**then** {c['then']}")
            tests = [t for t in s.tests if t["criterion_id"] == c["id"]]
            if not tests:
                st.markdown(":orange[⚠️ No test for this criterion yet.]")
                continue
            for t in tests:
                st.markdown(
                    f"<div style='margin-left:1.2rem; font-size:0.9rem'>"
                    f"{TEST_ICON.get(t['type'], '')} <b>{t['id']}</b> "
                    f"<span style='opacity:0.7'>{t['type']}</span> · {t['steps']} "
                    f"→ <i>{t['expected']}</i></div>", unsafe_allow_html=True)


# ====================================================================== step 4: export

def step_export() -> None:
    s = st.session_state
    if not s.stories:
        st.info("Draft the user stories first.")
        return
    try:
        stories_bytes = export.stories_csv(s.stories)
        tests_bytes = export.tests_csv(s.tests, s.stories)
        docx_bytes = export.agreed_docx(PROJECT, s.reqs, s.issues, s.stories, s.tests, s.log)
    except Exception as exc:
        st.error(f"The export could not be built ({type(exc).__name__}). Please try again.")
        return

    ready = checks.ready_to_build(s.stories, s.tests, s.reqs, s.issues)
    open_issues = [i for i in s.issues if i["status"] == "open"]
    if len(ready) == len(s.stories) and not open_issues:
        st.success(f"**All {len(ready)} stories are ready to build.** Every requirement was "
                   f"reviewed, every conflict settled, and every acceptance criterion has a test.",
                   icon="🎉")
    else:
        st.warning(f"{len(ready)} of {len(s.stories)} stories are ready to build. "
                   f"{len(open_issues)} issue(s) are still open; they are listed in the Word "
                   f"document under 'Open issues'.")

    files = [
        ("📊", "stories.csv", "One row per user story, with acceptance criteria. Ready for Jira import.",
         stories_bytes, "text/csv", "dl_stories"),
        ("🧪", "test_cases.csv", "One row per test, linked to its story and criterion.",
         tests_bytes, "text/csv", "dl_tests"),
        ("📝", "agreed_version.docx", "“Agreed requirements – Staff leave app – v1.0”: requirements "
         "with sources, resolved issues, stories, tests and every decision.",
         docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "dl_docx"),
    ]
    cols = st.columns(3, gap="medium")
    for col, (icon, name, desc, data, mime, key) in zip(cols, files):
        with col.container(border=True, height=210):
            st.markdown(f"**{icon} {name}**")
            st.caption(desc)
            st.download_button("Download", data, name, mime, key=key, type="primary",
                               icon=":material/download:", width="stretch")

    with st.expander("Preview stories.csv"):
        st.dataframe(pd.read_csv(io.BytesIO(stories_bytes), encoding="utf-8-sig"),
                     hide_index=True, width="stretch")
    with st.expander("Preview test_cases.csv"):
        if s.tests:
            st.dataframe(pd.read_csv(io.BytesIO(tests_bytes), encoding="utf-8-sig"),
                         hide_index=True, width="stretch")
        else:
            st.caption("No test cases yet.")
    with st.expander(f"Decision log ({len(s.log)})"):
        render_log()


# ====================================================================== page

def scroll_to_top() -> None:
    """Open each step at the top of the page (Streamlit keeps the old scroll position)."""
    components.html(
        "<script>const d = window.parent.document;"
        "d.querySelectorAll('[data-testid=\"stMain\"], [data-testid=\"stAppViewContainer\"], "
        "section.main').forEach(e => e.scrollTo({top: 0}));</script>", height=0)


def main() -> None:
    init_state()
    if st.session_state.flash:
        st.toast(st.session_state.flash, icon="✅")
        st.session_state.flash = None
    render_sidebar()

    st.markdown("# 🌉 SpecBridge")
    st.caption(f"{PROJECT['name']} · {PROJECT['company']} · built by {PROJECT['partner']}")
    if not step_unlocked(st.session_state.stage):
        st.session_state.stage = 0
    render_stepper()
    st.caption(f"👉 {next_hint()}")
    if st.session_state.get("_shown_stage") != st.session_state.stage:
        st.session_state["_shown_stage"] = st.session_state.stage
        scroll_to_top()

    [step_sources, step_review, step_stories, step_export][st.session_state.stage]()


main()
