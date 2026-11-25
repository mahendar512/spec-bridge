"""SpecBridge - turn scattered documents and emails into one agreed list of requirements.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import inspect
import io
from datetime import datetime

import pandas as pd
import streamlit as st

import ai
import checks
import export
import prompts
from create_samples import build_all
from parsing import SUPPORTED_TYPES, ParsedDoc, parse_file, parse_path

PROJECT = {
    "name": "Staff leave app",
    "company": "Larkfield Retail (5,000 staff)",
    "partner": "Partner A",
    "analyst": "Alex Morgan",
    "email": "specbridge+leave-app@company.com",
}

STATUS_BADGE = {
    "pending": ":gray[Waiting for review]",
    "accepted": ":green[Accepted]",
    "edited": ":blue[Edited]",
    "rejected": ":red[Rejected]",
    "added": ":violet[Added by analyst]",
}
ISSUE_ICON = {"Conflict": "🔴", "Gap": "🟡", "Unclear": "🟠", "Not testable": "🟠"}

st.set_page_config(page_title="SpecBridge", page_icon="🌉", layout="wide")


# ====================================================================== state helpers

def init_state() -> None:
    defaults = {"docs": [], "reqs": [], "issues": [], "stories": [], "tests": [], "log": [],
                "notes": {}, "health": None, "inputs_changed": False, "stories_basis": None}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


@st.cache_resource
def get_provider() -> ai.OllamaProvider:
    return ai.OllamaProvider()


def check_health(force: bool = False) -> tuple[bool, str]:
    if force or st.session_state.health is None:
        with st.spinner("Checking the local AI…"):
            st.session_state.health = get_provider().health_check()
    return st.session_state.health


def add_log(action: str, item: str, detail: str = "") -> None:
    st.session_state.log.append({"time": datetime.now().strftime("%H:%M:%S"),
                                 "action": action, "item": item, "detail": detail})


def docs() -> list[ParsedDoc]:
    return [ParsedDoc.from_dict(d) for d in st.session_state.docs]


def find_req(rid: str) -> dict | None:
    return next((r for r in st.session_state.reqs if r["id"] == rid), None)


def find_issue(iid: str) -> dict | None:
    return next((i for i in st.session_state.issues if i["id"] == iid), None)


def open_issues_for(rid: str) -> list[dict]:
    return [i for i in st.session_state.issues if i["status"] == "open" and rid in i["req_ids"]]


def approved_basis() -> list:
    return sorted((r["id"], r["text"]) for r in st.session_state.reqs if r["status"] in checks.APPROVED)


def source_files(req: dict) -> str:
    return ", ".join(s["file"] for s in req["sources"]) or "Added by analyst"


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
        alive = [i for i in issue["req_ids"]
                 if (find_req(i) or {}).get("status") != "rejected"]
        if issue["type"] == "Conflict" and len(alive) == 1:
            _resolve(issue, f"{rid} was rejected, so {alive[0]} stands.")
        elif not alive:
            _resolve(issue, "The requirement was rejected.")


def _resolve(issue: dict, resolution: str) -> None:
    issue["status"] = "resolved"
    issue["resolution"] = resolution
    add_log("Issue resolved", issue["id"], resolution)


def start_edit(rid: str) -> None:
    req = find_req(rid)
    st.session_state[f"editing_{rid}"] = True
    st.session_state[f"edit_text_{rid}"] = req["text"]


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
    blocked = {rid for i in st.session_state.issues
               if i["status"] == "open" and i["type"] == "Conflict" for rid in i["req_ids"]}
    for r in st.session_state.reqs:
        if r["status"] == "pending" and r["id"] not in blocked:
            set_status(r["id"], "accepted")


def resolve_conflict(iid: str) -> None:
    issue = find_issue(iid)
    choice = st.session_state.get(f"pick_{iid}") or ""
    note = (st.session_state.get(f"note_{iid}") or "").strip() or "Decided by analyst"
    if choice.startswith("Something else"):
        custom = (st.session_state.get(f"custom_{iid}") or "").strip()
        if not custom:
            st.session_state.notes["review"] = ["Please type the agreed wording before saving the decision."]
            return
        winner = find_req(issue["req_ids"][0])
        old = winner["text"]
        winner["text"], winner["status"] = custom, "edited"
        add_log("Edited", winner["id"], f"\"{old}\" → \"{custom}\"")
    else:
        winner = find_req(choice.split(":")[0].strip())
        winner["status"] = "accepted"
    winner["note"] = note
    add_log("Accepted", winner["id"], f"{winner['text']} ({note})")
    for rid in issue["req_ids"]:
        if rid != winner["id"]:
            loser = find_req(rid)
            loser["status"] = "rejected"
            loser["note"] = f"Superseded by {winner['id']}"
            add_log("Rejected", rid, f"Superseded by {winner['id']} ({note})")
    _resolve(issue, f"Kept {winner['id']}: \"{winner['text']}\" – {note}")
    st.session_state.notes.pop("review", None)


def resolve_issue(iid: str) -> None:
    issue = find_issue(iid)
    note = (st.session_state.get(f"res_note_{iid}") or "").strip() or "Resolved by analyst"
    _resolve(issue, note)


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


# ====================================================================== AI runs

def spinner_step(label: str):
    return st.spinner(label)


def run_find_requirements(demo: bool) -> None:
    provider = None if demo else get_provider()
    try:
        reqs, notes = ai.run_extraction(docs(), provider, demo, step=spinner_step)
        issues, issue_notes = ai.run_issue_check(reqs, provider, demo, step=spinner_step)
    except Exception as exc:  # never crash the demo
        st.session_state.notes["extract"] = [f"Something went wrong while reading the inputs "
                                             f"({type(exc).__name__}: {exc}). Please try again."]
        return
    st.session_state.update(reqs=reqs, issues=issues, stories=[], tests=[], inputs_changed=False,
                            stories_basis=None)
    st.session_state.notes["extract"] = notes
    st.session_state.notes["issues"] = issue_notes
    for k in [k for k in st.session_state.keys() if str(k).startswith("editing_")]:
        st.session_state[k] = False
    add_log("AI run", "Requirements", f"{len(reqs)} requirements and {len(issues)} issues found"
            + (" (demo mode)" if demo else ""))


def run_issue_recheck(demo: bool) -> None:
    provider = None if demo else get_provider()
    try:
        issues, notes = ai.run_issue_check(st.session_state.reqs, provider, demo, step=spinner_step)
    except Exception as exc:
        st.session_state.notes["issues"] = [f"The problem check failed ({type(exc).__name__}). Please try again."]
        return
    st.session_state.issues = issues
    st.session_state.notes["issues"] = notes
    add_log("AI run", "Issues", f"{len(issues)} issues found")


def run_make_stories(demo: bool) -> None:
    provider = None if demo else get_provider()
    try:
        stories, notes = ai.run_stories(st.session_state.reqs, provider, demo, step=spinner_step)
    except Exception as exc:
        st.session_state.notes["stories"] = [f"Writing stories failed ({type(exc).__name__}). Please try again."]
        return
    st.session_state.update(stories=stories, tests=[], stories_basis=approved_basis())
    st.session_state.notes["stories"] = notes
    st.session_state.notes.pop("tests", None)
    add_log("AI run", "User stories", f"{len(stories)} stories drafted")


def run_make_tests(demo: bool) -> None:
    provider = None if demo else get_provider()
    try:
        tests, notes = ai.run_tests(st.session_state.stories, provider, demo, step=spinner_step)
    except Exception as exc:
        st.session_state.notes["tests"] = [f"Writing tests failed ({type(exc).__name__}). Please try again."]
        return
    st.session_state.tests = tests
    st.session_state.notes["tests"] = notes
    add_log("AI run", "Test cases", f"{len(tests)} test cases drafted")


def show_notes(key: str) -> None:
    for note in st.session_state.notes.get(key) or []:
        st.warning(note, icon="⚠️")


# ====================================================================== sidebar

def render_sidebar() -> bool:
    sb = st.sidebar
    sb.title("🌉 SpecBridge")
    sb.caption("The AI only drafts. A person approves everything.")

    ok, message = check_health()
    if ok:
        sb.success(message, icon="✅")
    else:
        sb.warning(message, icon="🔌")
        sb.caption("Demo mode is on, so you can still try every step.")
    sb.button("Check the local AI again", on_click=check_health, kwargs={"force": True},
              key="health_again")
    if "demo" not in st.session_state:
        st.session_state.demo = not ok
    demo = sb.toggle("Demo mode", key="demo",
                     help="Replays saved results from demo_data/, so the demo works without the local AI.")
    if demo:
        sb.info("Demo mode: showing saved results instead of asking the local AI.", icon="🎬")

    sb.divider()
    sb.subheader("Summary")
    reqs, issues = st.session_state.reqs, st.session_state.issues
    count = lambda status: sum(1 for r in reqs if r["status"] == status)  # noqa: E731
    c1, c2 = sb.columns(2)
    c1.metric("Requirements found", sum(1 for r in reqs if r["origin"] == "ai"))
    c2.metric("Issues flagged", len(issues),
              delta=f"{sum(1 for i in issues if i['status'] == 'open')} open", delta_color="off")
    c1.metric("Accepted", count("accepted"))
    c2.metric("Edited", count("edited"))
    c1.metric("Rejected", count("rejected"))
    c2.metric("Added by hand", count("added"))
    ready = checks.ready_to_build(st.session_state.stories, st.session_state.tests, reqs, issues)
    sb.metric("Stories ready to build", f"{len(ready)} of {len(st.session_state.stories)}",
              help="Approved, every acceptance criterion has a test, and no open issues.")
    return demo


# ====================================================================== tabs

def tab_project() -> None:
    st.subheader("Project setup")
    c1, c2 = st.columns(2)
    c1.text_input("Project", PROJECT["name"], disabled=True)
    c1.text_input("Company", PROJECT["company"], disabled=True)
    c1.text_input("Build partner", PROJECT["partner"], disabled=True)
    c2.text_input("Business analyst", PROJECT["analyst"], disabled=True)
    c2.text_input("Project email address", PROJECT["email"], disabled=True)
    c2.caption("Simulated: in a real setup, any email copied to this address would arrive "
               "here as a new input automatically.")
    st.markdown("""
**How SpecBridge works**
1. **Inputs**: add the documents and emails where requirements are scattered.
2. **Requirements**: the AI pulls out each requirement with the exact words it came from.
3. **Issues found**: conflicts, gaps and unclear wording are flagged before anything is built.
4. **Analyst review**: you accept, edit or reject every item and settle each conflict.
5. **Stories, tests and export**: drafted only from what you approved.
""")
    with st.expander("Show the AI prompts (from prompts.py)"):
        for name, value in inspect.getmembers(prompts):
            if name.isupper() and isinstance(value, str):
                st.markdown(f"**{name}**")
                st.code(value, language="text")


def tab_inputs() -> None:
    st.subheader("Inputs")
    c1, c2 = st.columns([1, 2])
    c1.button("📂 Load sample inputs", key="load_samples", on_click=load_samples, type="primary",
              help="HR_Leave_Policy.docx and HR_email_carry_over.eml")
    c2.file_uploader("Or upload your own files", type=SUPPORTED_TYPES, accept_multiple_files=True,
                     key="uploader", on_change=on_upload)
    show_notes("inputs")
    if not st.session_state.docs:
        st.info("No inputs yet. Click **Load sample inputs** or upload a file.")
        return
    for doc in docs():
        icon = "✉️" if doc.kind == "email" else "📄"
        with st.container(border=True):
            h1, h2 = st.columns([5, 1])
            h1.markdown(f"#### {icon} {doc.name}")
            h2.button("Remove", key=f"remove_{doc.name}", on_click=remove_doc, args=(doc.name,))
            if doc.error:
                st.error(doc.error)
                continue
            if doc.kind == "email":
                m = doc.meta
                st.markdown(f"**From:** {m.get('from', '')}  \n**To:** {m.get('to', '')}  \n"
                            f"**Cc:** {m.get('cc', '')}  \n**Date:** {m.get('date', '')}  \n"
                            f"**Subject:** {m.get('subject', '')}")
            with st.container(height=220):
                st.text(doc.text)


def tab_requirements(demo: bool) -> None:
    st.subheader("Requirements")
    ready_docs = [d for d in docs() if not d.error]
    if st.button("🔎 Find requirements", key="find_reqs", type="primary", disabled=not ready_docs):
        run_find_requirements(demo)
        st.rerun()
    if not ready_docs:
        st.info("Add some inputs first (step 2).")
    if st.session_state.inputs_changed:
        st.warning("The inputs changed since the requirements were found. "
                   "Click **Find requirements** again to include the changes.")
    show_notes("extract")
    reqs = [r for r in st.session_state.reqs if r["origin"] == "ai"]
    if not reqs:
        return
    files = {s["file"] for r in reqs for s in r["sources"]}
    unverified = [r for r in reqs if not checks.is_verified(r)]
    merged = [r for r in reqs if len({s["file"] for s in r["sources"]}) > 1]
    m1, m2, m3 = st.columns(3)
    m1.metric("Requirements", len(reqs), help=f"from {len(files)} files")
    m2.metric("Merged duplicates", len(merged))
    m3.metric("Sources not verified", len(unverified))

    rows = []
    for r in reqs:
        rows.append({
            "ID": r["id"],
            "Requirement": r["text"],
            "Type": r["type"],
            "Source file": "\n".join(s["file"] for s in r["sources"]),
            "Quoted source text": "\n".join(f"“{s['quote']}”" for s in r["sources"]),
            "Source check": "✅ Verified" if checks.is_verified(r) else "🟠 Source not verified",
        })
    df = pd.DataFrame(rows)
    styled = df.style.map(
        lambda v: "color: #c2410c; font-weight: 600" if "not verified" in str(v) else "color: #15803d",
        subset=["Source check"])
    st.dataframe(styled, hide_index=True, width="stretch",
                 column_config={"Requirement": st.column_config.TextColumn(width="large"),
                                "Quoted source text": st.column_config.TextColumn(width="large")})
    for r in merged:
        st.success(f"**{r['id']}** appeared in {len({s['file'] for s in r['sources']})} files and was "
                   f"merged into one item: {source_files(r)}.", icon="🔗")
    for r in unverified:
        st.markdown(f":orange[**{r['id']}: Source not verified.** The quoted words could not be found "
                    f"in the source file. Check this item by hand before accepting it.]")


def tab_issues(demo: bool) -> None:
    st.subheader("Issues found")
    if not st.session_state.reqs:
        st.info("Find the requirements first (step 3).")
        return
    if st.button("Check again", key="recheck_issues"):
        run_issue_recheck(demo)
        st.rerun()
    show_notes("issues")
    issues = st.session_state.issues
    if not issues:
        st.success("No problems found.")
        return
    counts = {t: sum(1 for i in issues if i["type"] == t) for t in ISSUE_ICON}
    cols = st.columns(4)
    for col, (t, n) in zip(cols, counts.items()):
        col.metric(f"{ISSUE_ICON[t]} {t}", n)
    for issue in issues:
        render_issue(issue)


def render_issue(issue: dict, show_resolution: bool = True) -> None:
    with st.container(border=True):
        status = ":green[Resolved]" if issue["status"] == "resolved" else ":orange[Open]"
        st.markdown(f"**{issue['id']} · {ISSUE_ICON.get(issue['type'], '')} {issue['type']}** · "
                    f"{', '.join(issue['req_ids'])} · {status}")
        st.write(issue["explanation"])
        involved = [r for r in (find_req(i) for i in issue["req_ids"]) if r]
        if issue["type"] == "Conflict" and len(involved) > 1:
            for col, r in zip(st.columns(len(involved)), involved):
                with col.container(border=True):
                    col_quote(r)
        else:
            for r in involved:
                col_quote(r)
        st.markdown(f"💡 **Suggested fix or question to ask:** {issue['suggestion']}")
        st.caption("Found by: " + ("the AI check" if issue["origin"] == "ai" else "the built-in check"))
        if show_resolution and issue["status"] == "resolved":
            st.success(f"Decision: {issue['resolution']}")


def col_quote(r: dict) -> None:
    st.markdown(f"**{r['id']}**: {r['text']}")
    for s in r["sources"]:
        st.markdown(f"> “{s['quote']}”  \n> — *{s['file']}*")


def tab_review() -> None:
    st.subheader("Analyst review")
    reqs = st.session_state.reqs
    if not reqs:
        st.info("Find the requirements first (step 3).")
        return
    show_notes("review")

    # ---- conflicts
    conflicts = [i for i in st.session_state.issues if i["type"] == "Conflict"]
    if conflicts:
        st.markdown("#### 1. Settle the conflicts")
    for issue in conflicts:
        with st.container(border=True):
            st.markdown(f"**{issue['id']} · 🔴 Conflict** · {issue['explanation']}")
            if issue["status"] == "resolved":
                st.success(f"Decision: {issue['resolution']}")
                continue
            options = [f"{rid}: {find_req(rid)['text']}  ({source_files(find_req(rid))})"
                       for rid in issue["req_ids"]] + ["Something else (type it below)"]
            st.radio("Which one is correct?", options, key=f"pick_{issue['id']}")
            if (st.session_state.get(f"pick_{issue['id']}") or "").startswith("Something else"):
                st.text_input("Agreed wording", key=f"custom_{issue['id']}")
            if f"note_{issue['id']}" not in st.session_state:
                st.session_state[f"note_{issue['id']}"] = "Confirmed by HR"
            st.text_input("Note (who confirmed it?)", key=f"note_{issue['id']}")
            st.button("Save decision", key=f"decide_{issue['id']}", on_click=resolve_conflict,
                      args=(issue["id"],), type="primary")

    # ---- requirements
    st.markdown("#### 2. Accept, edit or reject each requirement")
    st.button("Accept all remaining (except open conflicts)", key="accept_rest",
              on_click=accept_remaining)
    for r in reqs:
        rid = r["id"]
        with st.container(border=True):
            left, b1, b2, b3 = st.columns([7, 1.2, 1.2, 1.2])
            left.markdown(f"**{rid}** · {r['type']} · {STATUS_BADGE[r['status']]}"
                          + (f" · _{r['note']}_" if r.get("note") else ""))
            left.write(r["text"])
            if r["sources"]:
                verified = "" if checks.is_verified(r) else " · :orange[Source not verified]"
                left.caption(f"Source: {source_files(r)}{verified}")
            for issue in open_issues_for(rid):
                left.markdown(f":orange[⚠️ Open issue {issue['id']} ({issue['type']})]")
            if r["status"] != "added":
                b1.button("Accept", key=f"accept_{rid}", on_click=set_status, args=(rid, "accepted"),
                          disabled=r["status"] == "accepted", width="stretch")
            b2.button("Edit", key=f"edit_{rid}", on_click=start_edit, args=(rid,),
                      width="stretch")
            b3.button("Reject", key=f"reject_{rid}", on_click=set_status, args=(rid, "rejected"),
                      disabled=r["status"] == "rejected", width="stretch")
            if st.session_state.get(f"editing_{rid}"):
                st.text_area("New wording", key=f"edit_text_{rid}")
                e1, e2, _ = st.columns([1, 1, 6])
                e1.button("Save", key=f"save_{rid}", on_click=save_edit, args=(rid,), type="primary")
                e2.button("Cancel", key=f"cancel_{rid}", on_click=cancel_edit, args=(rid,))

    # ---- other open issues
    others = [i for i in st.session_state.issues if i["type"] != "Conflict" and i["status"] == "open"]
    st.markdown("#### 3. Close the other open issues")
    if not others:
        st.success("No other open issues.")
    for issue in others:
        render_issue(issue, show_resolution=False)
        c1, c2 = st.columns([5, 1])
        c1.text_input(f"How was {issue['id']} resolved?", key=f"res_note_{issue['id']}",
                      placeholder="e.g. Added REQ-12 after checking with HR")
        c2.button("Mark resolved", key=f"resolve_{issue['id']}", on_click=resolve_issue,
                  args=(issue["id"],))

    # ---- add requirement
    st.markdown("#### 4. Add anything the AI missed")
    with st.container(border=True):
        st.text_area("Requirement", key="new_req_text",
                     placeholder="e.g. If the line manager has not answered within 2 working days, "
                                 "the request goes to the manager's own manager.")
        c1, c2, c3 = st.columns([2, 4, 2])
        c1.selectbox("Type", ["functional", "rule", "non-functional"], key="new_req_type")
        c2.text_input("Where did it come from?", key="new_req_note",
                      placeholder="e.g. Agreed with HR lead on a call")
        c3.button("➕ Add requirement", key="add_req", on_click=add_requirement)

    # ---- log
    st.markdown("#### Decision log")
    if st.session_state.log:
        st.dataframe(pd.DataFrame(st.session_state.log), hide_index=True, width="stretch")
    else:
        st.caption("Nothing recorded yet.")


def tab_stories(demo: bool) -> None:
    st.subheader("User stories")
    reqs = st.session_state.reqs
    approved = [r for r in reqs if r["status"] in checks.APPROVED]
    pending = [r for r in reqs if r["status"] == "pending"]
    if not approved:
        st.info("Accept at least one requirement first (step 5). "
                "Stories are written only from approved requirements.")
        return
    st.caption(f"{len(approved)} approved requirements will be turned into stories.")
    if pending:
        st.warning(f"{len(pending)} requirements are still waiting for review "
                   f"({', '.join(r['id'] for r in pending)}). They will be left out.")
    if st.button("✍️ Write user stories", key="make_stories", type="primary"):
        run_make_stories(demo)
        st.rerun()
    show_notes("stories")
    stories = st.session_state.stories
    if stories and st.session_state.stories_basis != approved_basis():
        st.info("The approved requirements changed since these stories were written. "
                "Click **Write user stories** again to bring them up to date.")
    for s in stories:
        with st.container(border=True):
            st.markdown(f"#### {s['id']} · {s['title']}")
            st.markdown(f"_{s['description']}_")
            for c in s["criteria"]:
                st.markdown(f"- **{c['id']}** **Given** {c['given']}, **when** {c['when']}, "
                            f"**then** {c['then']}")
            drafted = {"live": "the local AI", "demo": "saved demo run",
                       "template": "placeholder - please review"}.get(s.get("drafted_by"), "")
            st.caption(f"Linked to {', '.join(s['req_ids'])} · Source: {'; '.join(s['sources'])}"
                       + (f" · Drafted by: {drafted}" if drafted else ""))
            for rid in s["req_ids"]:
                for issue in open_issues_for(rid):
                    st.markdown(f":orange[⚠️ {rid} still has an open issue ({issue['id']} {issue['type']})]")


def tab_tests(demo: bool) -> None:
    st.subheader("Test cases")
    stories = st.session_state.stories
    if not stories:
        st.info("Write the user stories first (step 6).")
        return
    if st.button("🧪 Write test cases", key="make_tests", type="primary"):
        run_make_tests(demo)
        st.rerun()
    show_notes("tests")
    tests = st.session_state.tests
    cov = checks.coverage(stories, tests)
    covered = sum(1 for c in cov if c["tests"] > 0)
    st.metric("Acceptance criteria with at least one test", f"{covered} of {len(cov)}")
    if tests:
        st.dataframe(pd.DataFrame([{
            "Test ID": t["id"], "Story": t["story_id"], "Criterion": t["criterion_id"],
            "Type": t["type"].capitalize(), "Steps": t["steps"], "Expected result": t["expected"],
        } for t in tests]), hide_index=True, width="stretch",
            column_config={"Steps": st.column_config.TextColumn(width="large"),
                           "Expected result": st.column_config.TextColumn(width="large")})
    st.markdown("#### Coverage check")
    cdf = pd.DataFrame([{
        "Criterion": c["criterion_id"], "Story": c["story_id"], "Acceptance criterion": c["criterion"],
        "Tests": c["tests"], "Status": "✅ Covered" if c["tests"] else "⚠️ No test yet",
    } for c in cov])
    styled = cdf.style.apply(
        lambda row: ["background-color: rgba(234, 88, 12, 0.18)" if row["Tests"] == 0 else ""] * len(row),
        axis=1)
    st.dataframe(styled, hide_index=True, width="stretch")


def tab_export() -> None:
    st.subheader("Export")
    s = st.session_state
    if not s.stories:
        st.info("Write the user stories first (step 6). Test cases are optional but recommended.")
        return
    try:
        stories_bytes = export.stories_csv(s.stories)
        tests_bytes = export.tests_csv(s.tests, s.stories)
        docx_bytes = export.agreed_docx(PROJECT, s.reqs, s.issues, s.stories, s.tests, s.log)
    except Exception as exc:
        st.error(f"The export could not be built ({type(exc).__name__}). Please try again.")
        return
    c1, c2, c3 = st.columns(3)
    c1.download_button("⬇️ stories.csv", stories_bytes, "stories.csv", "text/csv",
                       key="dl_stories", width="stretch")
    c2.download_button("⬇️ test_cases.csv", tests_bytes, "test_cases.csv", "text/csv",
                       key="dl_tests", width="stretch")
    c3.download_button("⬇️ agreed_version.docx", docx_bytes, "agreed_version.docx",
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       key="dl_docx", width="stretch")
    st.caption("The CSV files use a Jira-friendly layout: one row per story or test, "
               "with the acceptance criteria in one cell.")
    open_issues = [i for i in s.issues if i["status"] == "open"]
    if open_issues:
        st.warning(f"{len(open_issues)} issues are still open. They are listed in the Word document "
                   "under 'Open issues'.")
    with st.expander("Preview stories.csv"):
        st.dataframe(pd.read_csv(io.BytesIO(stories_bytes), encoding="utf-8-sig"),
                     hide_index=True, width="stretch")
    with st.expander("Preview test_cases.csv"):
        if s.tests:
            st.dataframe(pd.read_csv(io.BytesIO(tests_bytes), encoding="utf-8-sig"),
                         hide_index=True, width="stretch")
        else:
            st.caption("No test cases yet.")


# ====================================================================== page

def progress_bar() -> None:
    s = st.session_state
    review_done = bool(s.reqs) and not any(r["status"] == "pending" for r in s.reqs) and \
        not any(i["type"] == "Conflict" and i["status"] == "open" for i in s.issues)
    steps = [("add inputs", bool(s.docs)), ("find requirements", bool(s.reqs)),
             ("finish the review", review_done), ("write stories", bool(s.stories)),
             ("write tests", bool(s.tests))]
    done = sum(1 for _, ok in steps if ok)
    nxt = next((name for name, ok in steps if not ok), None)
    st.progress(done / len(steps), text=f"Progress: {done} of {len(steps)} steps done · "
                + (f"next: {nxt}" if nxt else "ready to export"))


def main() -> None:
    init_state()
    demo = render_sidebar()
    st.title("🌉 SpecBridge")
    st.caption(f"{PROJECT['name']} · {PROJECT['company']} · built by {PROJECT['partner']}")
    progress_bar()
    tabs = st.tabs(["1 · Project", "2 · Inputs", "3 · Requirements", "4 · Issues found",
                    "5 · Analyst review", "6 · User stories", "7 · Test cases", "8 · Export"])
    with tabs[0]:
        tab_project()
    with tabs[1]:
        tab_inputs()
    with tabs[2]:
        tab_requirements(demo)
    with tabs[3]:
        tab_issues(demo)
    with tabs[4]:
        tab_review()
    with tabs[5]:
        tab_stories(demo)
    with tabs[6]:
        tab_tests(demo)
    with tabs[7]:
        tab_export()


main()
