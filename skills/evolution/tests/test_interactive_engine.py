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
        self.assertEqual(
            envelope["session"]["lineage_video_url"],
            f"/api/sessions/{session_id}/lineage-video",
        )

    def test_legacy_english_stage_is_localized_before_reaching_the_browser(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        envelope = service.evolve(
            envelope["session"]["session_id"],
            first_selection(envelope, 1),
        )
        session_id = envelope["session"]["session_id"]
        persisted = json.loads((self.root / session_id / "session.json").read_text())
        stage = persisted["current_stage"]
        stage.update(
            {
                "organism_name": "Devonian delta fish lineage",
                "lineage_summary": "An entirely English lineage summary.",
                "change_summary": "English-only change summary.",
                "traits": ["English trait"],
                "benefits": ["English benefit"],
                "costs": ["English cost"],
            }
        )
        persisted["history"][-1] = stage
        (self.root / session_id / "session.json").write_text(
            json.dumps(persisted, ensure_ascii=False),
            encoding="utf-8",
        )

        public_stage = service.get_session(session_id)["session"]["current_stage"]
        for field in ("organism_name", "lineage_summary", "change_summary"):
            self.assertTrue(engine.CHINESE_PATTERN.search(public_stage[field]))
        for field in ("traits", "benefits", "costs"):
            self.assertTrue(public_stage[field])
            self.assertTrue(all(engine.CHINESE_PATTERN.search(item) for item in public_stage[field]))

    def test_open_world_registry_creates_four_distinct_scenarios(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        registry = service.list_scenarios()
        scenario_ids = [item["id"] for item in registry["scenarios"]]
        self.assertEqual(
            scenario_ids,
            [
                "hydrothermal_origin",
                "tidal_symbiosis",
                "ediacaran_seafloor",
                "devonian_estuary",
            ],
        )
        first_chapters: set[str] = set()
        first_organisms: set[str] = set()
        for scenario_id in scenario_ids:
            envelope = service.create_session(scenario_id)
            session = envelope["session"]
            self.assertEqual(session["scenario_id"], scenario_id)
            self.assertEqual(session["scenario"]["id"], scenario_id)
            self.assertEqual(session["max_rounds"], 3)
            self.assertTrue(envelope["choices"]["directions"])
            first_chapters.add(envelope["choices"]["chapter"])
            first_organisms.add(session["current_stage"]["organism_name"])
            asset_name = session["current_stage"]["image_url"].rsplit("/", 1)[1]
            self.assertTrue(service.asset_path(session["session_id"], asset_name).is_file())
        self.assertEqual(len(first_chapters), 4)
        self.assertEqual(len(first_organisms), 4)

    def test_every_world_has_three_complete_rounds_and_curated_direction_cards(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        for scenario in service.list_scenarios()["scenarios"]:
            catalog = service._catalog_for(scenario["id"])
            self.assertEqual(set(catalog["rounds"]), {"1", "2", "3"})
            for round_no in ("1", "2", "3"):
                spec = catalog["rounds"][round_no]
                self.assertGreaterEqual(len(spec["environments"]), 3)
                self.assertGreaterEqual(len(spec["contingencies"]), 3)
                self.assertGreaterEqual(len(spec["directions"]), 3)
                for direction in spec["directions"]:
                    card_id = direction["knowledge_card_id"]
                    if card_id != "NONE":
                        self.assertIn(card_id, service._cards)
                    else:
                        self.assertTrue(direction["transition_id"])

    def test_prebiotic_dry_run_never_calls_chemical_stages_descendants_or_species(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("hydrothermal_origin")
        session_id = envelope["session"]["session_id"]
        for round_no in range(1, 4):
            envelope = service.evolve(session_id, first_selection(envelope, round_no))
            stage = envelope["session"]["current_stage"]
            visible_copy = " ".join(
                str(stage.get(field, ""))
                for field in ("organism_name", "lineage_summary", "change_summary", "uncertainty_note")
            )
            for forbidden in ("后代", "身体", "物种", "谱系", "下一代"):
                self.assertNotIn(forbidden, visible_copy)

    def test_unknown_scenario_is_rejected_before_session_directory_is_created(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        with self.assertRaises(engine.InteractiveError) as context:
            service.create_session("unknown_world")
        self.assertEqual(context.exception.code, "invalid_scenario")
        self.assertEqual(list(self.root.iterdir()), [])

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

    def test_step_retries_when_visible_copy_is_english(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session()
        session = envelope["session"]
        spec = service._round_spec(1)
        selection = service._validate_selection(spec, first_selection(envelope, 1))
        invalid = {
            "organism_name": "English organism",
            "lineage_summary": "English summary",
            "change_summary": "English change",
            "traits": ["English trait", "Another trait"],
            "internal_causes": ["English cause"],
            "external_causes": ["English pressure"],
            "benefits": ["English benefit"],
            "costs": ["English cost"],
            "evidence_tag": "KNOWN_MECHANISM",
            "uncertainty_note": "English uncertainty",
            "image_prompt": "A scientific creature reconstruction",
        }
        response = {
            "model": engine.STEP_MODEL,
            "choices": [{"message": {"content": json.dumps(invalid)}, "finish_reason": "stop"}],
        }
        with mock.patch.dict(os.environ, {"STEP_API_KEY": "test-key"}, clear=False):
            with mock.patch.object(engine.helper, "post_json", return_value=response) as post:
                with self.assertRaises(engine.InteractiveError) as context:
                    service._plan_with_step(session["current_stage"], selection, spec)
        self.assertEqual(context.exception.code, "planner_invalid_output")
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
