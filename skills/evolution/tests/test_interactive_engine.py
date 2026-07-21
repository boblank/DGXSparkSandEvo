from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
ENGINE_PATH = ROOT / "skills" / "evolution" / "interactive_engine.py"
SPEC = importlib.util.spec_from_file_location("interactive_engine", ENGINE_PATH)
assert SPEC and SPEC.loader
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


def first_selection(envelope: dict, round_no: int) -> dict:
    choices = envelope["choices"]
    return {
        "environment_id": choices["environments"][0]["id"],
        "contingency_id": choices["contingencies"][0]["id"],
        "direction_id": choices["directions"][0]["id"],
        "expected_round": round_no,
    }


class InteractiveEvolutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dry_run_completes_three_inherited_rounds_and_persists_images(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session()
        session_id = envelope["session"]["session_id"]
        self.assertEqual(envelope["session"]["round_index"], 0)
        self.assertEqual(envelope["choices"]["round"], 1)

        previous_trait = envelope["session"]["current_stage"]["traits"][0]
        for round_no in range(1, 4):
            envelope = service.evolve(
                session_id,
                first_selection(envelope, round_no),
            )
            stage = envelope["session"]["current_stage"]
            self.assertIn(previous_trait, stage["traits"])
            self.assertTrue(
                service.asset_path(session_id, stage["image_url"].rsplit("/", 1)[1]).is_file()
            )
            previous_trait = stage["traits"][0]

        self.assertEqual(envelope["session"]["status"], "completed")
        self.assertEqual(envelope["session"]["round_index"], 3)
        self.assertEqual(len(envelope["session"]["history"]), 4)
        self.assertEqual(envelope["choices"]["directions"], [])
        future_match = envelope["session"]["current_stage"]["knowledge_match"]
        self.assertEqual(future_match["status"], "matched")
        self.assertEqual(future_match["match_scope"], "external_pressure")
        self.assertEqual(future_match["generated_outcome_status"], "no_match")
        persisted = json.loads((self.root / session_id / "session.json").read_text())
        self.assertEqual(persisted["history"], envelope["session"]["history"])

    def test_known_transition_returns_curated_knowledge(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session()
        payload = first_selection(envelope, 1)
        payload["direction_id"] = "endosymbiotic_cell"
        envelope = service.evolve(envelope["session"]["session_id"], payload)
        match = envelope["session"]["current_stage"]["knowledge_match"]
        self.assertEqual(match["status"], "matched")
        self.assertEqual(match["knowledge_card_id"], "ENDOSYMBIOSIS")
        self.assertTrue(match["sources"])

    def test_second_round_filters_incompatible_sexual_cycle_after_prokaryotic_consortium(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session()
        payload = first_selection(envelope, 1)
        payload["direction_id"] = "metabolic_consortium"
        envelope = service.evolve(envelope["session"]["session_id"], payload)
        direction_ids = {item["id"] for item in envelope["choices"]["directions"]}
        self.assertNotIn("sexual_cycle", direction_ids)
        self.assertIn("multicellular_body", direction_ids)
        self.assertIn("cooperative_colony", direction_ids)

    def test_interactive_cards_override_legacy_and_unknown_stays_explicit(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        social = service._knowledge_match("SOCIAL_COOPERATION", "M10")
        microbial = service._knowledge_match("MICROBIAL_CONSORTIUM", "M11")
        unknown = service._knowledge_match("NOT_CURATED", "M99")
        self.assertEqual(social["status"], "matched")
        self.assertEqual(microbial["status"], "matched")
        self.assertEqual(unknown["status"], "no_match")
        self.assertEqual(unknown["generated_outcome_status"], "no_match")

    def test_stale_round_and_unknown_choice_are_rejected_without_advancing(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session()
        session_id = envelope["session"]["session_id"]
        stale = first_selection(envelope, 1)
        stale["expected_round"] = 2
        with self.assertRaises(engine.InteractiveError) as context:
            service.evolve(session_id, stale)
        self.assertEqual(context.exception.code, "round_conflict")
        unknown = first_selection(envelope, 1)
        unknown["environment_id"] = "not_in_catalog"
        with self.assertRaises(engine.InteractiveError) as context:
            service.evolve(session_id, unknown)
        self.assertEqual(context.exception.code, "invalid_choice")
        self.assertEqual(service.get_session(session_id)["session"]["round_index"], 0)

    def test_failure_state_never_persists_downstream_secret(self) -> None:
        secret = "never-write-this-key"

        def failing_planner(previous: dict, selection: dict, spec: dict):
            raise RuntimeError("downstream failed with " + secret)

        service = engine.InteractiveEvolutionService(
            self.root,
            planner=failing_planner,
            renderer=lambda *args: {},
        )
        envelope = service.create_session()
        session_id = envelope["session"]["session_id"]
        with self.assertRaises(engine.InteractiveError) as context:
            service.evolve(session_id, first_selection(envelope, 1))
        self.assertNotIn(secret, context.exception.public_message)
        persisted_text = (self.root / session_id / "session.json").read_text()
        self.assertNotIn(secret, persisted_text)
        self.assertEqual(json.loads(persisted_text)["status"], "error")

    def test_step_request_uses_high_reasoning_and_strict_json_schema(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session()
        session = envelope["session"]
        spec = service._round_spec(1)
        selection = service._validate_selection(spec, first_selection(envelope, 1))
        valid = {
            "organism_name": "共生细胞",
            "lineage_summary": "它保留原有细胞膜，并形成稳定的内共生结构。",
            "change_summary": "稳定共生的后代逐步积累。",
            "traits": ["原有细胞膜", "内共生能量结构"],
            "internal_causes": ["可遗传的伙伴协调能力"],
            "external_causes": ["资源和氧气波动"],
            "benefits": ["能量供给更稳定"],
            "costs": ["双方需要同步资源和复制"],
            "evidence_tag": "KNOWN_MECHANISM",
            "uncertainty_note": "具体事件顺序仍有争议。",
            "image_prompt": "A single translucent early eukaryotic cell with an amber bacterial endosymbiont",
        }
        captured: dict = {}

        def fake_post(url: str, body: dict, headers: dict, timeout: int) -> dict:
            captured.update({"url": url, "body": body, "headers": headers, "timeout": timeout})
            return {
                "model": engine.STEP_MODEL,
                "choices": [{"message": {"content": json.dumps(valid)}, "finish_reason": "stop"}],
            }

        with mock.patch.dict(os.environ, {"STEP_API_KEY": "test-key"}, clear=False):
            with mock.patch.object(engine.helper, "post_json", side_effect=fake_post):
                result, metadata = service._plan_with_step(
                    session["current_stage"],
                    selection,
                    spec,
                )
        self.assertEqual(result, valid)
        self.assertEqual(captured["body"]["model"], "step-3.7-flash")
        self.assertEqual(captured["body"]["reasoning_effort"], "high")
        schema_contract = captured["body"]["response_format"]["json_schema"]
        self.assertTrue(schema_contract["strict"])
        self.assertEqual(schema_contract["schema"], service.schema)
        self.assertTrue(metadata["strict_schema"])

    def test_strict_schema_contains_no_unsupported_unique_items(self) -> None:
        schema_text = (ROOT / "skills/evolution/interactive_schema.json").read_text()
        self.assertNotIn('"uniqueItems"', schema_text)


if __name__ == "__main__":
    unittest.main()
