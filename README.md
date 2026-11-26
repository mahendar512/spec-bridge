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
| Review · **Checkpoint 1** | Accept, edit or reject each item (with a reason); settle conflicts; add what was missed; then approve | Analyst |
| Stories | Written **only** from requirements approved at Checkpoint 1, one story per requirement | AI |
| Partner confirms · **Checkpoint 2** | The partner lead confirms each agreed requirement | Partner lead |
| Tests · **Checkpoint 3** | Normal, error and edge cases for every criterion, plus a coverage check; the tester approves the export | AI, then tester |
| Export | `stories.csv`, `test_cases.csv` (Jira-friendly) and `agreed_version.docx`, each item with its source ID | Python |

The AI uses small, separate calls with structured JSON output and temperature 0. The answers are checked with Pydantic. All prompts are in [prompts.py](prompts.py).

| File | Purpose |
|---|---|
| [app.py](app.py) | Streamlit UI |
| [parsing.py](parsing.py) | Reads .docx, .pdf, .txt, .eml and .msg files |
| [ai.py](ai.py) | AI interface (Ollama), schemas, the four AI steps, demo mode |
| [prompts.py](prompts.py) | All AI prompts |
| [checks.py](checks.py) | Quote checking, merging, conflict/gap/weak-word checks, coverage |
| [export.py](export.py) | CSV and Word export |
| [create_samples.py](create_samples.py) | Builds the two sample files in `sample_inputs/` |

## The app in 6 steps, with 3 checkpoints

The stepper follows the SpecBridge flow. ✨ marks steps where the AI does the work and 👤 marks steps where a person decides. Each **checkpoint** has a card listing what must be true (for example "every conflict decided", "every requirement reviewed") and an approve button that only unlocks when every item is ticked. The next step stays locked until the checkpoint is approved. If something approved later changes (for example the analyst rewords a requirement), that checkpoint and the ones after it must be approved again.

| Step | What you see |
|---|---|
| **1 · Inputs** | Upload documents and emails. Each source appears as a card; emails show From, Date and Subject. The two sample files are in `sample_inputs/` (rebuild them with `python create_samples.py --force`). |
| **2 · AI checks** ✨ | What the AI found: requirements with their quoted sources, duplicates merged, and the problems flagged (conflicts, gaps, vague words). Nothing is agreed yet. |
| **3 · Analyst review** 👤 **Checkpoint 1** | Conflicts first, as a decision card with both quotes and a **Keep** button for each. Below them, only the requirements without conflicts; each card has **Accept / Edit / Reject** (rejecting asks for a reason, such as *Noise* or *Duplicate*). Approving drafts the stories. |
| **4 · AI stories** ✨ | One story per approved requirement with Given/When/Then criteria. **Send to partner lead →**. |
| **5 · Partner confirms** 👤 **Checkpoint 2** | The partner lead confirms each agreed requirement (or **Confirm all remaining**), then approves. |
| **6 · Tests & export** 👤 **Checkpoint 3** | The AI drafts normal, error and edge cases, listed under each criterion, plus a coverage check. The tester approves, and only then the export pack (`stories.csv`, `test_cases.csv`, `agreed_version.docx`) can be downloaded. The Word file includes who approved each checkpoint. |

The sidebar shows a live summary, the status of the three checkpoints (who approved and when), stories ready to build, and **Start over**. Every action is in the decision log with who did it. The round avatar in the top-right corner opens the profile: name (Mahendar), designation, employee ID, a **Light / Dark** theme switch, **New project** and **Log out** (a mock sign-in, no real accounts; edit `USER` in `app.py`). The light and dark themes are defined in `.streamlit/config.toml`, which also hides Streamlit's own menu and Deploy button. Switching theme reloads the page, and the work in progress is kept.

## 10-step demo script: "Staff leave app"

**Setting:** Larkfield Retail is a made-up company with 5,000 staff. It is having a staff leave app built by an outside partner, Partner A. The sample inputs are deliberately small: an HR policy with 3 rules and a later email from the HR lead with 1 rule, which gives **4 requirements and 1 planted problem**.

1. **Set the scene.** "Requirements never arrive in one place. Here they're in a policy document and an email from HR."

2. **Add the sources (step 1).** Upload `HR_Leave_Policy.docx` and `HR_email_carry_over.eml` from `sample_inputs/` (drag both onto the upload box). Open **Preview text** on the policy: 20 days of annual leave, carry over at most **5** days, see your leave balance. Then show the email card's From, Date and Subject. "Note the date: the email came *after* the policy."

3. **The AI extracts and checks (step 2).** Click **Find requirements →**. "Four requirements, each with the exact words it came from, and every quote checked against the source. If the AI made something up, it would be marked *Source not verified*." Point at the stepper: "The AI does the light steps. A person decides at each of the three checkpoints, and nothing moves on without that approval."

4. **One problem, found before a single line of code was written.** Point at *Problems flagged*: 1 conflict. Click **Start analyst review →**.

5. **⏸ PAUSE on the conflict.** The decision card shows the two quotes side by side: the policy says **5** carry-over days (REQ-02) and the HR email says **10** (REQ-04). Let it land: "If this had gone to Partner A as-is, a developer would have picked one, and we'd have found out in user testing or from an angry store manager in January. That's rework, and maybe a payroll problem." Point out that SpecBridge doesn't decide. It suggests the question to ask HR, and **Accept** stays locked on both cards until a person decides.

6. **Settle it.** Leave "Confirmed by HR" in the box and click **Keep REQ-04**. REQ-02 is marked rejected as superseded, and the decision is in the **Decision log**.

7. **Checkpoint 1.** Click **Accept all remaining**. Scroll to the Checkpoint 1 card: every item is ticked, so **Approve review** unlocks. Click it. "Only now does the AI draft stories, and only from approved items. The 5-day rule is gone." Show the carry-over story and its Given/When/Then criteria, then click **Send to partner lead →**.

8. **Checkpoint 2: the partner confirms.** "The build partner's lead checks the agreed list before anything is built." Click **Confirm all remaining**, then **Confirm agreed requirements**.

9. **Checkpoint 3: tests.** Click **Draft test cases**. Point at the edge tests 📏: *10 days → allowed, 11 days → blocked*. "Normal, error and edge cases for every criterion, and a coverage check. The export stays locked until the tester approves." Click **Approve export**.

10. **Export and close.** Download `stories.csv` (ready for Jira import), `test_cases.csv` and `agreed_version.docx`. Open the Word file: agreed requirements with sources, who approved each checkpoint and when, stories, tests and the full decision log. Close with: "The AI did the reading and drafting. People made every decision at three checkpoints, and the audit trail shows it."

**If asked "what are the prompts?"**: open [prompts.py](prompts.py).
**If asked "what if the AI is wrong?"**: point to quote checking, the Python conflict check running beside the AI, and the analyst approving every item.
**If asked "what else can it catch?"**: it also flags gaps (e.g. a deadline with no rule for when it is missed), vague words like *fast* or *user-friendly*, and the same rule appearing in two files.
