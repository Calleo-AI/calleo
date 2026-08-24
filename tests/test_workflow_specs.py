"""Unit tests for workflow_specs.py — spec loading, validation, and hashing.

Pure functions throughout: no mocks, no network, no ChromaDB. Tests build spec
dicts inline rather than reading the shipped files wherever possible, because
workflows/*.json is explicitly meant to be edited by a deployer — binding
assertions to its contents would make the suite fail on a legitimate edit.
"""
import copy
import json
import os
import sys

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_chatbot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import site_config
import workflow_specs
from workflow_specs import WorkflowSpecError, spec_hash, validate_spec

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "workflows")
FULL_FIXTURE = os.path.join(FIXTURE_DIR, "full_features.json")


def load_fixture():
    with open(FULL_FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# validate_spec — the happy path
# ---------------------------------------------------------------------------

class TestValidateSpecAccepts:
    def test_fixture_is_valid(self):
        assert validate_spec(load_fixture()) == []

    def test_shipped_specs_are_valid(self):
        """The specs in workflows/ must load — a broken one ships a dead feature."""
        directory = workflow_specs.workflows_dir()
        names = [n for n in os.listdir(directory) if n.endswith(".json")]
        assert names, "expected at least one shipped workflow spec"
        for name in names:
            spec, digest = workflow_specs.load_spec(os.path.join(directory, name))
            assert spec["id"]
            assert len(digest) == 12


# ---------------------------------------------------------------------------
# validate_spec — rejections
# ---------------------------------------------------------------------------

class TestValidateSpecRejects:
    def test_non_dict(self):
        assert validate_spec([]) == ["spec must be a JSON object"]

    def test_wrong_schema_version(self):
        spec = load_fixture()
        spec["schema_version"] = 2
        assert any("schema_version" in e for e in validate_spec(spec))

    @pytest.mark.parametrize("key", ["title", "description"])
    def test_missing_required_string(self, key):
        spec = load_fixture()
        del spec[key]
        assert any(key in e for e in validate_spec(spec))

    def test_bad_id_characters(self):
        spec = load_fixture()
        spec["id"] = "Not Valid"
        assert any("'id'" in e for e in validate_spec(spec))

    def test_no_sections(self):
        spec = load_fixture()
        spec["sections"] = []
        assert any("sections" in e for e in validate_spec(spec))

    def test_duplicate_section_ids(self):
        spec = load_fixture()
        spec["sections"].append(copy.deepcopy(spec["sections"][0]))
        assert any("duplicate section id" in e for e in validate_spec(spec))

    def test_duplicate_question_ids_within_a_section(self):
        spec = load_fixture()
        section = spec["sections"][0]
        section["questions"].append(copy.deepcopy(section["questions"][0]))
        assert any("duplicate question id" in e for e in validate_spec(spec))

    def test_same_question_id_in_different_sections_is_fine(self):
        """Question ids are namespaced per section, so reuse across them is legal."""
        spec = load_fixture()
        spec["sections"][2]["questions"][0]["id"] = "name"
        spec["output_template"]["body"] = spec["output_template"]["body"].replace(
            "{{wrap.notes}}", "{{wrap.name}}"
        )
        assert validate_spec(spec) == []

    def test_unknown_expects(self):
        spec = load_fixture()
        spec["sections"][0]["questions"][0]["expects"] = "colour"
        assert any("expects" in e for e in validate_spec(spec))

    def test_choice_without_choices(self):
        spec = load_fixture()
        spec["sections"][0]["questions"][2]["choices"] = []
        assert any("requires a non-empty 'choices'" in e for e in validate_spec(spec))

    def test_empty_prompt(self):
        spec = load_fixture()
        spec["sections"][0]["questions"][0]["prompt"] = "   "
        assert any("prompt" in e for e in validate_spec(spec))

    def test_unknown_capture_field(self):
        spec = load_fixture()
        spec["sections"][0]["questions"][0]["capture"] = ["value", "vibes"]
        assert any("capture" in e for e in validate_spec(spec))

    @pytest.mark.parametrize("bad", [0, 51, "many", -1])
    def test_repeat_max_items_out_of_range(self, bad):
        spec = load_fixture()
        spec["sections"][1]["repeat"]["max_items"] = bad
        assert any("max_items" in e for e in validate_spec(spec))

    def test_repeat_name_field_must_be_a_question_in_that_section(self):
        spec = load_fixture()
        spec["sections"][1]["repeat"]["name_field"] = "nonexistent"
        assert any("name_field" in e for e in validate_spec(spec))

    def test_max_follow_ups_out_of_range(self):
        spec = load_fixture()
        spec["max_follow_ups"] = 99
        assert any("max_follow_ups" in e for e in validate_spec(spec))

    def test_missing_output_template(self):
        spec = load_fixture()
        del spec["output_template"]
        assert any("output_template" in e for e in validate_spec(spec))


# ---------------------------------------------------------------------------
# Template placeholder validation — the check that earns the validator
# ---------------------------------------------------------------------------

class TestTemplateValidation:
    def test_unknown_section_in_placeholder(self):
        spec = load_fixture()
        spec["output_template"]["body"] = "{{ghost.name}}"
        assert any("unknown section" in e for e in validate_spec(spec))

    def test_unknown_question_in_placeholder(self):
        spec = load_fixture()
        spec["output_template"]["body"] = "{{basics.ghost}}"
        assert any("unknown question" in e for e in validate_spec(spec))

    def test_bare_placeholder_outside_a_repeat_block(self):
        spec = load_fixture()
        spec["output_template"]["body"] = "{{name}}"
        assert any("must be section_id.question_id" in e for e in validate_spec(spec))

    def test_unknown_sub_field(self):
        spec = load_fixture()
        spec["output_template"]["body"] = "{{basics.count.vibes}}"
        assert any("unknown sub-field" in e for e in validate_spec(spec))

    def test_repeat_block_over_a_non_repeating_section(self):
        spec = load_fixture()
        spec["output_template"]["body"] = "{{#repeat basics}}{{name}}{{/repeat}}"
        assert any("without 'repeat' set" in e for e in validate_spec(spec))

    def test_repeat_block_over_an_unknown_section(self):
        spec = load_fixture()
        spec["output_template"]["body"] = "{{#repeat ghost}}{{x}}{{/repeat}}"
        assert any("unknown section" in e for e in validate_spec(spec))

    def test_field_inside_repeat_block_must_belong_to_that_section(self):
        spec = load_fixture()
        spec["output_template"]["body"] = "{{#repeat steps}}{{name}}{{/repeat}}"
        assert any("inside repeat block" in e for e in validate_spec(spec))

    def test_confidence_sub_field_inside_repeat_block_is_accepted(self):
        spec = load_fixture()
        spec["output_template"]["body"] = (
            "{{#repeat steps}}{{effort}} {{effort.confidence}}{{/repeat}}"
        )
        assert validate_spec(spec) == []


# ---------------------------------------------------------------------------
# spec_hash
# ---------------------------------------------------------------------------

class TestSpecHash:
    def test_is_stable_across_calls(self):
        spec = load_fixture()
        assert spec_hash(spec) == spec_hash(load_fixture())

    def test_is_insensitive_to_key_order(self):
        spec = load_fixture()
        reordered = dict(reversed(list(spec.items())))
        assert spec_hash(spec) == spec_hash(reordered)

    def test_changes_when_content_changes(self):
        spec = load_fixture()
        before = spec_hash(spec)
        spec["sections"][0]["questions"][0]["prompt"] = "Something else?"
        assert spec_hash(spec) != before

    def test_is_twelve_hex_characters(self):
        digest = spec_hash(load_fixture())
        assert len(digest) == 12
        assert all(c in "0123456789abcdef" for c in digest)


# ---------------------------------------------------------------------------
# Loading, substitution, discovery
# ---------------------------------------------------------------------------

class TestLoading:
    def test_load_spec_returns_spec_and_hash(self):
        spec, digest = workflow_specs.load_spec(FULL_FIXTURE)
        assert spec["id"] == "fixture_full"
        assert digest == spec_hash(spec)

    def test_load_spec_raises_on_invalid_json(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(WorkflowSpecError, match="could not read as JSON"):
            workflow_specs.load_spec(str(path))

    def test_load_spec_raises_on_validation_failure(self, tmp_path):
        spec = load_fixture()
        del spec["title"]
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        with pytest.raises(WorkflowSpecError, match="title"):
            workflow_specs.load_spec(str(path))

    def test_site_placeholders_are_substituted(self, tmp_path):
        spec = load_fixture()
        spec["intro"] = "Welcome to {site_name} — reach us at {contact_email}."
        path = tmp_path / "sub.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        loaded, _ = workflow_specs.load_spec(str(path))
        assert site_config.SITE_NAME in loaded["intro"]
        assert site_config.CONTACT_EMAIL in loaded["intro"]
        assert "{site_name}" not in loaded["intro"]

    def test_load_all_skips_an_invalid_file_and_keeps_the_rest(self, tmp_path, capsys):
        good = load_fixture()
        (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
        (tmp_path / "bad.json").write_text("{oops", encoding="utf-8")

        registry = workflow_specs.load_all(str(tmp_path))
        assert list(registry) == ["fixture_full"]
        assert "SKIPPED" in capsys.readouterr().out

    def test_load_all_skips_a_duplicate_id(self, tmp_path, capsys):
        spec = load_fixture()
        (tmp_path / "a.json").write_text(json.dumps(spec), encoding="utf-8")
        (tmp_path / "b.json").write_text(json.dumps(spec), encoding="utf-8")
        registry = workflow_specs.load_all(str(tmp_path))
        assert len(registry) == 1
        assert "duplicate workflow id" in capsys.readouterr().out

    def test_load_all_on_a_missing_directory_is_empty_not_an_error(self, tmp_path):
        assert workflow_specs.load_all(str(tmp_path / "nope")) == {}

    def test_load_all_ignores_non_json_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "good.json").write_text(json.dumps(load_fixture()), encoding="utf-8")
        assert list(workflow_specs.load_all(str(tmp_path))) == ["fixture_full"]

    def test_workflows_dir_env_override(self, monkeypatch):
        monkeypatch.setenv("WORKFLOWS_DIR", "/tmp/some/where")
        assert workflow_specs.workflows_dir() == "/tmp/some/where"

    def test_workflows_dir_relative_resolves_against_repo_root(self, monkeypatch):
        monkeypatch.delenv("WORKFLOWS_DIR", raising=False)
        resolved = workflow_specs.workflows_dir()
        assert os.path.isabs(resolved)
        assert resolved.endswith(os.path.join("calleo", site_config.WORKFLOWS_DIR)) or \
            resolved.endswith(site_config.WORKFLOWS_DIR)


class TestSummaries:
    def test_summary_shape_and_counts(self, tmp_path):
        (tmp_path / "f.json").write_text(json.dumps(load_fixture()), encoding="utf-8")
        workflow_specs.load_all(str(tmp_path))
        summaries = workflow_specs.summaries()
        assert len(summaries) == 1
        entry = summaries[0]
        assert entry["id"] == "fixture_full"
        assert entry["title"] == "Fixture workflow"
        assert entry["sections"] == 3
        # 3 in basics + 2 in the repeating section + 1 in wrap.
        assert entry["questions"] == 6
        assert len(entry["spec_hash"]) == 12

    def test_summaries_never_leak_the_template_or_answers(self, tmp_path):
        (tmp_path / "f.json").write_text(json.dumps(load_fixture()), encoding="utf-8")
        workflow_specs.load_all(str(tmp_path))
        assert "output_template" not in workflow_specs.summaries()[0]

    def test_get_returns_none_for_unknown_id(self, tmp_path):
        workflow_specs.load_all(str(tmp_path))
        assert workflow_specs.get("nope") is None


@pytest.fixture(autouse=True)
def restore_registry():
    """Several tests repoint the registry at a tmp dir; put it back afterwards."""
    yield
    workflow_specs.load_all()
