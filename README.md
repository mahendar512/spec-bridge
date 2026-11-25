# SpecBridge

SpecBridge is an AI assistant that turns scattered emails, documents and meeting notes into one clear, agreed list of requirements. It flags conflicts and gaps before anything is built, and drafts the stories and test cases.

**The AI only drafts. A person approves everything.**

This is a small prototype built for a 10-minute demo. There is no email server, no Jira and no database.

## Setup (Windows, macOS or Linux)

You need Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

### Always demo mode

The app always runs in demo mode. It replays the AI results saved in `demo_data/*.json`, so the demo works offline every time, with no API key and no local AI needed. The rule-based checks (quote checking, merging, conflicts, gaps, vague words, coverage) still run for real on whatever files you load.

The local-AI code is kept in [ai.py](ai.py) for later. It runs on [Ollama](https://ollama.com) with `qwen2.5:7b`, using structured output, temperature 0, one retry, and a fallback to demo data. To use it, set `DEMO_MODE = False` at the top of [app.py](app.py), then run `ollama pull qwen2.5:7b`. Each live step that succeeds saves its results into `demo_data/`.

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
- a full click-through of the app, from loading the samples to export

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

The AI uses small, separate calls with structured JSON output and temperature 0. The answers are checked with Pydantic. All prompts are in [prompts.py](prompts.py), and you can also see them in the app's sidebar under **Show the AI prompts**.

| File | Purpose |
|---|---|
| [app.py](app.py) | Streamlit UI |
| [parsing.py](parsing.py) | Reads .docx, .pdf, .txt, .eml and .msg files |
| [ai.py](ai.py) | AI interface (Ollama), schemas, the four AI steps, demo mode |
| [prompts.py](prompts.py) | All AI prompts |
| [checks.py](checks.py) | Quote checking, merging, conflict/gap/weak-word checks, coverage |
| [export.py](export.py) | CSV and Word export |
| [create_samples.py](create_samples.py) | Builds the two sample files in `sample_inputs/` |

## The app in 4 steps

The stepper at the top always shows where you are. Each step moves you on to the next one when its main button is clicked, and you can jump back to any step that is already done.

| Step | What you see |
|---|---|
| **1 · Add sources** | Load the sample project or upload files. Each source appears as a card; emails show From, Date and Subject. |
| **2 · Review** | Conflicts come first, as a decision card with both quotes side by side and a **Keep** button for each. Below them, every requirement is a card with its quoted source, any problems and **Accept / Edit / Reject**. Add missed requirements, see a table view, resolved issues and the decision log. |
| **3 · Stories & tests** | One click drafts both. Each story shows its Given/When/Then criteria with the tests listed under each one, and a **Ready to build** badge. Criteria without a test are highlighted. |
| **4 · Export** | Download `stories.csv`, `test_cases.csv` and `agreed_version.docx`, with previews. |

The sidebar shows the project, a live summary (found, flagged, accepted, edited, rejected, added, ready to build), the AI prompts and **Start over**.

## 10-step demo script: "Staff leave app"

**Setting:** Larkfield Retail is a made-up company with 5,000 staff. It is having a staff leave app built by an outside partner, Partner A. The sample inputs are deliberately small: an HR policy with 3 rules and a later email from the HR lead with 1 rule, which gives **4 requirements and 1 planted problem**.

1. **Set the scene.** Point at the project card in the sidebar: "Requirements never arrive in one place. Here they're in a policy document and an email. Anything copied to `specbridge+leave-app@company.com` would land in SpecBridge."

2. **Add the sources (step 1).** Click **Load sample sources**. Open **Preview text** on the policy: 20 days of annual leave, carry over at most **5** days, see your leave balance. Then show the email card's From, Date and Subject. "Note the date: the email came *after* the policy."

3. **Find the requirements.** Click **Find requirements →**. The app moves to **Review** by itself. "Four requirements, each with the exact words it came from, and every quote checked against the source. If the AI made something up, its card would show an orange *Source not verified* badge."

4. **One problem, found before a single line of code was written.** Point at the numbers row: 4 requirements, 1 decision needed.

5. **⏸ PAUSE on the conflict.** The decision card shows the two quotes side by side: the policy says **5** carry-over days (REQ-02) and the HR email says **10** (REQ-04). Let it land: "If this had gone to Partner A as-is, a developer would have picked one, and we'd have found out in user testing or from an angry store manager in January. That's rework, and maybe a payroll problem." Point out that SpecBridge doesn't decide. It suggests the question to ask HR, and **Accept** stays locked on both cards until a person decides.

6. **Settle it.** Leave "Confirmed by HR" in the box and click **Keep REQ-04**. REQ-02 is marked rejected as superseded, and the decision is in the **Decision log**.

7. **Finish the review.** Optional: click **Edit** on REQ-03 to show that the analyst can reword anything, for example *"Staff can see their remaining leave balance on the home screen at any time."* Then click **Accept all remaining** and point at the sidebar summary.

8. **Draft stories and tests.** Click **Draft stories and tests →**. "Only approved items. The 5-day rule is gone." Show the carry-over story and its Given/When/Then criteria.

9. **Tests under each criterion.** Point at the boundary tests 📏: *10 days → allowed, 11 days → blocked*. "Every criterion has a test. If one didn't, it would be highlighted." The badges and sidebar show **3 of 3 ready to build**.

10. **Export and close.** Click **Continue to export →** and download `stories.csv` (ready for Jira import), `test_cases.csv` and `agreed_version.docx`. Open the Word file: agreed requirements with sources, the resolved conflict with who confirmed it, stories, tests and the full decision log. Close with: "The AI did the reading and drafting. The analyst made every decision, and the audit trail shows it."

**If asked "what are the prompts?"**: open **Show the AI prompts** in the sidebar, or [prompts.py](prompts.py).
**If asked "what if the AI is wrong?"**: point to quote checking, the Python conflict check running beside the AI, and the analyst approving every item.
**If asked "what else can it catch?"**: it also flags gaps (e.g. a deadline with no rule for when it is missed), vague words like *fast* or *user-friendly*, and the same rule appearing in two files.
