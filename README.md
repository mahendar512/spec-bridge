# SpecBridge

SpecBridge is an AI assistant that turns scattered emails, documents and meeting notes into one clear, agreed list of requirements. It flags conflicts and gaps before anything is built, and drafts the stories and test cases.

**The AI only drafts. A person approves everything.**

This is a small prototype built for a 10-minute demo. There is no email server, no Jira and no database. The AI runs locally through [Ollama](https://ollama.com), so it needs no API keys and no internet connection.

## Setup (Windows, macOS or Linux)

You need Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

### Local AI (optional; demo mode works without it)

1. Install Ollama from https://ollama.com and start it.
2. Download the model: `ollama pull qwen2.5:7b`
3. Optional: copy `.env.example` to `.env` to change `OLLAMA_HOST`, `OLLAMA_MODEL` or `OLLAMA_TIMEOUT`.

If Ollama is not running, or the model is missing, the sidebar says so ("Start Ollama and run: ollama pull qwen2.5:7b") and switches on **Demo mode**.

### Demo mode

Demo mode replays saved results from `demo_data/*.json`, so the demo always works offline. The repo includes a hand-made demo run for the two sample files. After a step succeeds live with the local AI, its results are saved to `demo_data/`, so demo mode then replays a real run. Live mode is protected in the same way: if the AI gives an invalid answer twice for a step, that step uses the saved demo data and the app shows a note.

### Tests

```bash
python -m pytest
```

The tests cover:
- reading files
- quote checking
- duplicate merging and the conflict, gap and weak-word checks
- the CSV and Word exports
- the AI retry and fallback logic, using a fake AI
- a full click-through of the app in demo mode

## How it works

| Step | What happens | Who does it |
|---|---|---|
| Read | Each file is read separately; every requirement comes with the exact quoted words and file name | AI (one call per file) |
| Check quotes | Each quote is looked up in the source text. Missing quotes are marked **Source not verified** in orange | Python |
| Merge | The same rule found in two files becomes one item with both sources | Python |
| Find problems | Different numbers on the same subject (conflict), deadlines with no "what if it's missed" (gap), vague words like *fast* or *user-friendly* (unclear) | Python + AI, results merged |
| Review | Accept, edit or reject each item; settle conflicts; add what was missed. Every action is logged | Analyst |
| Stories and tests | Written **only** from approved requirements: one story per requirement, then tests per story (positive, negative, boundary) | AI |
| Export | `stories.csv`, `test_cases.csv` (Jira-friendly) and `agreed_version.docx` | Python |

The AI uses small, separate calls with structured JSON output and temperature 0. The answers are checked with Pydantic. All prompts are in [prompts.py](prompts.py), and you can also see them in the app under **1 · Project → Show the AI prompts**.

| File | Purpose |
|---|---|
| [app.py](app.py) | Streamlit UI |
| [parsing.py](parsing.py) | Reads .docx, .pdf, .txt, .eml and .msg files |
| [ai.py](ai.py) | AI interface (Ollama), schemas, the four AI steps, demo mode |
| [prompts.py](prompts.py) | All AI prompts |
| [checks.py](checks.py) | Quote checking, merging, conflict/gap/weak-word checks, coverage |
| [export.py](export.py) | CSV and Word export |
| [create_samples.py](create_samples.py) | Builds the two sample files in `sample_inputs/` |

## 10-step demo script: "Staff leave app"

**Setting:** Larkfield Retail is a made-up company with 5,000 staff. It is having a staff leave app built by an outside partner, Partner A. The sample inputs are deliberately small: an HR policy with 3 rules and a later email from the HR lead with 1 rule, which gives **4 requirements and 1 planted problem**. Before you start, open the app; turn **Demo mode** off only if Ollama is running.

1. **Set the scene (tab 1 · Project).** "Requirements never arrive in one place. Here they're in a policy document and an email. Anything copied to this project address would land in SpecBridge." Point at `specbridge+leave-app@company.com`.

2. **Load the inputs (tab 2 · Inputs).** Click **Load sample inputs**. Show the policy: 20 days of annual leave, carry over at most **5** days, see your leave balance. Then show the email's From, Date and Subject. "Note the date: the email came *after* the policy."

3. **Find the requirements (tab 3).** Click **Find requirements** and point out the spinners ("Reading HR_Leave_Policy.docx…"). "Four requirements, each with the exact words it came from, and every quote checked against the source. If the AI made something up, it would show here in orange as *Source not verified*."

4. **Open the issues (tab 4).** "One problem found, before a single line of code was written."

5. **⏸ PAUSE on the conflict (ISS-01).** The two quotes sit side by side: the policy says **5** carry-over days (REQ-02) and the HR email says **10** (REQ-04). Let it land: "If this had gone to Partner A as-is, a developer would have picked one, and we'd have found out in user testing or from an angry store manager in January. That's rework, and maybe a payroll problem." Point out that SpecBridge doesn't decide. It suggests the question to ask HR.

6. **Settle it (tab 5 · Analyst review).** Under **Settle the conflicts**, pick **REQ-04 (10 days)**, keep the note "Confirmed by HR" and click **Save decision**. REQ-02 is marked rejected as superseded, and the decision appears in the log at the bottom.

7. **Finish the review.** Optional: click **Edit** on REQ-03 to show that the analyst can reword anything, for example *"Staff can see their remaining leave balance on the home screen at any time."* Then click **Accept all remaining**. Point at the sidebar counts: accepted, edited, rejected.

8. **User stories (tab 6).** Click **Write user stories**: "Only approved items. The 5-day rule is gone." Open the carry-over story and its Given/When/Then criteria.

9. **Test cases (tab 7).** Click **Write test cases** and show the boundary tests: *10 days → allowed, 11 days → blocked*. Show the coverage check: every criterion has a test, and any gap would be highlighted. The sidebar shows **3 of 3 stories ready to build**.

10. **Export (tab 8) and close.** Download `stories.csv` (ready for Jira import), `test_cases.csv` and `agreed_version.docx`. Open the Word file: agreed requirements with sources, the resolved conflict with who confirmed it, stories, tests and the full decision log. Close with: "The AI did the reading and drafting. The analyst made every decision, and the audit trail shows it."

**If asked "what are the prompts?"**: go to tab 1 → **Show the AI prompts**, or open [prompts.py](prompts.py).
**If asked "what if the AI is wrong?"**: point to quote checking, the Python conflict check running beside the AI, and the analyst approving every item.
**If asked "what else can it catch?"**: it also flags gaps (e.g. a deadline with no rule for when it is missed), vague words like *fast* or *user-friendly*, and the same rule appearing in two files. Upload your own file to show it.
