from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "scientific_review.py"
SPEC = importlib.util.spec_from_file_location("evolab_scientific_review_tests", MODULE_PATH)
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = review
SPEC.loader.exec_module(review)


class ScientificReviewTests(unittest.TestCase):
    def test_step_review_schema_avoids_unsupported_unique_items(self) -> None:
        self.assertNotIn("uniqueItems", json.dumps(review.REVIEW_SCHEMA))

    def test_historical_draft_cannot_drop_a_protected_trait(self) -> None:
        decision = review.deterministic_review(
            draft={"traits": ["新的呼吸方式"], "lineage_summary": "出现了新的呼吸方式。"},
            previous={"protected_traits": ["能承重的成对附肢"]},
            selection={"direction": {"trait_transformations": []}},
            spec={"world": {"constraint_mode": "historical_reconstruction"}},
            evidence_pack={"status": "matched", "source_ids": ["src-1"]},
        )
        self.assertEqual(decision["verdict"], "revise")
        self.assertIn("PROTECTED_TRAIT_MISSING", decision["issue_codes"])

    def test_future_draft_cannot_call_short_term_adaptation_hereditary(self) -> None:
        decision = review.deterministic_review(
            draft={
                "traits": ["宇航员个体的短期适应被遗传给后代"],
                "lineage_summary": "一次飞行中的身体反应立刻成为遗传演化。",
            },
            previous={"protected_traits": []},
            selection={"direction": {"trait_transformations": []}},
            spec={"world": {"constraint_mode": "future_scenario"}},
            evidence_pack={"status": "matched", "source_ids": ["src-2"]},
        )
        self.assertEqual(decision["verdict"], "revise")
        self.assertIn("ACCLIMATIZATION_AS_HEREDITY", decision["issue_codes"])

    def test_historical_route_cannot_skip_its_parent_branch(self) -> None:
        decision = review.deterministic_review(
            draft={"traits": ["新的结构"], "lineage_summary": "沿当前结构继续。"},
            previous={
                "protected_traits": [],
                "selection": {"direction": {"id": "unrelated_parent"}},
            },
            selection={
                "direction": {
                    "allowed_parent_direction_ids": ["required_parent"],
                    "trait_transformations": [],
                }
            },
            spec={"world": {"constraint_mode": "historical_reconstruction"}},
            evidence_pack={"status": "matched", "source_ids": ["src-3"]},
        )
        self.assertEqual(decision["verdict"], "revise")
        self.assertIn("HISTORICAL_PREREQUISITE_SKIPPED", decision["issue_codes"])

    def test_strong_historical_match_requires_external_and_internal_anchors(self) -> None:
        decision = review.deterministic_review(
            draft={
                "organism_name": "浅水伏行谱系",
                "traits": ["宽扁头部"],
                "lineage_summary": "它保留宽扁头部，在浅水里伏行。",
            },
            previous={"protected_traits": []},
            selection={"direction": {"trait_transformations": []}},
            spec={"world": {"constraint_mode": "historical_reconstruction"}},
            evidence_pack={
                "status": "matched",
                "historical_match_status": "historical_reference",
                "historical_required_external_traits": ["宽扁头部"],
                "historical_required_internal_traits": ["肋骨可支持躯干承重"],
                "source_ids": ["SRC-LAND-001"],
            },
        )

        self.assertEqual(decision["verdict"], "revise")
        self.assertIn("HISTORICAL_ANALOG_DIVERGENCE", decision["issue_codes"])

    def test_bounded_inference_cannot_claim_a_context_taxon_as_the_result(self) -> None:
        decision = review.deterministic_review(
            draft={
                "organism_name": "狄更逊水母",
                "traits": ["分节体表"],
                "lineage_summary": "这就是已经确认的狄更逊水母。",
            },
            previous={"protected_traits": []},
            selection={"direction": {"trait_transformations": []}},
            spec={"world": {"constraint_mode": "historical_reconstruction"}},
            evidence_pack={
                "status": "matched",
                "historical_match_status": "bounded_inference",
                "historical_candidate_names": ["Dickinsonia", "狄更逊水母"],
                "source_ids": ["SRC-EDI-001"],
            },
        )

        self.assertEqual(decision["verdict"], "revise")
        self.assertIn("UNSUPPORTED_TAXON_CLAIM", decision["issue_codes"])

    def test_partial_reference_cannot_be_renamed_as_the_generated_taxon(self) -> None:
        decision = review.deterministic_review(
            draft={
                "organism_name": "Archaeopteryx lithographica",
                "traits": ["带羽前肢"],
                "lineage_summary": "这里只保留了部分相似条件。",
            },
            previous={"protected_traits": []},
            selection={"direction": {"trait_transformations": []}},
            spec={"world": {"constraint_mode": "historical_reconstruction"}},
            evidence_pack={
                "status": "matched",
                "historical_match_status": "partial_reference",
                "historical_candidate_names": ["Archaeopteryx lithographica", "始祖鸟"],
                "source_ids": ["SRC-FEA-003"],
            },
        )

        self.assertEqual(decision["verdict"], "revise")
        self.assertIn("UNSUPPORTED_TAXON_CLAIM", decision["issue_codes"])

    def test_unverified_source_is_blocked_and_trace_is_allowlisted(self) -> None:
        decision = review.normalize_decision(
            {
                "verdict": "pass",
                "issue_codes": [],
                "summary": "证据足以支持这一小步。",
                "source_ids": ["invented-source"],
                "transition_ids": ["transition-1"],
                "pressure_ids": ["pressure-1"],
                "reasoning": "secret chain of thought",
                "api_key": "must-not-persist",
            },
            evidence_pack={"source_ids": [], "transition_ids": ["transition-1"], "pressure_ids": ["pressure-1"]},
        )
        self.assertEqual(decision["verdict"], "block")
        self.assertIn("UNVERIFIED_SOURCE", decision["issue_codes"])
        self.assertNotIn("reasoning", decision)
        self.assertNotIn("api_key", decision)


if __name__ == "__main__":
    unittest.main()
