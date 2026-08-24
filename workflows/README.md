# Guided workflows

A **workflow** is a questionnaire the assistant walks a visitor through: sections
of questions asked one at a time, follow-ups when an answer is vague, and a
filled-in document at the end. Intake forms, structured interviews, eligibility
screening, feedback collection — anything where the assistant needs to *ask*
rather than answer.

Each workflow is one JSON file in this directory. Adding a guided process means
dropping in a file; no code changes, no restart hook beyond restarting the
server.

Two ship with the repo:

| File | What it is |
|---|---|
| `contact_intake.json` | Three questions. A minimal, readable starting point to copy. |
| `process_interview.json` | A full current-state / future-state process interview, with effort-vs-duration probing, `measured`/`estimate`/`dont_know` tagging, and a repeating section for process steps. |

Both are written for the fictional Example Site. Edit them, or delete them and
write your own.

## How it runs

Sequencing is deterministic Python. The model is called **at most once per
answer**, and only to do three things at once: judge whether the answer is
sufficient, normalize it into typed fields, and draft one follow-up sentence.

That design is what makes the rest hold:

- **The final document is pure string substitution**, not model output. A value
  can only appear in it if it was stored from something the participant actually
  said, so "never invent a number" is structurally true rather than a request in
  a prompt.
- **Every question asked is your text, verbatim.** The only model-authored
  string a participant ever sees is a follow-up.
- **A model outage cannot strand anyone.** An unparseable judgement accepts the
  answer and moves on.
- **Follow-ups can't loop.** The budget is enforced in Python, and `skip` is
  matched before the model is called at all, so it can never argue with one.

State lives in the visitor's browser and travels with each request, so the
server stays stateless (and multi-worker safe), and someone can close the tab
mid-interview and resume on the same question days later.

## File format

```jsonc
{
  "schema_version": 1,
  "id": "process_intake",              // lowercase [a-z0-9_], unique, and the URL key
  "title": "Process sizing interview",
  "description": "One line, shown on the launcher chip.",
  "estimated_minutes": 15,

  "intro": "Shown when the workflow starts. Markdown.",
  "conduct": [                          // interviewer rules, passed to the judge
    "Probe every quantitative claim: measured, estimate, or guess?",
    "It is always fine to say you don't know."
  ],
  "scope_guard": "What to do when the participant goes off-topic.",

  "max_follow_ups": 2,                  // 0-5, per question, overridable below
  "allow_skip_default": true,

  "messages": {                         // optional; overrides the site-wide defaults
    "complete": "That's everything — thank you.",
    "stopped": "Saved. Pick this up any time.",
    "skipped": "No problem.",
    "dont_know": "That's fine."
  },

  "sections": [ /* see below */ ],
  "output_template": { /* see below */ }
}
```

### Sections

```jsonc
{
  "id": "volumes",
  "title": "Volumes and throughput",    // announced when the section is entered
  "intro": "Optional text before the first question.",
  "recap": "none",
  "repeat": null,                       // or a repeat block, see below
  "questions": [ /* ... */ ]
}
```

### Questions

```jsonc
{
  "id": "annual_volume",                // unique within its section
  "prompt": "Roughly how many do you process a year?",
  "guidance": "A number is enough. Then ask whether it is measured or estimated.",
  "expects": "number",                  // text | number | choice | boolean | list
  "choices": ["New", "Revision"],       // required when expects is "choice"
  "required": true,
  "allow_skip": true,
  "max_follow_ups": 1,                  // overrides the workflow default
  "capture": ["value", "confidence"],   // what the judge extracts
  "sufficiency_criteria": "Sufficient when a quantity is stated AND tagged."
}
```

`capture` is the mechanism behind "probe every number". Including `"confidence"`
tells the judge to record whether the participant called their answer
`measured`, an `estimate`, a `guess`, or left it `unknown` — and
`sufficiency_criteria` is what makes an untagged number insufficient, so the
assistant asks once more.

`expects: "choice"` renders its options as buttons.

### Repeating sections

For a section whose item count is not known up front — process steps, team
members, sites — where the participant's own naming matters:

```jsonc
"repeat": {
  "item_label": "step",
  "name_field": "step_name",            // which question holds their label for the item
  "max_items": 12,                      // 1-50; a hard ceiling
  "continue_question": "Is there another step after that one?"
}
```

The section's questions are asked once per item, then `continue_question` is
asked with Yes/No buttons. A "no" ends the section; so does hitting `max_items`.

### Output template

```jsonc
"output_template": {
  "format": "markdown",
  "filename": "intake-{{about_you.name}}-{date}.md",
  "unknown_placeholder": "dont_know",   // for a skipped or don't-know answer
  "uncovered_placeholder": "not_covered", // for a question never reached
  "body": "# Intake\n\nname: {{about_you.name}}\ncount: {{volumes.annual_volume}} ({{volumes.annual_volume.confidence}})\n\n{{#repeat process_steps}}- {{step_name}}: {{duration}}\n{{/repeat}}"
}
```

Placeholder forms:

| Form | Meaning |
|---|---|
| `{{section_id.question_id}}` | The normalized answer |
| `{{section_id.question_id.confidence}}` | Its `measured`/`estimate`/`guess`/`unknown` tag |
| `{{#repeat section_id}} … {{/repeat}}` | Repeated once per item; inside, use bare `{{question_id}}` and `{{question_id.confidence}}` |
| `{date}` | UTC timestamp — filename only, and note the *single* braces |

Every `{{…}}` is validated against your sections at load, so a typo is a
startup error rather than a hole in the document of someone who just spent an
hour answering questions.

Answers are flattened into a single line and stripped of brace pairs before
substitution, so a multi-line answer can't break the template's structure and an
answer containing `{{` can't inject a placeholder.

### Site placeholders

`{site_name}`, `{site_short_name}` and `{contact_email}` are substituted from
`site_config.py` anywhere in the file, so a spec never has to hardcode an
organization name.

## Adding your own

1. Copy `contact_intake.json` to `workflows/your_workflow.json`.
2. Change `id` (lowercase, underscores) — it must be unique across the directory.
3. Write your sections and questions, then the output template.
4. Restart the server. A spec that fails validation is **skipped with a printed
   reason**, and the other workflows keep working — check the server log if your
   chip doesn't appear.

To check a spec without starting the server:

```bash
python -c "
import sys; sys.path.insert(0, 'agent_chatbot'); sys.path.insert(0, '.')
import workflow_specs as ws
spec, digest = ws.load_spec('workflows/your_workflow.json')
print('valid:', spec['id'], digest, ws.count_questions(spec), 'questions')"
```

## Where the answers go

When a run finishes, the visitor gets the document as a card in the chat with
**Copy**, **Download** and **Email it** buttons, and a copy is written to
`WORKFLOW_RESPONSES_DIR` (default `workflow_responses/`, gitignored).

There is deliberately **no HTTP route that reads responses back**. They can
contain personal details, and the dashboard API has no authentication — read
them off the server's filesystem, or have them emailed.

## Editing a live workflow

Each spec is hashed. If you edit one while somebody is midway through it, their
next answer comes back as a `409` carrying a *migrated* state: answers to
questions that still exist are kept, and the interview resumes at the first
unanswered question. Renaming a question id discards that answer — so prefer
adding to a spec over renaming within it while a run could be open.

## Configuration

In `site_config.py`:

| Setting | Purpose |
|---|---|
| `WORKFLOWS_ENABLED` | `False` hides the feature without deleting the specs |
| `WORKFLOWS_DIR` | Where specs live (`WORKFLOWS_DIR` env var overrides) |
| `WORKFLOW_RESPONSES_DIR` | Where completed responses are written |
| `WORKFLOW_MAX_TURNS` | Force-completes a runaway run (default 150) |
| `WORKFLOW_MAX_STATE_BYTES` | Rejects an oversized state blob (default 128 KB) |
| `WORKFLOW_MAX_RAW_CHARS` / `WORKFLOW_MAX_VALUE_CHARS` | Per-answer truncation |
| `WORKFLOW_*_MESSAGE` | Canned engine replies, overridable per spec via `messages` |

UI chrome (button labels, the progress format) is translated in
`frontend/site_config.js` under `workflowTranslations`.

## A note on language

A spec is written in one language. The widget's selected language is passed to
the judge, so **follow-up questions** come back translated — but the scripted
prompts are shown as you wrote them. For a fully translated interview, ship one
spec per language and give each its own `id`.
