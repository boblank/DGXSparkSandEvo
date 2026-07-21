from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = ROOT / "skills" / "evolution" / "evolution_helper.py"
SPEC = importlib.util.spec_from_file_location("evolution_helper", HELPER_PATH)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class EvolutionHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(
            (ROOT / "skills/evolution/evolution_schema.json").read_text()
        )
        cls.rules = json.loads(
            (ROOT / "skills/evolution/evolution_rules.json").read_text()
        )
        manifest = json.loads(
            (ROOT / "demo-assets/fixtures/evolution_manifest.normal.json").read_text()
        )
        cls.plan = {
            key: copy.deepcopy(manifest[key])
            for key in (
                "scenario",
                "story_stages",
                "routes",
                "selected_route_id",
                "decision_summary",
            )
        }
        for stage in cls.plan["story_stages"]:
            for key in ("status", "render_source", "image_path", "visual_review_status"):
                stage.pop(key, None)

    def test_normal_fixture_plan_matches_schema_and_semantics(self) -> None:
        self.assertEqual(helper.validate_schema(self.plan, self.schema), [])
        self.assertEqual(helper.semantic_errors(self.plan), [])

    def test_schema_rejects_extra_fields(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["unexpected"] = "unsafe"
        errors = helper.validate_schema(plan, self.schema)
        self.assertTrue(any("extra" in error for error in errors))

    def test_step_schema_avoids_unsupported_unique_items_keyword(self) -> None:
        encoded = json.dumps(self.schema, sort_keys=True)
        self.assertNotIn('"uniqueItems"', encoded)

    def test_semantics_rejects_duplicate_trait_ids(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["routes"][0]["trait_ids"].append(plan["routes"][0]["trait_ids"][0])
        self.assertTrue(
            any("duplicate trait IDs" in error for error in helper.semantic_errors(plan))
        )

    def test_semantics_rejects_stage_reordering(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["story_stages"][0], plan["story_stages"][1] = (
            plan["story_stages"][1],
            plan["story_stages"][0],
        )
        self.assertIn(
            "story_stages must use the fixed order",
            helper.semantic_errors(plan),
        )

    def test_semantics_rejects_unsupported_pseudo_precision(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["routes"][0]["benefits"][0] = "在低氧下存活率提高40%"
        self.assertTrue(
            any(
                "unsupported pseudo-precision" in error
                for error in helper.semantic_errors(plan)
            )
        )

    def test_normalization_owns_order_cross_references_and_duplicates(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["story_stages"] = [
            plan["story_stages"][2],
            plan["story_stages"][0],
            plan["story_stages"][1],
        ]
        for stage in plan["story_stages"]:
            stage["transition_id"] = "M12"
            stage["time_scope"] = "FUTURE_SCENARIO"
            stage["knowledge_card_id"] = "NONE"
            stage["route_reference_id"] = "energy_saver"
            stage["evidence_tag"] = "SCENARIO_EXTRAPOLATION"
        plan["routes"].reverse()
        for route in plan["routes"]:
            route["strategy_slot"] = "DEFENSE_AMBUSH"
        plan["routes"][0]["trait_ids"].append(
            plan["routes"][0]["trait_ids"][0]
        )

        helper.normalize_plan_contract(plan)

        self.assertEqual(
            [stage["stage_id"] for stage in plan["story_stages"]],
            helper.STAGE_ORDER,
        )
        self.assertEqual(
            [stage["transition_id"] for stage in plan["story_stages"]],
            helper.TRANSITION_ORDER,
        )
        self.assertEqual(
            [route["route_id"] for route in plan["routes"]],
            helper.ROUTE_IDS,
        )
        self.assertEqual(
            [route["strategy_slot"] for route in plan["routes"]],
            helper.STRATEGY_SLOTS,
        )
        self.assertEqual(helper.semantic_errors(plan), [])

    def test_normalization_limits_visible_demo_conflict_to_defense_route(self) -> None:
        plan = copy.deepcopy(self.plan)
        for route in plan["routes"]:
            route["trait_ids"] = list(helper.DEMO_CONFLICT_TRAITS)

        helper.normalize_plan_contract(plan)
        rejections = helper.apply_deterministic_rules(plan, self.rules)

        self.assertEqual(
            [(item["route_id"], item["rule_id"]) for item in rejections],
            [("defense_ambush", "TRAIT_CONFLICT_HEAVY_FAST_CHEAP")],
        )
        self.assertTrue(
            all(
                not set(helper.DEMO_CONFLICT_TRAITS).intersection(route["trait_ids"])
                for route in plan["routes"]
            )
        )
        selected_route = next(
            route
            for route in plan["routes"]
            if route["route_id"] == plan["selected_route_id"]
        )
        self.assertEqual(plan["story_stages"][2]["after_state"], selected_route["description"])
        self.assertEqual(plan["story_stages"][2]["benefits"], selected_route["benefits"])
        self.assertEqual(plan["story_stages"][2]["costs"], selected_route["costs"])

    def test_deterministic_conflict_revision_is_visible(self) -> None:
        plan = copy.deepcopy(self.plan)
        defense = next(
            route
            for route in plan["routes"]
            if route["route_id"] == "defense_ambush"
        )
        defense["trait_ids"] = [
            "HEAVY_ARMOR",
            "EXTREMELY_HIGH_SWIM_SPEED",
            "EXTREMELY_LOW_ENERGY_USE",
        ]
        rejections = helper.apply_deterministic_rules(plan, self.rules)
        self.assertEqual(rejections[0]["rule_id"], "TRAIT_CONFLICT_HEAVY_FAST_CHEAP")
        self.assertNotIn("HEAVY_ARMOR", defense["trait_ids"])
        self.assertIn("LOCAL_ARMOR", defense["trait_ids"])
        self.assertIn("AMBUSH_BEHAVIOR", defense["trait_ids"])
        self.assertTrue(any("局部防护" in item for item in defense["benefits"]))
        self.assertTrue(any("持续游速受限" in item for item in defense["costs"]))

    def test_knowledge_payload_uses_only_referenced_sources(self) -> None:
        knowledge = json.loads(
            (ROOT / "skills/evolution/knowledge_cards.json").read_text()
        )
        cards, sources = helper.build_knowledge_payload(knowledge)
        self.assertEqual(
            {card["knowledge_card_id"] for card in cards},
            {"ENDOSYMBIOSIS", "MULTICELLULARITY"},
        )
        referenced = {
            source_id
            for card in cards
            for source_id in card["source_ids"]
        }
        self.assertEqual(
            {source["source_id"] for source in sources},
            referenced,
        )


if __name__ == "__main__":
    unittest.main()
