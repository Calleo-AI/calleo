"""Workflow spec loading and validation.

A *workflow* is a guided questionnaire the assistant walks a visitor through:
sections of questions, asked one at a time, with follow-ups when an answer is
vague, and a filled-in document at the end. Each one is a single JSON file in
``workflows/`` — adding a new guided process is dropping in a file, not writing
code.

This module owns the file format: discovery, validation, and hashing. The
sequencing logic lives in ``workflow_engine``; the HTTP surface lives in
``server``. Nothing here imports flask or chromadb, so it loads instantly in
tests.

Validation is deliberately strict and runs at load. The highest-value check is
that every ``{{placeholder}}`` in the output template resolves to a real
section/question id — without it, a typo surfaces as a hole in the document of
someone who just spent an hour answering questions.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import site_config

SCHEMA_VERSION = 1

# Answer shapes a question may ask for. "choice" additionally requires choices.
EXPECTS = ("text", "number", "choice", "boolean", "list")

# Sub-fields the judge may extract from an answer. "value" is the normalized
# answer; "confidence" is the measured/estimate/guess tag that makes
# "probe every quantitative claim" enforceable rather than merely requested.
CAPTURE_FIELDS = ("value", "confidence")

MAX_ITEMS_LIMIT = 50

# {{section_id.question_id}} or {{section_id.question_id.confidence}}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
# {{#repeat section_id}} ... {{/repeat}}
_REPEAT_BLOCK_RE = re.compile(
    r"\{\{#repeat\s+([a-zA-Z0-9_]+)\s*\}\}(.*?)\{\{/repeat\s*\}\}", re.DOTALL
)
_ID_RE = re.compile(r"^[a-z0-9_]+$")


class WorkflowSpecError(Exception):
    """Raised when a spec file is unreadable or fails validation."""


# --- Validation ---------------------------------------------------------------

def _validate_question(question, section_id, seen_ids, errors):
    where = f"section '{section_id}'"
    qid = question.get("id")
    if not isinstance(qid, str) or not _ID_RE.match(qid or ""):
        errors.append(f"{where}: question id must be lowercase [a-z0-9_], got {qid!r}")
        return
    if qid in seen_ids:
        errors.append(f"{where}: duplicate question id '{qid}'")
    seen_ids.add(qid)

    if not isinstance(question.get("prompt"), str) or not question["prompt"].strip():
        errors.append(f"{where}, question '{qid}': 'prompt' must be a non-empty string")

    expects = question.get("expects", "text")
    if expects not in EXPECTS:
        errors.append(
            f"{where}, question '{qid}': 'expects' must be one of {list(EXPECTS)}, got {expects!r}"
        )
    if expects == "choice":
        choices = question.get("choices")
        if not isinstance(choices, list) or not choices:
            errors.append(
                f"{where}, question '{qid}': expects='choice' requires a non-empty 'choices' list"
            )

    for field in question.get("capture", []):
        if field not in CAPTURE_FIELDS:
            errors.append(
                f"{where}, question '{qid}': unknown capture field {field!r} "
                f"(expected one of {list(CAPTURE_FIELDS)})"
            )

    max_fu = question.get("max_follow_ups")
    if max_fu is not None and (not isinstance(max_fu, int) or max_fu < 0 or max_fu > 5):
        errors.append(f"{where}, question '{qid}': 'max_follow_ups' must be an int 0-5")


def _validate_section(section, seen_section_ids, errors):
    sid = section.get("id")
    if not isinstance(sid, str) or not _ID_RE.match(sid or ""):
        errors.append(f"section id must be lowercase [a-z0-9_], got {sid!r}")
        return
    if sid in seen_section_ids:
        errors.append(f"duplicate section id '{sid}'")
    seen_section_ids.add(sid)

    if not isinstance(section.get("title"), str) or not section["title"].strip():
        errors.append(f"section '{sid}': 'title' must be a non-empty string")

    questions = section.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append(f"section '{sid}': 'questions' must be a non-empty list")
        return

    seen_qids = set()
    for question in questions:
        if not isinstance(question, dict):
            errors.append(f"section '{sid}': each question must be an object")
            continue
        _validate_question(question, sid, seen_qids, errors)

    repeat = section.get("repeat")
    if repeat is not None:
        if not isinstance(repeat, dict):
            errors.append(f"section '{sid}': 'repeat' must be an object or null")
        else:
            max_items = repeat.get("max_items", 12)
            if not isinstance(max_items, int) or not (1 <= max_items <= MAX_ITEMS_LIMIT):
                errors.append(
                    f"section '{sid}': repeat.max_items must be an int 1-{MAX_ITEMS_LIMIT}"
                )
            if not isinstance(repeat.get("continue_question", ""), str):
                errors.append(f"section '{sid}': repeat.continue_question must be a string")
            name_field = repeat.get("name_field")
            if name_field is not None and name_field not in seen_qids:
                errors.append(
                    f"section '{sid}': repeat.name_field '{name_field}' is not a question in this section"
                )


def _template_field_errors(spec):
    """Check every template placeholder resolves to a real question.

    This is the check that pays for the whole validator: an unresolvable
    placeholder is otherwise invisible until someone finishes an interview and
    finds a hole in their document.
    """
    errors = []
    template = (spec.get("output_template") or {}).get("body", "")
    if not isinstance(template, str):
        return ["output_template.body must be a string"]

    by_section = {}
    repeating = set()
    for section in spec.get("sections", []):
        if not isinstance(section, dict) or not isinstance(section.get("id"), str):
            continue
        by_section[section["id"]] = {
            q.get("id") for q in section.get("questions", []) if isinstance(q, dict)
        }
        if section.get("repeat"):
            repeating.add(section["id"])

    # Repeat blocks first, so their inner bare {{field}} names are checked
    # against the owning section rather than against the top-level namespace.
    remainder = template
    for match in _REPEAT_BLOCK_RE.finditer(template):
        section_id, block = match.group(1), match.group(2)
        if section_id not in by_section:
            errors.append(f"output_template: {{{{#repeat {section_id}}}}} names an unknown section")
            continue
        if section_id not in repeating:
            errors.append(
                f"output_template: {{{{#repeat {section_id}}}}} names a section without 'repeat' set"
            )
        for inner in _PLACEHOLDER_RE.findall(block):
            field = inner.split(".")[0]
            if field not in by_section[section_id]:
                errors.append(
                    f"output_template: '{inner}' inside repeat block '{section_id}' "
                    f"is not a question in that section"
                )
        remainder = remainder.replace(match.group(0), "")

    for ref in _PLACEHOLDER_RE.findall(remainder):
        parts = ref.split(".")
        if len(parts) < 2:
            errors.append(
                f"output_template: '{ref}' must be section_id.question_id"
            )
            continue
        section_id, question_id = parts[0], parts[1]
        if section_id not in by_section:
            errors.append(f"output_template: '{ref}' names unknown section '{section_id}'")
        elif question_id not in by_section[section_id]:
            errors.append(
                f"output_template: '{ref}' names unknown question '{question_id}' "
                f"in section '{section_id}'"
            )
        if len(parts) == 3 and parts[2] not in CAPTURE_FIELDS:
            errors.append(f"output_template: '{ref}' has unknown sub-field '{parts[2]}'")

    return errors


def validate_spec(raw):
    """Return a list of human-readable problems. Empty list means valid.

    Never raises — callers decide whether a bad spec is fatal (it is not: a
    broken file is skipped with a warning rather than blocking server start).
    """
    errors = []
    if not isinstance(raw, dict):
        return ["spec must be a JSON object"]

    if raw.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"'schema_version' must be {SCHEMA_VERSION}, got {raw.get('schema_version')!r}"
        )

    wid = raw.get("id")
    if not isinstance(wid, str) or not _ID_RE.match(wid or ""):
        errors.append(f"'id' must be lowercase [a-z0-9_], got {wid!r}")

    for key in ("title", "description"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            errors.append(f"'{key}' must be a non-empty string")

    max_fu = raw.get("max_follow_ups", 2)
    if not isinstance(max_fu, int) or not (0 <= max_fu <= 5):
        errors.append("'max_follow_ups' must be an int 0-5")

    sections = raw.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("'sections' must be a non-empty list")
    else:
        seen = set()
        for section in sections:
            if not isinstance(section, dict):
                errors.append("each section must be an object")
                continue
            _validate_section(section, seen, errors)

    output = raw.get("output_template")
    if not isinstance(output, dict):
        errors.append("'output_template' must be an object")
    else:
        if not isinstance(output.get("body"), str) or not output["body"].strip():
            errors.append("'output_template.body' must be a non-empty string")
        if not isinstance(output.get("filename", ""), str):
            errors.append("'output_template.filename' must be a string")

    # Only worth checking once the structure it references is known-good.
    if not errors:
        errors.extend(_template_field_errors(raw))

    return errors


# --- Loading ------------------------------------------------------------------

def spec_hash(spec):
    """Stable short digest of a spec, used to detect drift mid-interview."""
    import hashlib

    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _substitute_site_values(obj):
    """Replace {site_name} / {contact_email} throughout a spec.

    Lets a shipped spec read naturally without hardcoding any organization
    string into it, which CLAUDE.md forbids outside the two config files.
    """
    replacements = {
        "{site_name}": site_config.SITE_NAME,
        "{site_short_name}": site_config.SITE_SHORT_NAME,
        "{contact_email}": site_config.CONTACT_EMAIL,
    }
    if isinstance(obj, str):
        for token, value in replacements.items():
            obj = obj.replace(token, value)
        return obj
    if isinstance(obj, list):
        return [_substitute_site_values(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _substitute_site_values(value) for key, value in obj.items()}
    return obj


def load_spec(path):
    """Load and validate one spec file. Raises WorkflowSpecError on any problem."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise WorkflowSpecError(f"{path}: could not read as JSON ({exc})") from exc

    raw = _substitute_site_values(raw)
    errors = validate_spec(raw)
    if errors:
        raise WorkflowSpecError(f"{path}: " + "; ".join(errors))
    return raw, spec_hash(raw)


def workflows_dir():
    """Directory to load specs from. WORKFLOWS_DIR overrides the config default."""
    configured = os.environ.get("WORKFLOWS_DIR") or getattr(
        site_config, "WORKFLOWS_DIR", "workflows"
    )
    if os.path.isabs(configured):
        return configured
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, configured)


_registry = {}


def load_all(directory=None):
    """Populate the registry from ``directory``. Returns {id: {spec, hash, path}}.

    A file that fails validation is reported and skipped — one malformed spec
    must never stop the server from booting or take the other workflows down
    with it.
    """
    global _registry
    directory = directory or workflows_dir()
    registry = {}

    if not os.path.isdir(directory):
        _registry = registry
        return registry

    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            spec, digest = load_spec(path)
        except WorkflowSpecError as exc:
            print(f"[workflows] SKIPPED invalid spec: {exc}")
            continue
        if spec["id"] in registry:
            print(f"[workflows] SKIPPED {path}: duplicate workflow id '{spec['id']}'")
            continue
        registry[spec["id"]] = {"spec": spec, "hash": digest, "path": path}

    _registry = registry
    return registry


def reload():
    """Re-read the spec directory. Used by tests and any future admin hook."""
    return load_all()


def get(workflow_id):
    """Return {'spec', 'hash', 'path'} for a workflow id, or None."""
    return _registry.get(workflow_id)


def all_ids():
    return sorted(_registry)


def count_questions(spec):
    """Total questions, counting a repeating section's block only once."""
    return sum(len(section.get("questions", [])) for section in spec.get("sections", []))


def summaries():
    """Compact list for the widget's launcher. No answers, no template."""
    out = []
    for workflow_id in all_ids():
        entry = _registry[workflow_id]
        spec = entry["spec"]
        out.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "description": spec["description"],
                "estimated_minutes": spec.get("estimated_minutes"),
                "sections": len(spec.get("sections", [])),
                "questions": count_questions(spec),
                "spec_hash": entry["hash"],
            }
        )
    return out
