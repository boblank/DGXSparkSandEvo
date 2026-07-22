from __future__ import annotations

import copy
import importlib.util
import hashlib
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

    def test_validated_first_last_frame_video_is_preferred_over_static_recap(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        for round_no in range(1, 4):
            envelope = service.evolve(session_id, first_selection(envelope, round_no))
        session = json.loads((self.root / session_id / "session.json").read_text())
        session_dir = self.root / session_id
        flf_root = session_dir / "lineage_flf"
        flf_root.mkdir()
        output = flf_root / "lineage_flf_complete.mp4"
        output.write_bytes(b"real-first-last-frame-video" * 5000)
        stage_hashes = []
        for index, stage in enumerate(session["history"]):
            stage_path = session_dir / Path(stage["image_url"]).name
            stage_hashes.append(
                {
                    "round": int(stage.get("round", index)),
                    "sha256": hashlib.sha256(stage_path.read_bytes()).hexdigest(),
                }
            )
        manifest = {
            "contract_version": "1.0.0",
            "passed": True,
            "session": {
                "session_id": session_id,
                "updated_at": session["updated_at"],
                "stage_sha256": stage_hashes,
            },
            "merged": {
                "passed": True,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            },
        }
        (flf_root / "lineage_flf_validation.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        self.assertEqual(service.lineage_video_path(session_id), output.resolve())

    def test_selected_environment_recomputes_downstream_candidates(self) -> None:
        calls: list[tuple[str, str | None]] = []

        def option_planner(previous, spec, environment, contingency):
            del previous
            calls.append((environment["id"], contingency["id"] if contingency else None))
            direction_ids = {
                "oxygen_poor_pool": ["air_breathing", "bottom_support"],
                "weedy_shallows": ["bottom_support", "deepwater_return"],
            }[environment["id"]]
            return {
                "contingencies": [
                    {"id": item["id"], "reason": "当前环境会改变这件事的后果。"}
                    for item in spec["contingencies"][:2]
                ],
                "directions": [
                    {"id": item_id, "reason": "这条路线与当前压力和已有性状更贴近。"}
                    for item_id in direction_ids
                ],
            }, {"planner": "fixture", "strict_schema": True}

        service = engine.InteractiveEvolutionService(
            self.root,
            dry_run=True,
            option_planner=option_planner,
        )
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        low_oxygen = service.contextualize_choices(
            session_id,
            {"expected_round": 1, "environment_id": "oxygen_poor_pool"},
        )
        weeds = service.contextualize_choices(
            session_id,
            {"expected_round": 1, "environment_id": "weedy_shallows"},
        )
        self.assertEqual(calls, [("oxygen_poor_pool", None), ("weedy_shallows", None)])
        self.assertEqual(
            [item["id"] for item in low_oxygen["directions"]],
            ["air_breathing", "bottom_support"],
        )
        self.assertEqual(
            [item["id"] for item in weeds["directions"]],
            ["bottom_support", "deepwater_return"],
        )
        self.assertTrue(all(item.get("context_reason") for item in weeds["directions"]))
        contextual = service.contextualize_choices(
            session_id,
            {
                "expected_round": 1,
                "environment_id": "weedy_shallows",
                "contingency_id": weeds["contingencies"][0]["id"],
            },
        )
        evolved = service.evolve(
            session_id,
            {
                "expected_round": 1,
                "environment_id": "weedy_shallows",
                "contingency_id": contextual["contingencies"][0]["id"],
                "direction_id": contextual["directions"][0]["id"],
            },
        )
        stage_selection = evolved["session"]["current_stage"]["selection"]
        self.assertTrue(stage_selection["contingency"]["context_reason"])
        self.assertTrue(stage_selection["direction"]["context_reason"])
        self.assertEqual(
            evolved["session"]["current_stage"]["model"]["choice_planner"]["planner"],
            "fixture",
        )
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

    def test_open_world_registry_creates_seven_distinct_scenarios(self) -> None:
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
                "avian_flight",
                "hominin_origins",
                "space_human_future",
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
        self.assertEqual(len(first_chapters), 7)
        self.assertEqual(len(first_organisms), 7)

    def test_historical_key_innovation_survives_later_rounds(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        envelope = service.evolve(
            session_id,
            {
                "environment_id": "oxygen_poor_pool",
                "contingency_id": "stronger_wrist_joint",
                "direction_id": "bottom_support",
                "expected_round": 1,
            },
        )
        first_stage = envelope["session"]["current_stage"]
        self.assertIn("能在浅水承重的成对附肢", first_stage["protected_traits"])
        self.assertNotIn("有内部骨骼支撑的肉质鳍", first_stage["protected_traits"])
        envelope = service.evolve(
            session_id,
            {
                "environment_id": "seasonal_drought",
                "contingency_id": "reinforced_rib",
                "direction_id": "pool_hopper",
                "expected_round": 2,
            },
        )
        second_stage = envelope["session"]["current_stage"]
        self.assertIn("能在湿泥短距推进的成对附肢", second_stage["protected_traits"])
        self.assertNotIn("能在浅水承重的成对附肢", second_stage["protected_traits"])
        envelope = service.evolve(
            session_id,
            {
                "environment_id": "mudflat_expands",
                "contingency_id": "broader_digits",
                "direction_id": "amphibious_edge",
                "expected_round": 3,
            },
        )
        final_stage = envelope["session"]["current_stage"]
        self.assertIn("带趾、仍适合浅水的承重附肢", final_stage["protected_traits"])
        self.assertIn("带趾、仍适合浅水的承重附肢", final_stage["traits"])
        self.assertNotIn("能在湿泥短距推进的成对附肢", final_stage["protected_traits"])

    def test_historical_route_exposes_real_taxon_reference_before_rendering(self) -> None:
        service = engine.InteractiveEvolutionService(
            self.root,
            dry_run=True,
            review_mode="off",
        )
        envelope = service.create_session("devonian_estuary")
        envelope = service.evolve(
            envelope["session"]["session_id"],
            {
                "environment_id": "weedy_shallows",
                "contingency_id": "stronger_wrist_joint",
                "direction_id": "bottom_support",
                "expected_round": 1,
            },
        )

        reference = envelope["session"]["current_stage"]["historical_reference"]
        self.assertEqual(reference["status"], "historical_reference")
        self.assertEqual(reference["candidates"][0]["taxon_id"], "TAXON_TIKTAALIK_ROSEAE")
        self.assertTrue(reference["required_external_traits"])
        self.assertTrue(reference["required_internal_traits"])
        self.assertNotIn("score_components", reference["candidates"][0])

    def test_historical_choices_cannot_skip_required_branch(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        payload = first_selection(envelope, 1)
        payload["direction_id"] = "deepwater_return"
        envelope = service.evolve(envelope["session"]["session_id"], payload)
        direction_ids = {item["id"] for item in envelope["choices"]["directions"]}
        self.assertNotIn("pool_hopper", direction_ids)
        self.assertIn("buried_dormancy", direction_ids)

    def test_landing_route_requires_the_actual_appendage_ledger(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        envelope = service.evolve(
            session_id,
            {
                "environment_id": "oxygen_poor_pool",
                "contingency_id": "air_gulping_reflex",
                "direction_id": "air_breathing",
                "expected_round": 1,
            },
        )
        envelope = service.evolve(
            session_id,
            {
                "environment_id": "shoreline_food",
                "contingency_id": "shoreline_hearing",
                "direction_id": "shoreline_ambush",
                "expected_round": 2,
            },
        )
        direction_ids = {item["id"] for item in envelope["choices"]["directions"]}
        self.assertNotIn("amphibious_edge", direction_ids)
        self.assertNotIn("shoreline_forager", direction_ids)
        self.assertIn("aquatic_specialist", direction_ids)

    def test_future_world_keeps_open_outcomes_explicit(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("space_human_future")
        self.assertEqual(envelope["session"]["scenario"]["constraint_mode"], "future_scenario")
        for round_no in range(1, 4):
            envelope = service.evolve(
                envelope["session"]["session_id"],
                first_selection(envelope, round_no),
            )
            stage = envelope["session"]["current_stage"]
            self.assertEqual(stage["evidence_tag"], "SCENARIO_EXTRAPOLATION")
            self.assertIn("未来", stage["uncertainty_note"])

    def test_planner_prompt_separates_historical_reconstruction_from_future_scenario(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        historical = service.create_session("devonian_estuary")
        historical_spec = service._round_spec(1, "devonian_estuary")
        historical_selection = service._validate_selection(
            historical_spec,
            first_selection(historical, 1),
        )
        historical_system = service._messages(
            historical["session"]["current_stage"], historical_selection, historical_spec
        )[0]["content"]
        self.assertIn("历史重建模式", historical_system)
        self.assertIn("关键性状不得无解释消失", historical_system)

        revision_spec = copy.deepcopy(historical_spec)
        revision_spec["review_revision"] = {
            "issue_codes": ["PROTECTED_TRAIT_MISSING"],
            "summary": "规划漏掉了受保护性状。",
            "required_protected_traits": ["有内部骨骼支撑的肉质鳍", "水下呼吸为主"],
        }
        revision_prompt = service._messages(
            historical["session"]["current_stage"], historical_selection, revision_spec
        )[1]["content"]
        self.assertIn("本次必须逐字写入 traits", revision_prompt)
        self.assertIn("有内部骨骼支撑的肉质鳍；水下呼吸为主", revision_prompt)

        future = service.create_session("space_human_future")
        future_spec = service._round_spec(1, "space_human_future")
        future_selection = service._validate_selection(future_spec, first_selection(future, 1))
        future_system = service._messages(
            future["session"]["current_stage"], future_selection, future_spec
        )[0]["content"]
        self.assertIn("未来推演模式", future_system)
        self.assertIn("不能把适应性反应写成可遗传演化", future_system)

    def test_planner_receives_historical_taxon_boundaries_and_anatomy(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        spec = service._round_spec(1, "devonian_estuary")
        selection = service._validate_selection(
            spec,
            {
                "environment_id": "weedy_shallows",
                "contingency_id": "stronger_wrist_joint",
                "direction_id": "bottom_support",
                "expected_round": 1,
            },
        )
        spec["historical_reference"] = service._historical_reference(selection, spec)

        messages = service._messages(
            envelope["session"]["current_stage"], selection, spec
        )
        prompt = "\n".join(message["content"] for message in messages)

        self.assertIn("Tiktaalik roseae", prompt)
        self.assertIn("成对肉质鳍", prompt)
        self.assertIn("鳍内具有腕样承重关节", prompt)
        self.assertIn("不能写成直接祖先", prompt)

        evidence = service._review_evidence_pack(selection, spec)
        self.assertEqual(evidence["historical_match_status"], "historical_reference")
        self.assertIn("SRC-LAND-001", evidence["source_ids"])
        self.assertIn("成对肉质鳍", evidence["historical_required_external_traits"])
        self.assertIn(
            "鳍内具有腕样承重关节",
            evidence["historical_required_internal_traits"],
        )

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

    def test_every_world_can_finish_three_rounds_without_losing_historical_ledger(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        for scenario in service.list_scenarios()["scenarios"]:
            envelope = service.create_session(scenario["id"])
            previous_protected = set(
                envelope["session"]["current_stage"].get("protected_traits", [])
            )
            for round_no in range(1, 4):
                self.assertTrue(
                    envelope["choices"]["directions"],
                    f"{scenario['id']} round {round_no} has no reachable direction",
                )
                selected_direction = service._effective_round_spec(
                    envelope["session"], round_no
                )["directions"][0]
                envelope = service.evolve(
                    envelope["session"]["session_id"],
                    first_selection(envelope, round_no),
                )
                stage = envelope["session"]["current_stage"]
                if scenario["constraint_mode"] == "historical_reconstruction":
                    current_protected = set(stage.get("protected_traits", []))
                    replacements = {
                        item["from"]: item["to"]
                        for item in selected_direction.get("trait_transformations", [])
                    }
                    for trait in previous_protected:
                        self.assertTrue(
                            trait in current_protected
                            or replacements.get(trait) in current_protected,
                            f"{scenario['id']} round {round_no} lost {trait!r} without a declared transformation",
                        )
                    previous_protected = current_protected
            self.assertEqual(envelope["session"]["status"], "completed")

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
        early_flight = service._knowledge_match("FLIGHT", "B03")
        unknown = service._knowledge_match("NOT_CURATED", "M99")
        self.assertEqual(social["status"], "matched")
        self.assertEqual(microbial["status"], "matched")
        self.assertEqual(
            {source["source_id"] for source in early_flight["sources"]},
            {"SRC-FEA-005"},
        )
        self.assertEqual(unknown["status"], "no_match")
        self.assertEqual(unknown["generated_outcome_status"], "no_match")

    def test_unsupported_historical_alternative_stays_no_match(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        payload = first_selection(envelope, 1)
        payload["direction_id"] = "deepwater_return"
        envelope = service.evolve(envelope["session"]["session_id"], payload)
        stage = envelope["session"]["current_stage"]
        self.assertEqual(stage["evidence_tag"], "TEACHING_SIMPLIFICATION")
        self.assertEqual(stage["knowledge_match"]["status"], "no_match")
        self.assertEqual(stage["knowledge_match"]["sources"], [])

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

    def test_stale_generating_session_recovers_without_skipping_a_round(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session()
        session_id = envelope["session"]["session_id"]
        payload = first_selection(envelope, 1)
        session_path = self.root / session_id / "session.json"
        persisted = json.loads(session_path.read_text(encoding="utf-8"))
        persisted["status"] = "generating"
        session_path.write_text(json.dumps(persisted), encoding="utf-8")

        with self.assertRaises(engine.InteractiveError) as context:
            service.evolve(session_id, payload)
        self.assertEqual(context.exception.code, "session_busy")

        persisted["updated_at"] = "2000-01-01T00:00:00Z"
        session_path.write_text(json.dumps(persisted), encoding="utf-8")
        recovered = service.evolve(session_id, payload)
        self.assertEqual(recovered["session"]["round_index"], 1)
        self.assertEqual(recovered["session"]["status"], "ready")
        recovered_manifest = json.loads(session_path.read_text(encoding="utf-8"))
        self.assertEqual(
            recovered_manifest["generation_recovery"]["stale_updated_at"],
            "2000-01-01T00:00:00Z",
        )
        self.assertEqual(len(recovered_manifest["history"]), 2)

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

    def test_historical_renderer_receives_key_trait_ledger(self) -> None:
        service = engine.InteractiveEvolutionService(self.root, dry_run=True)
        envelope = service.create_session("devonian_estuary")
        envelope = service.evolve(
            envelope["session"]["session_id"],
            {
                "environment_id": "oxygen_poor_pool",
                "contingency_id": "stronger_wrist_joint",
                "direction_id": "bottom_support",
                "expected_round": 1,
            },
        )
        session = envelope["session"]
        session["current_stage"]["image_url"] = (
            f"/api/assets/{session['session_id']}/stage_01.png"
        )
        reference_image = self.root / "stage_01.png"
        from PIL import Image

        parent_fixture = Image.new("RGB", (32, 32), (0, 0, 0))
        for x in range(8, 24):
            for y in range(10, 22):
                parent_fixture.putpixel((x, y), (160, 160, 160))
        parent_fixture.save(reference_image)
        spec = service._effective_round_spec(session, 2)
        selection = service._validate_selection(
            spec,
            {
                "environment_id": "seasonal_drought",
                "contingency_id": "reinforced_rib",
                "direction_id": "pool_hopper",
                "expected_round": 2,
            },
        )
        selection["historical_reference"] = service._historical_reference(
            selection, spec
        )
        captured: dict = {}

        def fake_render(_chain, **kwargs):
            captured.update(kwargs)
            parent_fixture.point(lambda value: min(255, value + 30)).save(kwargs["destination"])
            return mock.Mock(
                generator="fixture",
                renderer="flux1",
                seed=123,
                duration_seconds=1.0,
                fallback_from=None,
                reference_conditioning=True,
            )

        result = {
            "image_prompt": "A Devonian shoreline vertebrate in shallow water"
        }
        with mock.patch.dict(os.environ, {"EVOLAB_UNLOAD_OLLAMA": "0"}, clear=False):
            with mock.patch.object(
                engine.rendering, "render_image_with_fallback", side_effect=fake_render
            ):
                metadata = service._render_with_comfy(
                    result,
                    session["current_stage"],
                    selection,
                    self.root / "stage_02.png",
                )
        self.assertIn("能在浅水承重的成对附肢", captured["prompt"])
        self.assertIn("Do not revert the body to an earlier generic form", captured["prompt"])
        self.assertIn("Use the supplied previous-stage image", captured["prompt"])
        self.assertIn("Devonian appendage lock", captured["prompt"])
        self.assertIn("Required visible change", captured["prompt"])
        self.assertIn("obvious at thumbnail scale", captured["prompt"])
        self.assertIn("Evidence-backed historical analog", captured["prompt"])
        self.assertIn("Tiktaalik-like aquatic lobe-finned body plan", captured["prompt"])
        self.assertIn("still continuous paddles without separate digits", captured["prompt"])
        self.assertIn("separate digits", captured["negative_prompt"])
        self.assertNotIn("Preserve the same individual body plan", captured["prompt"])
        self.assertEqual(captured["reference_image"], reference_image)
        self.assertIs(captured["upload_reference"], engine.video_generation.upload_image)
        self.assertAlmostEqual(metadata["visual_change_score"], 30 / 255, places=2)
        self.assertTrue(metadata["technical_visual_gate"]["passed"])
        self.assertEqual(metadata["visual_review"]["mode"], "technical_only")

    def test_reference_change_gate_rejects_copies_and_identity_jumps(self) -> None:
        from PIL import Image

        parent = self.root / "parent.png"
        copy_like = self.root / "copy-like.png"
        identity_jump = self.root / "identity-jump.png"
        Image.new("RGB", (32, 32), (0, 0, 0)).save(parent)
        Image.new("RGB", (32, 32), (5, 5, 5)).save(copy_like)
        Image.new("RGB", (32, 32), (100, 100, 100)).save(identity_jump)
        self.assertLess(
            engine._visual_change_score(parent, copy_like),
            engine.MIN_REFERENCE_CHANGE,
        )
        self.assertGreater(
            engine._visual_change_score(parent, identity_jump),
            engine.MAX_REFERENCE_CHANGE,
        )

    def test_review_may_request_one_revision_before_rendering(self) -> None:
        planner_calls: list[dict] = []
        review_calls: list[dict] = []
        render_calls: list[Path] = []

        fixture = engine.InteractiveEvolutionService(self.root / "fixture", dry_run=True)

        def planner(previous, selection, spec):
            planner_calls.append(spec)
            return fixture._dry_run_plan(previous, selection, spec)

        def reviewer(draft, previous, selection, spec, evidence):
            del previous, selection, spec, evidence
            review_calls.append(draft)
            verdict = "revise" if len(review_calls) == 1 else "pass"
            return {
                "verdict": verdict,
                "issue_codes": ["PROTECTED_TRAIT_MISSING"] if verdict == "revise" else [],
                "summary": "请保留上一阶段已经建立的关键性状。" if verdict == "revise" else "修订后符合当前证据边界。",
                "source_ids": [],
                "transition_ids": [],
                "pressure_ids": [],
            }, {"adapter": "fixture-reviewer", "version": "1.0", "strict_schema": True}

        def renderer(result, previous, selection, destination):
            del result, previous, selection
            render_calls.append(destination)
            destination.write_bytes(b"image")
            return {"render_source": "generated", "renderer": "fixture"}

        service = engine.InteractiveEvolutionService(
            self.root / "review-revise",
            planner=planner,
            reviewer=reviewer,
            renderer=renderer,
            review_mode="required",
        )
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        envelope = service.evolve(session_id, first_selection(envelope, 1))
        self.assertEqual(len(planner_calls), 2)
        self.assertEqual(planner_calls[1]["review_revision"]["issue_codes"], ["PROTECTED_TRAIT_MISSING"])
        self.assertEqual(
            planner_calls[1]["review_revision"]["required_protected_traits"],
            ["能在浅水承重的成对附肢", "水下呼吸为主"],
        )
        self.assertEqual(len(render_calls), 1)
        summary = envelope["session"]["current_stage"]["review_summary"]
        self.assertEqual(summary["verdict"], "pass")
        self.assertEqual(summary["revision_count"], 1)
        persisted = json.loads((service.data_root / session_id / "session.json").read_text())
        self.assertEqual(len(persisted["review_trace"]), 2)
        self.assertNotIn("review_trace", envelope["session"])

    def test_second_non_pass_blocks_before_renderer(self) -> None:
        fixture = engine.InteractiveEvolutionService(self.root / "fixture-block", dry_run=True)
        render_calls: list[Path] = []

        def planner(previous, selection, spec):
            return fixture._dry_run_plan(previous, selection, spec)

        def reviewer(*_args):
            return {
                "verdict": "revise",
                "issue_codes": ["PROTECTED_TRAIT_MISSING"],
                "summary": "关键性状仍未保留。",
                "source_ids": [],
                "transition_ids": [],
                "pressure_ids": [],
            }, {"adapter": "fixture-reviewer", "version": "1.0", "strict_schema": True}

        def renderer(*args):
            render_calls.append(args[-1])
            return {"render_source": "generated"}

        service = engine.InteractiveEvolutionService(
            self.root / "review-block",
            planner=planner,
            reviewer=reviewer,
            renderer=renderer,
            review_mode="required",
        )
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        with self.assertRaises(engine.InteractiveError) as raised:
            service.evolve(session_id, first_selection(envelope, 1))
        self.assertEqual(raised.exception.code, "review_blocked")
        self.assertEqual(render_calls, [])
        persisted = json.loads((service.data_root / session_id / "session.json").read_text())
        self.assertEqual(persisted["round_index"], 0)
        self.assertEqual(len(persisted["review_trace"]), 2)

    def test_optional_review_failure_uses_rules_only_without_claiming_agent(self) -> None:
        fixture = engine.InteractiveEvolutionService(self.root / "fixture-rules", dry_run=True)

        def planner(previous, selection, spec):
            return fixture._dry_run_plan(previous, selection, spec)

        def unavailable(*_args):
            raise TimeoutError("injected reviewer timeout")

        def renderer(result, previous, selection, destination):
            del result, previous, selection
            destination.write_bytes(b"image")
            return {"render_source": "generated", "renderer": "fixture"}

        service = engine.InteractiveEvolutionService(
            self.root / "review-rules",
            planner=planner,
            reviewer=unavailable,
            renderer=renderer,
            review_mode="optional",
        )
        envelope = service.create_session("devonian_estuary")
        envelope = service.evolve(
            envelope["session"]["session_id"],
            first_selection(envelope, 1),
        )
        summary = envelope["session"]["current_stage"]["review_summary"]
        self.assertEqual(summary["review_mode"], "rules_only")
        self.assertNotIn("agent", json.dumps(summary).lower())

    def test_required_review_timeout_blocks_before_renderer(self) -> None:
        fixture = engine.InteractiveEvolutionService(self.root / "fixture-timeout", dry_run=True)
        render_calls: list[Path] = []

        def planner(previous, selection, spec):
            return fixture._dry_run_plan(previous, selection, spec)

        def timeout(*_args):
            raise TimeoutError("injected timeout")

        def renderer(*args):
            render_calls.append(args[-1])
            return {"render_source": "generated"}

        service = engine.InteractiveEvolutionService(
            self.root / "review-timeout",
            planner=planner,
            reviewer=timeout,
            renderer=renderer,
            review_mode="required",
        )
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        with self.assertRaises(engine.InteractiveError) as raised:
            service.evolve(session_id, first_selection(envelope, 1))
        self.assertEqual(raised.exception.code, "review_unavailable")
        self.assertEqual(render_calls, [])
        persisted = json.loads((service.data_root / session_id / "session.json").read_text())
        self.assertEqual(persisted["review_trace"][-1]["final_fallback_path"], "fail_closed")
        self.assertNotIn("injected timeout", json.dumps(persisted))

    def test_visual_block_keeps_only_sanitized_private_trace(self) -> None:
        fixture = engine.InteractiveEvolutionService(self.root / "fixture-visual", dry_run=True)

        def planner(previous, selection, spec):
            return fixture._dry_run_plan(previous, selection, spec)

        def reviewer(*_args):
            return {
                "verdict": "pass",
                "issue_codes": [],
                "summary": "符合当前证据边界。",
                "source_ids": [],
                "transition_ids": [],
                "pressure_ids": [],
            }, {"adapter": "fixture-reviewer", "version": "1.0"}

        def blocked_renderer(*_args):
            error = engine.InteractiveError(
                "visual_semantic_review_blocked",
                "图像审查没有确认谱系连续性。",
                http_status=502,
                retryable=True,
            )
            error.private_details = {
                "mode": "semantic",
                "verdict": "block",
                "forbidden_findings": ["出现未经声明的翅膀"],
            }
            raise error

        service = engine.InteractiveEvolutionService(
            self.root / "visual-block",
            planner=planner,
            reviewer=reviewer,
            renderer=blocked_renderer,
            review_mode="required",
        )
        envelope = service.create_session("devonian_estuary")
        session_id = envelope["session"]["session_id"]
        with self.assertRaises(engine.InteractiveError):
            service.evolve(session_id, first_selection(envelope, 1))
        persisted = json.loads((service.data_root / session_id / "session.json").read_text())
        self.assertEqual(persisted["visual_review_trace"][-1]["verdict"], "block")
        public = service.get_session(session_id)["session"]
        self.assertNotIn("visual_review_trace", public)


if __name__ == "__main__":
    unittest.main()
