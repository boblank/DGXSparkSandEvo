import json
import subprocess
import sys
import unittest
from pathlib import Path

from knowledge.knowledge_adapter import (
    DEFAULT_GRAPH_PATH,
    DEFAULT_SOURCE_PATH,
    EVIDENCE_CLASSES,
    load_catalog,
    load_graph,
    load_interactive_cards,
    lookup_knowledge,
    lookup_sources,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PATH = REPO_ROOT / "knowledge" / "knowledge_adapter.py"


class KnowledgeAdapterTests(unittest.TestCase):
    def test_catalog_has_unique_valid_sources_and_required_coverage(self) -> None:
        catalog = load_catalog()
        source_ids = [source["source_id"] for source in catalog["sources"]]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertGreaterEqual(len(source_ids), 10)

        card_ids = {
            card_id
            for source in catalog["sources"]
            for card_id in source["knowledge_card_ids"]
        }
        self.assertTrue(
            {"ENDOSYMBIOSIS", "MULTICELLULARITY", "FUTURE_SCENARIO"}
            <= card_ids
        )

    def test_lookup_by_knowledge_card_id(self) -> None:
        result = lookup_sources(knowledge_card_id="endosymbiosis")
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["count"], 0)
        self.assertTrue(
            all(
                "ENDOSYMBIOSIS" in source["knowledge_card_ids"]
                for source in result["sources"]
            )
        )

    def test_lookup_by_transition_id(self) -> None:
        result = lookup_sources(transition_id="m05")
        self.assertEqual(result["query"], {"transition_id": "M05"})
        self.assertGreater(result["count"], 0)
        self.assertTrue(
            all("M05" in source["transition_ids"] for source in result["sources"])
        )

    def test_lookup_sources_by_pressure_id(self) -> None:
        result = lookup_sources(pressure_id="ocean_acidification")
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["count"], 0)
        self.assertTrue(
            all(
                "OCEAN_ACIDIFICATION" in source["pressure_ids"]
                for source in result["sources"]
            )
        )

    def test_unknown_id_is_explicit_empty_result(self) -> None:
        result = lookup_sources(transition_id="M99")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["message"], "无可用来源")

    def test_exactly_one_query_is_required(self) -> None:
        with self.assertRaises(ValueError):
            lookup_sources()
        with self.assertRaises(ValueError):
            lookup_sources(knowledge_card_id="ENDOSYMBIOSIS", transition_id="M02")

    def test_graph_covers_historical_transitions_and_future_pressures(self) -> None:
        graph = load_graph()
        transition_ids = {edge["transition_id"] for edge in graph["edges"]}
        self.assertTrue(
            {"M02", "M03", "M04", "M05", "M07", "M08", "M09", "M10"}
            <= transition_ids
        )
        pressure_ids = {
            node["pressure_id"]
            for node in graph["nodes"]
            if "pressure_id" in node
        }
        self.assertEqual(
            pressure_ids,
            {
                "GLOBAL_WARMING",
                "SEA_LEVEL_RISE",
                "OCEAN_DEOXYGENATION",
                "OCEAN_ACIDIFICATION",
            },
        )
        for item in [*graph["nodes"], *graph["edges"]]:
            self.assertIn(item["evidence_class"], EVIDENCE_CLASSES)
            self.assertIn("prerequisites", item)
            self.assertIn("mechanisms", item)
            self.assertIn("tradeoffs", item)
            self.assertIn("source_ids", item)
            self.assertTrue(item["boundary"])

    def test_interactive_cards_cover_requested_concepts(self) -> None:
        cards = load_interactive_cards()
        by_id = {card["knowledge_card_id"]: card for card in cards}
        self.assertTrue(
            {
                "ENDOSYMBIOSIS",
                "PLASTID_ENDOSYMBIOSIS",
                "MULTICELLULARITY",
                "SEXUAL_REPRODUCTION",
                "WATER_TO_LAND",
                "FLIGHT",
                "NERVOUS_SYSTEM_AND_BRAIN",
                "SOCIAL_COOPERATION",
                "FUTURE_GLOBAL_WARMING",
                "FUTURE_SEA_LEVEL_RISE",
                "FUTURE_OCEAN_DEOXYGENATION",
                "FUTURE_OCEAN_ACIDIFICATION",
            }
            <= set(by_id)
        )
        for card in cards:
            self.assertIn(card["evidence_class"], EVIDENCE_CLASSES)
            self.assertTrue(card["source_ids"])
            self.assertTrue(card["boundary"])

    def test_transition_resolution_returns_graph_card_and_sources(self) -> None:
        result = lookup_knowledge(transition_id="m02")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["edges"])
        self.assertEqual(
            {card["knowledge_card_id"] for card in result["knowledge_cards"]},
            {"ENDOSYMBIOSIS"},
        )
        self.assertTrue(result["sources"])

    def test_pressure_resolution_is_scenario_extrapolation(self) -> None:
        result = lookup_knowledge(pressure_id="global_warming")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["knowledge_cards"])
        self.assertTrue(
            all(
                card["evidence_class"] == "scenario_extrapolation"
                for card in result["knowledge_cards"]
            )
        )

    def test_unknown_transition_is_no_match_without_invented_sources(self) -> None:
        result = lookup_knowledge(transition_id="M99")
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["knowledge_cards"], [])
        self.assertEqual(result["sources"], [])

    def test_cli_outputs_json_and_empty_lookup_is_not_an_error(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ADAPTER_PATH), "--knowledge-card-id", "UNKNOWN"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["sources"], [])

    def test_cli_explain_outputs_no_match(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ADAPTER_PATH),
                "--transition-id",
                "M99",
                "--explain",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "no_match")
        self.assertEqual(payload["sources"], [])

    def test_invalid_catalog_fails_closed(self) -> None:
        invalid_path = Path(self.id().replace(".", "_"))
        try:
            invalid_path.write_text('{"sources": [{"source_id": "incomplete"}]}')
            with self.assertRaises(ValueError):
                load_catalog(invalid_path)
        finally:
            invalid_path.unlink(missing_ok=True)

    def test_default_catalog_path_exists(self) -> None:
        self.assertTrue(DEFAULT_SOURCE_PATH.is_file())
        self.assertTrue(DEFAULT_GRAPH_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
