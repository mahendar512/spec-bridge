"""AI layer: one small provider interface (Ollama only for now), JSON schemas,
the four pipeline steps, and demo mode.

Design rules (see README):
- separate small calls: extraction (per file), issues, stories (per requirement), tests (per story)
- structured output: the Pydantic schema is passed to Ollama as `format`, temperature 0
- invalid JSON -> retry once -> fall back to demo data for that step, with a visible note
- demo mode replays demo_data/*.json; a successful live step overwrites them
"""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, ContextManager, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator

import prompts
from checks import (APPROVED, merge_issues, merge_requirements, normalise, run_python_checks,
                    similarity)
from parsing import ParsedDoc

BASE_DIR = Path(__file__).resolve().parent
DEMO_DIR = BASE_DIR / "demo_data"
PROJECT_NAME = "Staff leave app"

T = TypeVar("T", bound=BaseModel)
StepFn = Callable[[str], ContextManager]


def _no_step(_label: str) -> ContextManager:
    return nullcontext()


# ====================================================================== config

def load_config() -> dict:
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass
    return {
        "host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "model": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        "timeout": float(os.getenv("OLLAMA_TIMEOUT", "180")),
    }


# ====================================================================== schemas

class ExtractedReq(BaseModel):
    text: str = Field(min_length=3)
    type: Literal["functional", "non-functional", "rule"]
    quote: str = Field(min_length=3)
    topic: str = ""

    @field_validator("type", mode="before")
    @classmethod
    def _norm_type(cls, v):
        v = str(v).strip().lower().replace("_", "-").replace(" ", "-")
        if v in ("nonfunctional", "non-functional-requirement", "quality"):
            return "non-functional"
        if v in ("business-rule", "policy", "constraint"):
            return "rule"
        return v


class ExtractionResult(BaseModel):
    requirements: list[ExtractedReq]


class AIIssue(BaseModel):
    type: Literal["Conflict", "Gap", "Unclear", "Not testable"]
    req_ids: list[str] = Field(min_length=1)
    explanation: str
    suggestion: str

    @field_validator("type", mode="before")
    @classmethod
    def _norm_type(cls, v):
        v = str(v).strip().lower().replace("-", " ").replace("_", " ")
        return {"conflict": "Conflict", "gap": "Gap", "unclear": "Unclear",
                "not testable": "Not testable", "untestable": "Not testable"}.get(v, v)


class IssueResult(BaseModel):
    issues: list[AIIssue]


class Criterion(BaseModel):
    given: str
    when: str
    then: str


class StoryDraft(BaseModel):
    title: str
    role: str
    goal: str
    benefit: str
    criteria: list[Criterion] = Field(min_length=1, max_length=5)


class TestDraft(BaseModel):
    __test__ = False  # not a pytest test class
    criterion_number: int = Field(ge=1)
    type: Literal["positive", "negative", "boundary"]
    steps: str
    expected: str

    @field_validator("type", mode="before")
    @classmethod
    def _norm_type(cls, v):
        return str(v).strip().lower()


class TestResult(BaseModel):
    __test__ = False
    tests: list[TestDraft]


# ====================================================================== provider

class AIStepError(Exception):
    """An AI call failed (not running, timeout, or invalid answer twice)."""


class AIProvider(Protocol):
    name: str

    def health_check(self) -> tuple[bool, str]: ...

    def generate(self, system: str, user: str, schema: type[T]) -> T: ...


class OllamaProvider:
    name = "Ollama"

    def __init__(self, host: str | None = None, model: str | None = None,
                 timeout: float | None = None):
        cfg = load_config()
        self.host = host or cfg["host"]
        self.model = model or cfg["model"]
        self.timeout = timeout or cfg["timeout"]

    def _client(self, timeout: float):
        import ollama
        return ollama.Client(host=self.host, timeout=timeout)

    def health_check(self) -> tuple[bool, str]:
        hint = f"Start Ollama and run: ollama pull {self.model}"
        try:
            listing = self._client(timeout=3).list()
        except Exception:
            return False, f"The local AI (Ollama) is not running at {self.host}. {hint}"
        names = set()
        for m in getattr(listing, "models", None) or listing.get("models", []):
            name = getattr(m, "model", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else None)
            if name:
                names.add(name)
                if name.endswith(":latest"):
                    names.add(name[: -len(":latest")])
        if self.model not in names:
            return False, f"Ollama is running, but the model '{self.model}' is not downloaded. {hint}"
        return True, f"Local AI ready: {self.model}"

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        last_error = "unknown error"
        for _attempt in range(2):  # first try + one retry
            try:
                resp = self._client(self.timeout).chat(
                    model=self.model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    format=schema.model_json_schema(),
                    options={"temperature": 0, "num_ctx": 8192},
                )
            except Exception as exc:  # connection refused, timeout, model missing...
                raise AIStepError(_friendly_error(exc)) from exc
            content = resp.message.content if hasattr(resp, "message") else resp["message"]["content"]
            try:
                return schema.model_validate_json(content)
            except ValidationError:
                last_error = "the AI answer did not match the expected format"
        raise AIStepError(f"{last_error} (tried twice)")


def _friendly_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "the local AI took too long to answer"
    if "connect" in text or "refused" in text:
        return "the local AI (Ollama) is not reachable"
    if "not found" in text:
        return "the AI model is not downloaded"
    return f"the local AI returned an error ({type(exc).__name__})"


# ====================================================================== demo data

def _demo_path(name: str) -> Path:
    return DEMO_DIR / name


def load_demo(name: str):
    path = _demo_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_demo(name: str, data) -> None:
    try:
        DEMO_DIR.mkdir(exist_ok=True)
        _demo_path(name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # saving demo data is a nice-to-have, never fatal


def _update_demo(name: str, new_entries: dict) -> None:
    data = load_demo(name) or {}
    data.update(new_entries)
    save_demo(name, data)


def _extract_demo_name(file_name: str) -> str:
    return f"extract_{file_name}.json"


# ====================================================================== step 1: extraction

def run_extraction(docs: list[ParsedDoc], provider: AIProvider | None, demo: bool,
                   step: StepFn = _no_step) -> tuple[list[dict], list[str]]:
    """One AI call per file, then merge in Python. Returns (requirements, notes)."""
    items, notes = [], []
    # Documents before emails, then by name: REQ numbers must not depend on upload order
    # (saved demo stories and tests are keyed by REQ number).
    for doc in sorted(docs, key=lambda d: (d.kind == "email", d.name.lower())):
        if doc.error:
            notes.append(f"Skipped {doc.name}: {doc.error}")
            continue
        with step(f"Reading {doc.name}…"):
            result, note = _extract_one(doc, provider, demo)
        if note:
            notes.append(note)
        for r in result.requirements:
            items.append({**r.model_dump(), "source": doc.name})
    reqs = merge_requirements(items, {d.name: d.text for d in docs})
    return reqs, notes


def _extract_one(doc: ParsedDoc, provider, demo: bool) -> tuple[ExtractionResult, str | None]:
    demo_name = _extract_demo_name(doc.name)
    if not demo and provider is not None:
        try:
            result = provider.generate(
                prompts.EXTRACTION_SYSTEM,
                prompts.EXTRACTION_USER.format(file_name=doc.name, text=doc.text),
                ExtractionResult)
            save_demo(demo_name, result.model_dump())
            return result, None
        except AIStepError as exc:
            reason = f"The local AI could not read {doc.name} ({exc})."
    else:
        reason = None
    data = load_demo(demo_name)
    if data is None:
        msg = (f"{reason} " if reason else "") + \
              f"There are no saved demo results for {doc.name}, so it was skipped."
        if not reason:
            msg += " Turn off demo mode to read it with the local AI."
        return ExtractionResult(requirements=[]), msg
    note = f"{reason} Showing saved demo results for this file instead." if reason else None
    return ExtractionResult.model_validate(data), note


# ====================================================================== step 2: issues

def format_reqs_for_prompt(reqs: list[dict]) -> str:
    return "\n".join(f"{r['id']} ({r['type']}): {r['text']}  "
                     f"[source: {', '.join(s['file'] for s in r['sources'])}]" for r in reqs)


def _with_req_texts(data: dict, reqs: list[dict]) -> dict:
    """Save each finding with the wording of its requirements, not only their numbers."""
    by_id = {r["id"]: r["original_text"] for r in reqs}
    for issue in data["issues"]:
        issue["req_texts"] = [by_id.get(i, "") for i in issue["req_ids"]]
    return data


def _match_saved_issues(data: dict, reqs: list[dict]) -> dict:
    """Point saved findings at the current requirements by wording.

    A finding is replayed only if every requirement it names is found again with the
    same wording, so a saved conflict can never land on two unrelated requirements.
    """
    by_text = {normalise(r["original_text"]): r["id"] for r in reqs}
    kept = []
    for issue in data.get("issues", []):
        texts = issue.get("req_texts")
        if texts is None:
            kept.append(issue)  # old file without wording: keep the numbers as saved
            continue
        ids = [by_text.get(normalise(t)) for t in texts]
        if ids and all(ids):
            kept.append({**issue, "req_ids": ids})
    return {"issues": kept}


def run_issue_check(reqs: list[dict], provider: AIProvider | None, demo: bool,
                    step: StepFn = _no_step) -> tuple[list[dict], list[str]]:
    """Python checks + one AI call, merged. Returns (issues, notes)."""
    notes = []
    python_issues = run_python_checks(reqs)
    ai_result, reason = None, None
    with step("Checking for conflicts, gaps and unclear wording…"):
        if not demo and provider is not None and reqs:
            try:
                ai_result = provider.generate(
                    prompts.ISSUES_SYSTEM,
                    prompts.ISSUES_USER.format(project=PROJECT_NAME,
                                               requirements=format_reqs_for_prompt(reqs)),
                    IssueResult)
                save_demo("issues.json", _with_req_texts(ai_result.model_dump(), reqs))
            except AIStepError as exc:
                reason = f"The AI problem check failed ({exc})."
    if ai_result is None:
        data = load_demo("issues.json")
        if data is not None:
            ai_result = IssueResult.model_validate(_match_saved_issues(data, reqs))
            if reason:
                notes.append(f"{reason} Showing saved demo findings instead. "
                             f"The built-in checks still ran.")
        elif reason:
            notes.append(f"{reason} Only the built-in checks ran.")
    ai_issues = []
    for i in (ai_result.issues if ai_result else []):
        ai_issues.append({"id": "", "type": i.type, "req_ids": i.req_ids,
                          "explanation": i.explanation, "suggestion": i.suggestion,
                          "origin": "ai", "status": "open", "resolution": ""})
    issues = merge_issues(python_issues, ai_issues, {r["id"] for r in reqs})
    return issues, notes


# ====================================================================== step 3: stories

def run_stories(reqs: list[dict], provider: AIProvider | None, demo: bool,
                step: StepFn = _no_step) -> tuple[list[dict], list[str]]:
    """One story per approved requirement. Returns (stories, notes)."""
    approved = [r for r in reqs if r["status"] in APPROVED]
    stories, notes, live_saved = [], [], {}
    demo_data = load_demo("stories.json") or {}
    for n, req in enumerate(approved, start=1):
        with step(f"Writing story for {req['id']}…"):
            draft, note, source = _story_one(req, provider, demo, demo_data)
        if note:
            notes.append(note)
        if source == "live":
            live_saved[req["id"]] = {**draft.model_dump(), "req_text": req["text"]}
        sid = f"US-{n:02d}"
        stories.append({
            "id": sid,
            "title": draft.title,
            "role": draft.role,
            "goal": draft.goal,
            "benefit": draft.benefit,
            "description": f"As a {draft.role}, I want {draft.goal}, so that {draft.benefit}.",
            "req_ids": [req["id"]],
            "sources": sorted({s["file"] for s in req["sources"]}) or ["Added by analyst"],
            "criteria": [{"id": f"AC-{n:02d}-{k}", **c.model_dump()}
                         for k, c in enumerate(draft.criteria, start=1)],
            "drafted_by": source,
        })
    if live_saved:
        _update_demo("stories.json", live_saved)
    return stories, notes


def _story_one(req, provider, demo, demo_data) -> tuple[StoryDraft, str | None, str]:
    reason = None
    if not demo and provider is not None:
        try:
            draft = provider.generate(
                prompts.STORY_SYSTEM,
                prompts.STORY_USER.format(project=PROJECT_NAME, req_id=req["id"],
                                          req_type=req["type"], req_text=req["text"],
                                          note=req.get("note") or "none"),
                StoryDraft)
            return draft, None, "live"
        except AIStepError as exc:
            reason = f"The AI could not write a story for {req['id']} ({exc})."
    saved = demo_data.get(req["id"])
    usable = saved is not None and (req["origin"] == "ai" or
                                    similarity(saved.get("req_text", ""), req["text"]) >= 0.5)
    if usable:
        draft = StoryDraft.model_validate(saved)
        return draft, (f"{reason} Showing the saved demo story instead." if reason else None), "demo"
    msg = (f"{reason} " if reason else f"No saved demo story for {req['id']}. ") + \
          "A simple placeholder story was written instead - please review it."
    return _template_story(req), msg, "template"


def _template_story(req: dict) -> StoryDraft:
    text = req["text"].rstrip(".")
    return StoryDraft(
        title=text[:60],
        role="staff member",
        goal=text[0].lower() + text[1:],
        benefit="the leave rules are followed",
        criteria=[Criterion(given="the Staff leave app is in use",
                            when="the situation in the requirement happens",
                            then=text)],
    )


# ====================================================================== step 4: tests

def run_tests(stories: list[dict], provider: AIProvider | None, demo: bool,
              step: StepFn = _no_step) -> tuple[list[dict], list[str]]:
    """One AI call per story. Returns (tests, notes)."""
    tests, notes, live_saved = [], [], {}
    demo_data = load_demo("tests.json") or {}
    for story in stories:
        with step(f"Writing tests for {story['id']}…"):
            result, note, source = _tests_one(story, provider, demo, demo_data)
        if note:
            notes.append(note)
        if source == "live":
            live_saved[story["req_ids"][0]] = result.model_dump()
        for t in result.tests:
            if t.criterion_number > len(story["criteria"]):
                continue  # points at a criterion that does not exist
            tests.append({
                "id": f"TC-{len(tests) + 1:03d}",
                "story_id": story["id"],
                "criterion_id": story["criteria"][t.criterion_number - 1]["id"],
                "type": t.type,
                "steps": t.steps,
                "expected": t.expected,
            })
    if live_saved:
        _update_demo("tests.json", live_saved)
    return tests, notes


def _tests_one(story, provider, demo, demo_data) -> tuple[TestResult, str | None, str]:
    reason = None
    if not demo and provider is not None:
        criteria = "\n".join(f"{k}. Given {c['given']}, when {c['when']}, then {c['then']}"
                             for k, c in enumerate(story["criteria"], start=1))
        try:
            result = provider.generate(
                prompts.TESTS_SYSTEM,
                prompts.TESTS_USER.format(story_id=story["id"], title=story["title"],
                                          description=story["description"], criteria=criteria),
                TestResult)
            return result, None, "live"
        except AIStepError as exc:
            reason = f"The AI could not write tests for {story['id']} ({exc})."
    saved = demo_data.get(story["req_ids"][0])
    if saved is not None and story.get("drafted_by") != "template":
        result = TestResult.model_validate(saved)
        return result, (f"{reason} Showing the saved demo tests instead." if reason else None), "demo"
    msg = (f"{reason} " if reason else f"No saved demo tests for {story['id']}. ") + \
          "Simple placeholder tests were written instead - please review them."
    return _template_tests(story), msg, "template"


def _template_tests(story: dict) -> TestResult:
    tests = []
    for k, c in enumerate(story["criteria"], start=1):
        tests.append(TestDraft(criterion_number=k, type="positive",
                               steps=f"1. Set up: {c['given']} 2. Do: {c['when']}",
                               expected=c["then"]))
        tests.append(TestDraft(criterion_number=k, type="negative",
                               steps=f"1. Set up: {c['given']} 2. Try to break the rule",
                               expected="The app blocks it and shows a clear message"))
    return TestResult(tests=tests)
