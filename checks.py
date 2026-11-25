"""Rule-based checks done in plain Python (no AI).

- quote verification (does the quoted text really appear in the source?)
- merging duplicate requirements found in several files
- number conflicts (same subject, different numbers: "5 days" vs "10 days")
- deadline gaps ("within 2 days" but nothing says what happens if it is missed)
- weak / vague words ("fast", "user-friendly", ...)
- test coverage and "ready to build"
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

APPROVED = ("accepted", "edited", "added")

# ------------------------------------------------------------------ text helpers

_QUOTE_CHARS = {"‘": "'", "’": "'", "‚": "'", "‛": "'",
                "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
                "–": "-", "—": "-", "−": "-", " ": " ", "…": "..."}

_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                 "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
                 "twelve": "12", "fifteen": "15", "twenty": "20", "thirty": "30"}

# Note: "over" is deliberately NOT a stopword ("carry over" matters).
_STOPWORDS = set("""a an the and or of to in into on at for by with from as is are be been being
it its this that these those their there they them can could may might must shall should will would
up than then also any all each every per not no only most least more less very just""".split())


def normalise(text: str) -> str:
    """Lower-case, unify quotes and dashes, collapse whitespace."""
    for bad, good in _QUOTE_CHARS.items():
        text = text.replace(bad, good)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_outer(quote: str) -> str:
    quote = quote.strip()
    quote = quote.strip("\"'")
    quote = quote.rstrip(".;:,!? ")
    return quote.strip()


def verify_quote(quote: str, source_text: str) -> bool:
    """True if the quote appears in the source text (normalised substring match)."""
    q = _strip_outer(normalise(quote or ""))
    if len(q) < 5:
        return False
    return q in normalise(source_text or "")


def numbers_in(text: str) -> set[str]:
    """Numbers mentioned in a sentence. Number words ("ten") count as numbers."""
    t = normalise(text)
    found = set(re.findall(r"\d+(?:\.\d+)?", t))
    for word, digit in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", t):
            found.add(digit)
    return found


def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z\-]+", normalise(text))
    out = set()
    for w in words:
        if w in _STOPWORDS or w in _NUMBER_WORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]  # tiny stemmer: days -> day
        out.add(w)
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def same_topic(a: dict, b: dict) -> bool:
    ta, tb = keywords(a.get("topic", "")), keywords(b.get("topic", ""))
    return jaccard(ta, tb) >= 0.5


def _related(a: dict, b: dict) -> bool:
    """Do two requirements talk about the same subject?"""
    if same_topic(a, b) and jaccard(keywords(a["text"]), keywords(b["text"])) >= 0.3:
        return True
    return jaccard(keywords(a["text"]), keywords(b["text"])) >= 0.5


# ------------------------------------------------------------------ merging

def merge_requirements(items: list[dict], source_texts: dict[str, str]) -> list[dict]:
    """Merge extracted items from all files into one numbered list.

    `items` are dicts with text, type, quote, topic, source (file name).
    Duplicates (same subject AND same numbers) become one requirement with
    several sources. Same subject with DIFFERENT numbers stays separate, so
    the conflict check can flag it.
    """
    merged: list[dict] = []
    for item in items:
        source = {
            "file": item["source"],
            "quote": item["quote"],
            "verified": verify_quote(item["quote"], source_texts.get(item["source"], "")),
        }
        target = None
        for req in merged:
            same_numbers = numbers_in(req["text"]) == numbers_in(item["text"])
            if same_numbers and (_related(req, item) or similarity(req["text"], item["text"]) >= 0.75):
                target = req
                break
        if target is not None:
            if not any(s["file"] == source["file"] and s["quote"] == source["quote"]
                       for s in target["sources"]):
                target["sources"].append(source)
            continue
        merged.append({
            "id": "",
            "text": item["text"].strip(),
            "type": item["type"],
            "topic": item.get("topic", ""),
            "sources": [source],
            "status": "pending",
            "origin": "ai",
            "original_text": item["text"].strip(),
            "note": "",
        })
    for i, req in enumerate(merged, start=1):
        req["id"] = f"REQ-{i:02d}"
    return merged


def is_verified(req: dict) -> bool:
    return all(s.get("verified") for s in req.get("sources", [])) if req.get("sources") else True


# ------------------------------------------------------------------ issue checks

def _issue(kind: str, req_ids: list[str], explanation: str, suggestion: str, origin="check") -> dict:
    return {"id": "", "type": kind, "req_ids": req_ids, "explanation": explanation,
            "suggestion": suggestion, "origin": origin, "status": "open", "resolution": ""}


def find_number_conflicts(reqs: list[dict]) -> list[dict]:
    issues = []
    for i, a in enumerate(reqs):
        for b in reqs[i + 1:]:
            na, nb = numbers_in(a["text"]), numbers_in(b["text"])
            if not na or not nb or na == nb:
                continue
            if not _related(a, b):
                continue
            files_a = ", ".join(s["file"] for s in a["sources"])
            files_b = ", ".join(s["file"] for s in b["sources"])
            issues.append(_issue(
                "Conflict", [a["id"], b["id"]],
                f"{a['id']} and {b['id']} are about the same thing but give different numbers "
                f"({', '.join(sorted(na))} vs {', '.join(sorted(nb))}). "
                f"One comes from {files_a}, the other from {files_b}.",
                "Ask the business owner which number is correct, and record who confirmed it.",
            ))
    return issues


_DEADLINE = re.compile(
    r"\bwithin\s+(\d+|one|two|three|four|five|ten)\s+(working\s+|business\s+|calendar\s+)?"
    r"(minute|hour|day|week)s?\b")
_FALLBACK = re.compile(
    r"\b(escalat\w*|otherwise|auto(matic(ally)?)?[- ]?approv\w*|no (reply|response|answer)|"
    r"if (the |a )?(manager|approver)s? (does not|doesn't|do not|don't|fails?|has not|hasn't)|"
    r"(is|are) not (approved|answered) in time)\b")


def find_deadline_gaps(reqs: list[dict]) -> list[dict]:
    all_text = " ".join(normalise(r["text"]) + " " +
                        " ".join(normalise(s["quote"]) for s in r["sources"]) for r in reqs)
    if _FALLBACK.search(all_text):
        return []
    issues = []
    for r in reqs:
        m = _DEADLINE.search(normalise(r["text"]))
        if not m:
            continue
        issues.append(_issue(
            "Gap", [r["id"]],
            f"{r['id']} sets a deadline ({m.group(0)}), but nothing says what happens "
            f"if the deadline is missed.",
            "Ask HR: if there is no reply in time, should the request be escalated "
            "(e.g. to the manager's manager), approved automatically, or declined?",
        ))
    return issues


WEAK_WORDS = {
    "fast": "How fast? For example: 'every screen loads in under 2 seconds'.",
    "quick": "How quick? Give a time limit, e.g. 'within 2 seconds'.",
    "quickly": "How quickly? Give a time limit, e.g. 'within 2 seconds'.",
    "user-friendly": "What does user-friendly mean here? For example: 'a new member of staff "
                     "can book leave in under 1 minute without training'.",
    "user friendly": "What does user friendly mean here? Describe a task and a time limit.",
    "easy": "Easy for whom, doing what? Describe a task and how long it may take.",
    "easily": "Describe the task and how long it may take.",
    "simple": "Simple in what way? Describe the steps or the time it may take.",
    "intuitive": "Describe how this will be checked, e.g. 'no training needed for the main tasks'.",
    "flexible": "Flexible in what way? List the options that must be supported.",
    "efficient": "Efficient how? Give a measure (time, clicks, cost).",
    "seamless": "Describe what must work without interruption, and how it is checked.",
    "modern": "Describe the look or the standard to follow instead.",
    "robust": "Describe what must still work when something goes wrong.",
    "as soon as possible": "Give a real deadline.",
    "asap": "Give a real deadline.",
    "appropriate": "Say exactly what is appropriate.",
    "etc": "List all items instead of 'etc.'.",
}


def find_weak_words(reqs: list[dict]) -> list[dict]:
    issues = []
    for r in reqs:
        t = normalise(r["text"])
        hits = [w for w in WEAK_WORDS if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", t)]
        if not hits:
            continue
        words = ", ".join(f"'{w}'" for w in hits)
        issues.append(_issue(
            "Unclear", [r["id"]],
            f"{r['id']} uses vague words ({words}). People will understand them differently, "
            f"so the build team can't build it and testers can't check it.",
            " ".join(WEAK_WORDS[w] for w in hits),
        ))
    return issues


def _issue_family(kind: str) -> str:
    return "Unclear" if kind in ("Unclear", "Not testable") else kind


def merge_issues(python_issues: list[dict], ai_issues: list[dict], valid_ids: set[str]) -> list[dict]:
    """Python findings first, then AI findings that add something new. Numbered ISS-01..."""
    out, seen = [], set()
    for issue in python_issues + ai_issues:
        ids = [i for i in issue["req_ids"] if i in valid_ids]
        if not ids:
            continue
        issue = {**issue, "req_ids": sorted(set(ids))}
        key = (_issue_family(issue["type"]), tuple(issue["req_ids"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    for i, issue in enumerate(out, start=1):
        issue["id"] = f"ISS-{i:02d}"
    return out


def run_python_checks(reqs: list[dict]) -> list[dict]:
    return find_number_conflicts(reqs) + find_deadline_gaps(reqs) + find_weak_words(reqs)


# ------------------------------------------------------------------ coverage

def coverage(stories: list[dict], tests: list[dict]) -> list[dict]:
    """One row per acceptance criterion with the number of tests that cover it."""
    rows = []
    for s in stories:
        for c in s["criteria"]:
            n = sum(1 for t in tests if t["story_id"] == s["id"] and t["criterion_id"] == c["id"])
            rows.append({"story_id": s["id"], "criterion_id": c["id"],
                         "criterion": criterion_text(c), "tests": n})
    return rows


def criterion_text(c: dict) -> str:
    return f"Given {c['given']}, when {c['when']}, then {c['then']}"


def ready_to_build(stories: list[dict], tests: list[dict], reqs: list[dict],
                   issues: list[dict]) -> list[str]:
    """Story IDs that are approved, fully tested and have no open issues."""
    status = {r["id"]: r["status"] for r in reqs}
    open_ids = {rid for i in issues if i["status"] == "open" for rid in i["req_ids"]}
    cov = coverage(stories, tests)
    ready = []
    for s in stories:
        if not all(status.get(rid) in APPROVED for rid in s["req_ids"]):
            continue
        if any(rid in open_ids for rid in s["req_ids"]):
            continue
        rows = [r for r in cov if r["story_id"] == s["id"]]
        if rows and all(r["tests"] > 0 for r in rows):
            ready.append(s["id"])
    return ready
