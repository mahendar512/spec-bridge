"""Live-mode AI path with a fake Ollama client: retry once, fall back, save demo data."""

import json
from types import SimpleNamespace

import pytest

import ai
from parsing import ParsedDoc


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(message=SimpleNamespace(content=answer))


def make_provider(client):
    p = ai.OllamaProvider(host="http://fake", model="fake-model", timeout=5)
    p._client = lambda timeout: client
    return p


GOOD = json.dumps({"requirements": [
    {"text": "Staff get 20 days.", "type": "Rule", "quote": "Staff get 20 days", "topic": "annual leave"}]})


def test_generate_uses_schema_and_temperature_zero():
    client = FakeClient([GOOD])
    result = make_provider(client).generate("sys", "user", ai.ExtractionResult)
    assert result.requirements[0].type == "rule"  # "Rule" was normalised
    call = client.calls[0]
    assert call["options"]["temperature"] == 0
    assert call["format"] == ai.ExtractionResult.model_json_schema()


def test_generate_retries_once_then_fails():
    client = FakeClient(["not json", GOOD])
    assert make_provider(client).generate("s", "u", ai.ExtractionResult).requirements
    client = FakeClient(["not json", '{"wrong": 1}'])
    with pytest.raises(ai.AIStepError, match="tried twice"):
        make_provider(client).generate("s", "u", ai.ExtractionResult)
    assert len(client.calls) == 2


def test_connection_error_is_friendly():
    client = FakeClient([ConnectionError("Connection refused")])
    with pytest.raises(ai.AIStepError, match="not reachable"):
        make_provider(client).generate("s", "u", ai.ExtractionResult)


def test_live_success_saves_demo_and_failure_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "DEMO_DIR", tmp_path)
    doc = ParsedDoc("policy.txt", "document", "Staff get 20 days of leave.")

    reqs, notes = ai.run_extraction([doc], make_provider(FakeClient([GOOD])), demo=False)
    assert notes == [] and reqs[0]["sources"][0]["verified"]
    assert (tmp_path / "extract_policy.txt.json").exists()

    # Now the AI fails twice: the saved run is replayed with a visible note
    reqs, notes = ai.run_extraction([doc], make_provider(FakeClient(["bad", "bad"])), demo=False)
    assert reqs[0]["text"] == "Staff get 20 days."
    assert "Showing saved demo results" in notes[0]


def test_demo_mode_unknown_file_is_skipped_with_note(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "DEMO_DIR", tmp_path)
    reqs, notes = ai.run_extraction([ParsedDoc("other.pdf", "document", "text")], None, demo=True)
    assert reqs == [] and "no saved demo results" in notes[0]


def test_stories_only_from_approved_requirements(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "DEMO_DIR", tmp_path)
    story = json.dumps({"title": "T", "role": "staff member", "goal": "g", "benefit": "b",
                        "criteria": [{"given": "x", "when": "y", "then": "z"}]})
    reqs = [{"id": "REQ-01", "text": "a", "type": "rule", "status": "accepted", "origin": "ai",
             "sources": [{"file": "f", "quote": "a", "verified": True}]},
            {"id": "REQ-02", "text": "b", "type": "rule", "status": "rejected", "origin": "ai",
             "sources": []},
            {"id": "REQ-03", "text": "c", "type": "rule", "status": "pending", "origin": "ai",
             "sources": []}]
    client = FakeClient([story])
    stories, notes = ai.run_stories(reqs, make_provider(client), demo=False)
    assert [s["req_ids"] for s in stories] == [["REQ-01"]]
    assert len(client.calls) == 1
    assert stories[0]["criteria"][0]["id"] == "AC-01-1"

    tests_json = json.dumps({"tests": [
        {"criterion_number": 1, "type": "positive", "steps": "1. a", "expected": "b"},
        {"criterion_number": 7, "type": "negative", "steps": "1. a", "expected": "b"}]})
    tests, notes = ai.run_tests(stories, make_provider(FakeClient([tests_json])), demo=False)
    assert [t["id"] for t in tests] == ["TC-001"]  # criterion 7 does not exist -> dropped
    assert json.loads((tmp_path / "tests.json").read_text())["REQ-01"]
