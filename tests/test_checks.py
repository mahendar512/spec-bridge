import checks
from checks import (coverage, find_deadline_gaps, find_number_conflicts, find_weak_words,
                    merge_requirements, numbers_in, ready_to_build, verify_quote)

SOURCE = "Staff can carry over a maximum of 5 unused\nannual leave days into the next calendar year."


def test_verify_quote_normalises_case_space_and_quotes():
    assert verify_quote("staff can carry over a maximum of 5 unused annual leave days", SOURCE)
    assert verify_quote("“Staff can carry over a maximum of 5 unused annual leave days.”", SOURCE)
    assert verify_quote("The staff member’s line manager", "The staff member's line manager must")


def test_verify_quote_rejects_invented_text():
    assert not verify_quote("Staff can carry over 10 days", SOURCE)
    assert not verify_quote("", SOURCE)


def test_numbers_include_number_words():
    assert numbers_in("carry over ten days") == {"10"}
    assert numbers_in("a half day is 0.5 days") == {"0.5"}


def _item(text, topic, source, quote=None):
    return {"text": text, "type": "rule", "quote": quote or text, "topic": topic, "source": source}


def test_duplicates_merge_but_different_numbers_do_not():
    items = [
        _item("Staff can see their current leave balance in the app at any time.",
              "leave balance visibility", "policy.docx"),
        _item("Staff can carry over at most 5 unused annual leave days into the next year.",
              "leave carry over", "policy.docx"),
        _item("Staff must see their remaining leave balance on the app home screen.",
              "leave balance visibility", "mail.eml"),
        _item("Staff can carry over up to 10 unused leave days into the next year.",
              "leave carry over", "mail.eml"),
    ]
    texts = {"policy.docx": items[0]["text"] + " " + items[1]["text"],
             "mail.eml": items[2]["text"] + " " + items[3]["text"]}
    reqs = merge_requirements(items, texts)
    assert [r["id"] for r in reqs] == ["REQ-01", "REQ-02", "REQ-03"]
    assert [s["file"] for s in reqs[0]["sources"]] == ["policy.docx", "mail.eml"]
    assert all(s["verified"] for r in reqs for s in r["sources"])
    conflicts = find_number_conflicts(reqs)
    assert len(conflicts) == 1 and conflicts[0]["req_ids"] == ["REQ-02", "REQ-03"]


def test_unverified_quote_is_marked():
    reqs = merge_requirements([_item("Staff get 30 days", "annual leave", "p",
                                     quote="Staff get 30 days")], {"p": "Staff get 20 days"})
    assert reqs[0]["sources"][0]["verified"] is False
    assert not checks.is_verified(reqs[0])


def test_unrelated_numbers_are_not_a_conflict():
    reqs = merge_requirements([
        _item("Full-time staff get 20 days of paid annual leave per calendar year.",
              "annual leave entitlement", "p"),
        _item("Staff can carry over at most 5 unused annual leave days into the next year.",
              "leave carry over", "p"),
    ], {"p": ""})
    assert find_number_conflicts(reqs) == []


def _req(rid, text):
    return {"id": rid, "text": text, "type": "rule", "topic": "", "status": "pending",
            "origin": "ai", "sources": [{"file": "p", "quote": text, "verified": True}]}


def test_deadline_gap_found_and_cleared_by_fallback_rule():
    reqs = [_req("REQ-01", "The line manager must approve each request within 2 working days.")]
    gaps = find_deadline_gaps(reqs)
    assert len(gaps) == 1 and gaps[0]["type"] == "Gap"
    reqs.append(_req("REQ-02", "If the manager does not reply, the request is escalated."))
    assert find_deadline_gaps(reqs) == []


def test_weak_words():
    issues = find_weak_words([_req("REQ-01", "The app should be fast and user-friendly."),
                              _req("REQ-02", "Pages load in under 2 seconds.")])
    assert len(issues) == 1
    assert "'fast'" in issues[0]["explanation"] and "'user-friendly'" in issues[0]["explanation"]


def test_merge_issues_dedupes_and_drops_unknown_ids():
    py = [{"type": "Unclear", "req_ids": ["REQ-01"], "explanation": "py", "suggestion": "",
           "origin": "check", "status": "open", "resolution": ""}]
    ai = [{**py[0], "type": "Not testable", "explanation": "ai", "origin": "ai"},
          {**py[0], "type": "Gap", "req_ids": ["REQ-99"]},
          {**py[0], "type": "Gap", "req_ids": ["REQ-01"], "origin": "ai"}]
    merged = checks.merge_issues(py, ai, {"REQ-01"})
    assert [(i["id"], i["type"]) for i in merged] == [("ISS-01", "Unclear"), ("ISS-02", "Gap")]


def test_coverage_and_ready_to_build():
    stories = [{"id": "US-01", "req_ids": ["REQ-01"], "criteria": [
        {"id": "AC-01-1", "given": "g", "when": "w", "then": "t"},
        {"id": "AC-01-2", "given": "g", "when": "w", "then": "t"}]}]
    tests = [{"id": "TC-001", "story_id": "US-01", "criterion_id": "AC-01-1"}]
    reqs = [{**_req("REQ-01", "x"), "status": "accepted"}]
    assert [c["tests"] for c in coverage(stories, tests)] == [1, 0]
    assert ready_to_build(stories, tests, reqs, []) == []
    tests.append({"id": "TC-002", "story_id": "US-01", "criterion_id": "AC-01-2"})
    assert ready_to_build(stories, tests, reqs, []) == ["US-01"]
    assert ready_to_build(stories, tests, reqs, [{"status": "open", "req_ids": ["REQ-01"]}]) == []
