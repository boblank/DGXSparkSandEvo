#!/usr/bin/env python3
"""Stateful, three-round EvoLab session engine.

The browser contract is intentionally smaller than the persisted session.  API
keys and absolute host paths never become part of either representation.
"""

from __future__ import annotations

import calendar
import copy
import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
CATALOG_FILE = SCRIPT_DIR / "interactive_catalog.json"
SCENARIO_REGISTRY_FILE = SCRIPT_DIR / "scenario_registry.json"
SCHEMA_FILE = SCRIPT_DIR / "interactive_schema.json"
KNOWLEDGE_CARDS_FILE = SCRIPT_DIR / "knowledge_cards.json"
CURATED_SOURCES_FILE = REPO_ROOT / "knowledge" / "sources.json"
KNOWLEDGE_ADAPTER_FILE = REPO_ROOT / "knowledge" / "knowledge_adapter.py"

STEP_ENDPOINT = "https://api.stepfun.com/step_plan/v1/chat/completions"
STEP_MODEL = "step-3.7-flash"
SESSION_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}-[a-f0-9]{8}$")
SAFE_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
LATIN_LETTER_PATTERN = re.compile(r"[A-Za-z]")
MIN_REFERENCE_CHANGE = 0.07
MAX_REFERENCE_CHANGE = 0.20


def configured_step_model() -> str:
    return os.environ.get("STEP_MODEL", STEP_MODEL).strip() or STEP_MODEL


def configured_step_endpoint() -> str:
    configured = os.environ.get("STEP_BASE_URL", "").strip().rstrip("/")
    if not configured:
        return STEP_ENDPOINT
    if configured.endswith("/chat/completions"):
        return configured
    return configured + "/chat/completions"


def _load_helper() -> Any:
    spec = importlib.util.spec_from_file_location(
        "evolution_interactive_shared_helper",
        SCRIPT_DIR / "evolution_helper.py",
    )
    if not spec or not spec.loader:
        raise RuntimeError("cannot load evolution helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = _load_helper()


def _load_rendering() -> Any:
    module_name = "evolution_interactive_rendering"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_DIR / "rendering.py")
    if not spec or not spec.loader:
        raise RuntimeError("cannot load evolution renderer profiles")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


rendering = _load_rendering()


def _load_scientific_review() -> Any:
    module_name = "evolution_interactive_scientific_review"
    spec = importlib.util.spec_from_file_location(
        module_name,
        SCRIPT_DIR / "scientific_review.py",
    )
    if not spec or not spec.loader:
        raise RuntimeError("cannot load scientific review module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


scientific_review = _load_scientific_review()


def _load_knowledge_adapter() -> Any:
    module_name = "evolution_interactive_knowledge_adapter"
    spec = importlib.util.spec_from_file_location(module_name, KNOWLEDGE_ADAPTER_FILE)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load historical taxon knowledge adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


knowledge_adapter = _load_knowledge_adapter()


def _load_visual_continuity() -> Any:
    module_name = "evolution_interactive_visual_continuity"
    spec = importlib.util.spec_from_file_location(
        module_name,
        SCRIPT_DIR / "visual_continuity.py",
    )
    if not spec or not spec.loader:
        raise RuntimeError("cannot load visual continuity module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


visual_continuity = _load_visual_continuity()


def _load_runtime_readiness() -> Any:
    module_name = "evolution_interactive_runtime_readiness"
    spec = importlib.util.spec_from_file_location(
        module_name,
        SCRIPT_DIR / "runtime_readiness.py",
    )
    if not spec or not spec.loader:
        raise RuntimeError("cannot load runtime readiness module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


runtime_readiness = _load_runtime_readiness()


def _load_video_generation() -> Any:
    module_name = "evolution_interactive_video_generation"
    spec = importlib.util.spec_from_file_location(
        module_name, SCRIPT_DIR / "video_generation.py"
    )
    if not spec or not spec.loader:
        raise RuntimeError("cannot load ComfyUI image uploader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


video_generation = _load_video_generation()


class InteractiveError(RuntimeError):
    """A safe error that can cross the HTTP boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 400,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.http_status = http_status
        self.retryable = retryable


Planner = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any]],
    tuple[dict[str, Any], dict[str, Any]],
]
Renderer = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], Path],
    dict[str, Any],
]
OptionPlanner = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], Optional[dict[str, Any]]],
    tuple[dict[str, Any], dict[str, Any]],
]
Reviewer = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    tuple[dict[str, Any], dict[str, Any]],
]
VisualReviewer = Callable[
    [Path, Path, list[str], list[str]],
    tuple[dict[str, Any], dict[str, Any]],
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_natural_chinese(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    chinese_count = len(CHINESE_PATTERN.findall(value))
    latin_count = len(LATIN_LETTER_PATTERN.findall(value))
    return chinese_count > 0 and latin_count <= max(12, chinese_count)


def _as_choice_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _public_source(source: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(source.get(key, ""))
        for key in ("source_id", "title", "url", "supports", "boundary")
    }


def _visual_change_score(parent: Path, descendant: Path) -> float:
    """Return normalized pixel RMS for a same-scene reference edit."""
    from PIL import Image, ImageChops, ImageStat

    with Image.open(parent) as parent_source, Image.open(descendant) as child_source:
        parent_image = parent_source.convert("RGB").resize((256, 256))
        child_image = child_source.convert("RGB").resize((256, 256))
    difference = ImageChops.difference(parent_image, child_image)
    channel_rms = ImageStat.Stat(difference).rms
    return round((sum(value * value for value in channel_rms) / 3) ** 0.5 / 255, 4)


class InteractiveEvolutionService:
    """Create, advance, and persist three-round evolution sessions."""

    def __init__(
        self,
        data_root: Path,
        *,
        dry_run: bool = False,
        planner: Planner | None = None,
        renderer: Renderer | None = None,
        option_planner: OptionPlanner | None = None,
        reviewer: Reviewer | None = None,
        review_mode: str | None = None,
        visual_reviewer: VisualReviewer | None = None,
        visual_review_mode: str | None = None,
        step_timeout: int = 240,
        comfy_timeout: int = 900,
    ) -> None:
        self.data_root = data_root.expanduser().resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.dry_run = dry_run
        self.registry = _read_json(SCENARIO_REGISTRY_FILE)
        self._scenario_defs = {
            item["id"]: item for item in self.registry.get("scenarios", [])
        }
        self.default_scenario_id = str(self.registry["default_scenario_id"])
        if self.default_scenario_id not in self._scenario_defs:
            raise RuntimeError("default scenario is missing from registry")
        self._catalogs: dict[str, dict[str, Any]] = {}
        for scenario_id, scenario in self._scenario_defs.items():
            filename = str(scenario.get("catalog_file", ""))
            if not re.fullmatch(r"[a-z0-9_]+\.json", filename):
                raise RuntimeError(f"unsafe scenario catalog: {filename}")
            self._catalogs[scenario_id] = _read_json(SCRIPT_DIR / filename)
        # Kept as the default catalog for compatibility with existing helper tests.
        self.catalog = self._catalogs[self.default_scenario_id]
        self.schema = _read_json(SCHEMA_FILE)
        self.step_timeout = step_timeout
        self.comfy_timeout = comfy_timeout
        self.generation_lease_seconds = max(300, step_timeout + comfy_timeout + 120)
        self._planner = planner or self._plan_with_step
        self._renderer = renderer or self._render_with_comfy
        self._option_planner = option_planner or (
            self._fixture_option_plan if dry_run else self._plan_options_with_step
        )
        self.review_mode = (review_mode or os.environ.get("EVOLAB_REVIEW_MODE", "optional")).strip().lower()
        if self.review_mode not in {"off", "optional", "required"}:
            raise ValueError("EVOLAB_REVIEW_MODE must be off, optional, or required")
        self._reviewer = reviewer or (None if dry_run else self._review_with_step)
        self.visual_review_mode = (
            visual_review_mode
            or os.environ.get("EVOLAB_VISUAL_REVIEW_MODE", "optional")
        ).strip().lower()
        if self.visual_review_mode not in {"off", "optional", "required"}:
            raise ValueError("EVOLAB_VISUAL_REVIEW_MODE must be off, optional, or required")
        self._visual_reviewer = visual_reviewer or (
            None if dry_run else self._review_visual_with_step
        )
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._cards, self._sources = self._load_knowledge()
        self._readiness_cache: tuple[float, dict[str, Any]] | None = None

    @property
    def contract_version(self) -> str:
        return str(self.registry["contract_version"])

    @staticmethod
    def _public_scenario(scenario: dict[str, Any]) -> dict[str, str]:
        return {
            key: str(scenario.get(key, ""))
            for key in (
                "id",
                "title",
                "short_title",
                "era",
                "habitat",
                "summary",
                "entry_question",
                "evidence_note",
                "constraint_mode",
                "accent",
                "depth",
                "origin_asset",
            )
        }

    def list_scenarios(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "default_scenario_id": self.default_scenario_id,
            "scenarios": [
                self._public_scenario(scenario)
                for scenario in self.registry.get("scenarios", [])
            ],
        }

    def readiness(self, *, force: bool = False) -> dict[str, Any]:
        if (
            not force
            and self._readiness_cache is not None
            and time.monotonic() - self._readiness_cache[0] < 30
        ):
            return copy.deepcopy(self._readiness_cache[1])
        primary = os.environ.get("EVOLAB_IMAGE_RENDERER", "flux1")
        fallback = os.environ.get("EVOLAB_IMAGE_FALLBACK", "flux1")
        renderer_ids = rendering.renderer_chain(primary, fallback)
        result = runtime_readiness.probe_runtime(
            dry_run=self.dry_run,
            step_endpoint=configured_step_endpoint(),
            step_model=configured_step_model(),
            step_key=os.environ.get("STEP_API_KEY") or os.environ.get("STEPFUN_API_KEY"),
            comfy_url=os.environ.get("COMFYUI_URL", "http://127.0.0.1:7000"),
            renderer_ids=renderer_ids,
            renderer_catalog=rendering.renderer_catalog(),
            workflow_root=SCRIPT_DIR,
            get_json=helper.get_json,
            post_json=helper.post_json,
            timeout=min(15, self.step_timeout),
        )
        self._readiness_cache = (time.monotonic(), copy.deepcopy(result))
        return result

    def _scenario(self, scenario_id: str | None = None) -> dict[str, Any]:
        candidate = scenario_id or self.default_scenario_id
        if not isinstance(candidate, str) or not SAFE_ID_PATTERN.fullmatch(candidate):
            raise InteractiveError("invalid_scenario", "没有找到这个世界，请回到图谱重新选择。")
        scenario = self._scenario_defs.get(candidate)
        if not scenario:
            raise InteractiveError("invalid_scenario", "没有找到这个世界，请回到图谱重新选择。")
        return scenario

    def _catalog_for(self, scenario_id: str | None = None) -> dict[str, Any]:
        return self._catalogs[self._scenario(scenario_id)["id"]]

    def _load_knowledge(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        cards: dict[str, dict[str, Any]] = {}
        sources: list[dict[str, Any]] = []
        if KNOWLEDGE_CARDS_FILE.is_file():
            payload = _read_json(KNOWLEDGE_CARDS_FILE)
            for collection in ("cards", "interactive_cards"):
                for card in payload.get(collection, []):
                    if isinstance(card, dict) and card.get("knowledge_card_id"):
                        cards[card["knowledge_card_id"]] = card
            sources.extend(payload.get("sources", []))
        if CURATED_SOURCES_FILE.is_file():
            payload = _read_json(CURATED_SOURCES_FILE)
            sources.extend(payload.get("sources", []))
        deduplicated: dict[str, dict[str, Any]] = {}
        for source in sources:
            source_id = source.get("source_id")
            if isinstance(source_id, str):
                deduplicated[source_id] = source
        return cards, list(deduplicated.values())

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def _session_dir(self, session_id: str) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise InteractiveError("session_not_found", "没有找到这段演化记录。", http_status=404)
        return self.data_root / session_id

    def _load_session(self, session_id: str) -> dict[str, Any]:
        manifest = self._session_dir(session_id) / "session.json"
        if not manifest.is_file():
            raise InteractiveError("session_not_found", "没有找到这段演化记录。", http_status=404)
        try:
            session = _read_json(manifest)
        except (OSError, json.JSONDecodeError) as exc:
            raise InteractiveError(
                "session_unavailable",
                "这段演化记录暂时无法读取。",
                http_status=500,
                retryable=True,
            ) from exc
        if session.get("session_id") != session_id:
            raise InteractiveError("session_unavailable", "这段演化记录不完整。", http_status=500)
        # Sessions created before the open-world registry belong to the original tidal scene.
        session.setdefault("scenario_id", self.default_scenario_id)
        return session

    def _write_session(self, session: dict[str, Any]) -> None:
        _atomic_write_json(
            self._session_dir(session["session_id"]) / "session.json",
            session,
        )

    def _generation_is_stale(self, session: dict[str, Any]) -> bool:
        try:
            started_at = calendar.timegm(
                time.strptime(
                    str(session.get("updated_at", "")),
                    "%Y-%m-%dT%H:%M:%SZ",
                )
            )
        except (TypeError, ValueError):
            return True
        return time.time() - started_at > self.generation_lease_seconds

    def _public_stage(self, source: dict[str, Any]) -> dict[str, Any]:
        stage = copy.deepcopy(source)
        selection = stage.get("selection") if isinstance(stage.get("selection"), dict) else {}
        environment = selection.get("environment") if isinstance(selection.get("environment"), dict) else {}
        contingency = selection.get("contingency") if isinstance(selection.get("contingency"), dict) else {}
        direction = selection.get("direction") if isinstance(selection.get("direction"), dict) else {}
        direction_title = str(direction.get("title") or "").strip()
        direction_detail = str(direction.get("description") or "").strip()
        environment_title = str(environment.get("title") or "").strip()
        contingency_title = str(contingency.get("title") or "").strip()
        chemistry = stage.get("scenario_id") == "hydrothermal_origin"

        if not _is_natural_chinese(stage.get("organism_name")):
            if direction_title:
                stage["organism_name"] = (
                    f"沿“{direction_title}”继续的反应系统"
                    if chemistry
                    else f"沿“{direction_title}”延续的谱系"
                )
            else:
                stage["organism_name"] = "尚未命名的化学系统" if chemistry else "尚未命名的谱系"
        if not _is_natural_chinese(stage.get("lineage_summary")):
            if chemistry:
                stage["lineage_summary"] = (
                    f"周围变成“{environment_title or '新的环境'}”，“{contingency_title or '一次偶然变化'}”"
                    f"又扰动了原有反应。系统沿“{direction_title or '当前方向'}”继续，但这还不能证明生命已经出现。"
                )
            else:
                stage["lineage_summary"] = (
                    f"周围变成“{environment_title or '新的环境'}”，“{contingency_title or '一次偶然变化'}”"
                    f"又把它推离原路。这条谱系沿“{direction_title or '当前方向'}”继续，没有凭空跳成一个新物种。"
                )
        if not _is_natural_chinese(stage.get("change_summary")):
            stage["change_summary"] = direction_detail or "这一阶段保留了来路，也增加了新的代价。"

        def localized_list(field: str, fallbacks: list[str]) -> None:
            original = stage.get(field)
            if not isinstance(original, list) and not fallbacks:
                return
            values = original if isinstance(original, list) else []
            chinese_values = [str(item).strip() for item in values if _is_natural_chinese(item)]
            if not chinese_values:
                for fallback in fallbacks:
                    if _is_natural_chinese(fallback) and fallback not in chinese_values:
                        chinese_values.append(fallback)
            stage[field] = chinese_values[:6]

        localized_list("traits", [direction_title, environment_title])
        localized_list("inherited_traits", [])
        localized_list("protected_traits", [])
        localized_list("internal_causes", [direction_detail or direction_title])
        localized_list("external_causes", [environment_title, contingency_title])
        localized_list("benefits", ["更容易应对当前环境的压力"])
        localized_list("costs", ["这种变化也会增加维持成本，并可能失去原有优势"])
        if not _is_natural_chinese(stage.get("uncertainty_note")):
            stage["uncertainty_note"] = "这是受约束的情景推演，不是已经发现的物种，也不是确定预测。"
        parent = stage.get("lineage_parent")
        if isinstance(parent, dict):
            if not _is_natural_chinese(parent.get("organism_name")):
                parent["organism_name"] = "上一阶段"
            if not _is_natural_chinese(parent.get("lineage_summary")):
                parent["lineage_summary"] = "上一阶段的结构和生活方式为这次改变提供了起点。"
        return stage

    def _knowledge_match(
        self,
        knowledge_card_id: str,
        transition_id: str,
        *,
        match_scope: str = "transition",
    ) -> dict[str, Any]:
        card = self._cards.get(knowledge_card_id)
        card_sources = [
            source
            for source in self._sources
            if knowledge_card_id in source.get("knowledge_card_ids", [])
            or source.get("source_id") in (card or {}).get("source_ids", [])
        ]
        transition_sources = [
            source
            for source in self._sources
            if transition_id in source.get("transition_ids", [])
        ]
        if card:
            transition_card_sources = [
                source
                for source in card_sources
                if transition_id in source.get("transition_ids", [])
            ]
            return {
                "status": "matched",
                "match_scope": match_scope,
                "knowledge_card_id": knowledge_card_id,
                "title": card["title"],
                "summary": card["body"],
                "boundary": card.get("boundary", "这是机制解释，不是对生成结果的实证认定。"),
                "sources": [
                    _public_source(source)
                    for source in (transition_card_sources or card_sources)
                ],
                "context_sources": [],
                "generated_outcome_status": (
                    "no_match" if match_scope == "external_pressure" else "mechanism_match"
                ),
                "generated_outcome_reason": (
                    "知识卡支持的是外部压力和可能的选择方向，不证明图中的未来形态已经存在，也不证明它必然出现。"
                    if match_scope == "external_pressure"
                    else "知识卡解释这一类演化机制；生成形态仍是教学性重建。"
                ),
            }
        return {
            "status": "no_match",
            "match_scope": match_scope,
            "knowledge_card_id": "NONE",
            "title": "这条路没有现成的历史节点",
            "summary": (
                "知识库没有命中能与这次形态变化一一对应的已知演化事件。"
                "下面展示的是按环境压力、已有差异和代价推出来的情景，不是已经观察到的历史结果。"
            ),
            "boundary": "只把它当作受约束的演化假说，不当作确定预测。",
            "sources": [],
            "context_sources": [_public_source(source) for source in transition_sources],
            "generated_outcome_status": "no_match",
            "generated_outcome_reason": "知识库没有与这次形态变化一一对应的已知节点。",
        }

    def _historical_reference(
        self,
        selection: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        constraint_mode = str(
            (spec.get("world") or {}).get(
                "constraint_mode", "historical_reconstruction"
            )
        )
        if constraint_mode != "historical_reconstruction":
            return {
                "status": "not_applicable",
                "query": {},
                "candidates": [],
                "required_external_traits": [],
                "required_internal_traits": [],
                "source_ids": [],
                "message": "这一轮不是历史形态重建，不套用古生物类群模板。",
            }
        return knowledge_adapter.match_historical_taxa(
            scenario_id=str((spec.get("world") or {}).get("id", "")),
            transition_id=str(selection["direction"].get("transition_id", "")),
            direction_id=str(selection["direction"].get("id", "")),
            environment_id=str(selection["environment"].get("id", "")),
        )

    @staticmethod
    def _public_historical_reference(reference: dict[str, Any]) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for candidate in reference.get("candidates", [])[:3]:
            if not isinstance(candidate, dict):
                continue
            candidates.append(
                {
                    key: copy.deepcopy(candidate[key])
                    for key in (
                        "taxon_id",
                        "scientific_name",
                        "display_name",
                        "age_ma",
                        "external_traits",
                        "internal_traits",
                        "boundary",
                        "source_ids",
                        "score",
                    )
                    if key in candidate
                }
            )
        return {
            "status": str(reference.get("status", "bounded_inference")),
            "query": copy.deepcopy(reference.get("query", {})),
            "candidates": candidates,
            "required_external_traits": [
                str(item)
                for item in reference.get("required_external_traits", [])
                if str(item).strip()
            ],
            "required_internal_traits": [
                str(item)
                for item in reference.get("required_internal_traits", [])
                if str(item).strip()
            ],
            "source_ids": [
                str(item)
                for item in reference.get("source_ids", [])
                if str(item).strip()
            ],
            "message": str(reference.get("message", "")),
        }

    def _round_spec(
        self,
        round_no: int,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            spec = copy.deepcopy(self._catalog_for(scenario_id)["rounds"][str(round_no)])
            scenario = self._scenario(scenario_id)
            scenario_mode = scenario.get("constraint_mode", "historical_reconstruction")
            effective_mode = spec.get("constraint_mode")
            if not effective_mode:
                effective_mode = (
                    "future_scenario"
                    if spec.get("time_scope") == "FUTURE_SCENARIO"
                    else "historical_reconstruction"
                    if scenario_mode == "mixed_evidence"
                    else scenario_mode
                )
            spec["world"] = {
                "id": scenario["id"],
                "title": scenario["title"],
                "era": scenario["era"],
                "habitat": scenario["habitat"],
                "summary": scenario["summary"],
                "constraint_mode": effective_mode,
                "visual_anchor": scenario.get("visual_anchor", ""),
                "visual_negative": scenario.get("visual_negative")
                or scenario.get("negative_prompt", ""),
            }
            return spec
        except KeyError as exc:
            raise InteractiveError("session_completed", "这条路线已经走完三轮。", http_status=409) from exc

    def _effective_round_spec(
        self,
        session: dict[str, Any],
        round_no: int,
    ) -> dict[str, Any]:
        """Filter choices that would contradict the branch already taken."""
        spec = self._round_spec(round_no, session.get("scenario_id"))
        parent_selection = (session.get("current_stage") or {}).get("selection") or {}
        parent_direction_id = (parent_selection.get("direction") or {}).get("id")
        if not parent_direction_id:
            return spec
        protected_traits = {
            str(item)
            for item in (session.get("current_stage") or {}).get("protected_traits", [])
            if str(item).strip()
        }

        def direction_is_compatible(direction: dict[str, Any]) -> bool:
            allowed_parents = direction.get("allowed_parent_direction_ids", [])
            if allowed_parents and parent_direction_id not in allowed_parents:
                return False
            required_all = {
                str(item)
                for item in direction.get("required_protected_traits", [])
                if str(item).strip()
            }
            if not required_all.issubset(protected_traits):
                return False
            required_any = {
                str(item)
                for item in direction.get("required_any_protected_traits", [])
                if str(item).strip()
            }
            return not required_any or bool(required_any & protected_traits)

        spec["directions"] = [
            direction
            for direction in spec["directions"]
            if direction_is_compatible(direction)
        ]
        return spec

    def _choices_for(self, session: dict[str, Any]) -> dict[str, Any]:
        next_round = int(session["round_index"]) + 1
        max_rounds = int(session.get("max_rounds", 3))
        if next_round > max_rounds:
            return {
                "round": None,
                "chapter": "演化仍会继续",
                "environments": [],
                "contingencies": [],
                "directions": [],
            }
        spec = self._effective_round_spec(session, next_round)
        return {
            "round": next_round,
            "chapter": spec["chapter"],
            "environments": copy.deepcopy(spec["environments"]),
            "contingencies": copy.deepcopy(spec["contingencies"]),
            "directions": [
                {
                    key: copy.deepcopy(direction[key])
                    for key in (
                        "id",
                        "title",
                        "description",
                        "mechanism_hint",
                        "tradeoff_hint",
                    )
                }
                for direction in spec["directions"]
            ],
        }

    @staticmethod
    def _choice_with_reason(
        item: dict[str, Any],
        reason: str,
        *,
        direction: bool = False,
    ) -> dict[str, Any]:
        keys = (
            ("id", "title", "description", "mechanism_hint", "tradeoff_hint")
            if direction
            else ("id", "title", "description")
        )
        public = {key: copy.deepcopy(item[key]) for key in keys if key in item}
        public["context_reason"] = reason
        return public

    def contextualize_choices(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-rank audited candidates after an upstream choice changes."""
        allowed = {"expected_round", "environment_id", "contingency_id"}
        if set(payload) - allowed or not {"expected_round", "environment_id"}.issubset(payload):
            raise InteractiveError(
                "invalid_request",
                "请先选定当前环境，再查看它会怎样改变后面的选项。",
            )
        try:
            expected_round = int(payload["expected_round"])
        except (TypeError, ValueError) as exc:
            raise InteractiveError("invalid_request", "轮次信息不正确，请刷新后重试。") from exc

        with self._session_lock(session_id):
            session = self._load_session(session_id)
            next_round = int(session["round_index"]) + 1
            if expected_round != next_round:
                raise InteractiveError(
                    "round_conflict",
                    "这段记录已经向前走了一步，请刷新后再选。",
                    http_status=409,
                )
            if next_round > int(session.get("max_rounds", 3)):
                raise InteractiveError(
                    "session_completed",
                    "这条路线已经走完三轮。",
                    http_status=409,
                )
            spec = self._effective_round_spec(session, next_round)
            environment_id = payload.get("environment_id")
            environment = _as_choice_map(spec["environments"]).get(environment_id)
            if not environment:
                raise InteractiveError(
                    "invalid_choice",
                    "这个环境不属于当前阶段，请重新选择。",
                    http_status=409,
                )
            contingency = None
            contingency_id = payload.get("contingency_id")
            if contingency_id is not None:
                contingency = _as_choice_map(spec["contingencies"]).get(contingency_id)
                if not contingency:
                    raise InteractiveError(
                        "invalid_choice",
                        "这个偶然事件不属于当前阶段，请重新选择。",
                        http_status=409,
                    )
            previous = copy.deepcopy(session["current_stage"])

        plan, metadata = self._option_planner(
            previous,
            copy.deepcopy(spec),
            copy.deepcopy(environment),
            copy.deepcopy(contingency),
        )
        contingency_map = _as_choice_map(spec["contingencies"])
        direction_map = _as_choice_map(spec["directions"])

        def resolve(
            planned: Any,
            candidates: dict[str, dict[str, Any]],
            *,
            direction: bool,
        ) -> list[dict[str, Any]]:
            if not isinstance(planned, list):
                raise InteractiveError(
                    "planner_invalid_output",
                    "候选重算没有通过校验，请重新选择当前环境。",
                    http_status=502,
                    retryable=True,
                )
            resolved: list[dict[str, Any]] = []
            seen: set[str] = set()
            for choice in planned:
                choice_id = choice.get("id") if isinstance(choice, dict) else None
                reason = choice.get("reason") if isinstance(choice, dict) else None
                if (
                    not isinstance(choice_id, str)
                    or choice_id in seen
                    or choice_id not in candidates
                    or not _is_natural_chinese(reason)
                ):
                    raise InteractiveError(
                        "planner_invalid_output",
                        "候选重算没有通过校验，请重新选择当前环境。",
                        http_status=502,
                        retryable=True,
                    )
                seen.add(choice_id)
                resolved.append(
                    self._choice_with_reason(
                        candidates[choice_id],
                        str(reason).strip(),
                        direction=direction,
                    )
                )
            if not resolved:
                raise InteractiveError(
                    "planner_invalid_output",
                    "当前条件下没有得到可用候选，请换一个环境。",
                    http_status=502,
                    retryable=True,
                )
            return resolved

        resolved_contingencies = resolve(
            plan.get("contingencies") if isinstance(plan, dict) else None,
            contingency_map,
            direction=False,
        )
        resolved_directions = resolve(
            plan.get("directions") if isinstance(plan, dict) else None,
            direction_map,
            direction=True,
        )
        response = {
            "round": next_round,
            "chapter": spec["chapter"],
            "environment": self._choice_with_reason(
                environment,
                "后续候选正在按这个环境的选择压力重新排序。",
            ),
            "contingency": (
                self._choice_with_reason(
                    contingency,
                    "演化方向也会把这次偶然变化的后果算进去。",
                )
                if contingency
                else None
            ),
            "contingencies": resolved_contingencies,
            "directions": resolved_directions,
            "contextualized": True,
            "model": {
                "planner": str(metadata.get("planner", "unknown")),
                "reasoning_effort": str(metadata.get("reasoning_effort", "not_applicable")),
                "strict_schema": bool(metadata.get("strict_schema", False)),
            },
        }
        with self._session_lock(session_id):
            current = self._load_session(session_id)
            if int(current["round_index"]) + 1 != next_round:
                raise InteractiveError(
                    "round_conflict",
                    "这段记录已经向前走了一步，请刷新后再选。",
                    http_status=409,
                )
            current["choice_context"] = {
                "round": next_round,
                "environment_id": environment["id"],
                "contingency_id": contingency["id"] if contingency else None,
                "environment_reason": response["environment"]["context_reason"],
                "contingency_reasons": {
                    item["id"]: item["context_reason"] for item in resolved_contingencies
                },
                "direction_reasons": {
                    item["id"]: item["context_reason"] for item in resolved_directions
                },
                "model": copy.deepcopy(response["model"]),
            }
            self._write_session(current)
        return response

    def public_envelope(self, session: dict[str, Any]) -> dict[str, Any]:
        public_session = {
            key: copy.deepcopy(session[key])
            for key in (
                "contract_version",
                "session_id",
                "scenario_id",
                "status",
                "round_index",
                "max_rounds",
                "current_stage",
                "history",
                "last_error",
                "created_at",
                "updated_at",
            )
        }
        public_session["scenario"] = self._public_scenario(
            self._scenario(session.get("scenario_id"))
        )
        public_session["current_stage"] = self._public_stage(session["current_stage"])
        public_session["history"] = [
            self._public_stage(stage)
            for stage in session.get("history", [])
            if isinstance(stage, dict)
        ]
        public_session["lineage_video_url"] = (
            f"/api/sessions/{session['session_id']}/lineage-video"
        )
        return {
            "session": public_session,
            "choices": self._choices_for(session),
        }

    def lineage_video_path(self, session_id: str) -> Path:
        with self._session_lock(session_id):
            session = self._load_session(session_id)
            if session.get("status") != "completed":
                raise InteractiveError(
                    "video_not_ready",
                    "三次改变完成后，才能整理这条路线的回放。",
                    http_status=409,
                    retryable=True,
                )
            session_dir = self._session_dir(session_id).resolve()
            flf_root = session_dir / "lineage_flf"
            flf_output = flf_root / "lineage_flf_complete.mp4"
            flf_manifest = flf_root / "lineage_flf_validation.json"
            if flf_output.is_file() and flf_manifest.is_file():
                try:
                    flf_evidence = _read_json(flf_manifest)
                except (OSError, json.JSONDecodeError):
                    flf_evidence = {}
                flf_session = (
                    flf_evidence.get("session")
                    if isinstance(flf_evidence.get("session"), dict)
                    else {}
                )
                flf_merged = (
                    flf_evidence.get("merged")
                    if isinstance(flf_evidence.get("merged"), dict)
                    else {}
                )
                try:
                    expected_stage_hashes = [
                        {
                            "round": int(stage.get("round", index)),
                            "sha256": _sha256_file(
                                session_dir / Path(str(stage.get("image_url", ""))).name
                            ),
                        }
                        for index, stage in enumerate(session.get("history", []))
                    ]
                except OSError:
                    expected_stage_hashes = []
                if (
                    flf_evidence.get("contract_version") == "1.0.0"
                    and flf_evidence.get("passed") is True
                    and flf_session.get("session_id") == session.get("session_id")
                    and flf_session.get("updated_at") == session.get("updated_at")
                    and flf_session.get("stage_sha256") == expected_stage_hashes
                    and flf_merged.get("passed") is True
                    and flf_output.stat().st_size >= 100_000
                    and flf_merged.get("sha256") == _sha256_file(flf_output)
                ):
                    return flf_output
            output = session_dir / "lineage_recap.mp4"
            manifest = output.with_suffix(".json")
            if output.is_file() and manifest.is_file():
                try:
                    evidence = _read_json(manifest)
                except (OSError, json.JSONDecodeError):
                    evidence = {}
                output_evidence = (
                    evidence.get("output")
                    if isinstance(evidence.get("output"), dict)
                    else {}
                )
                if (
                    evidence.get("contract_version") == "1.2.0"
                    and evidence.get("session_updated_at") == session.get("updated_at")
                    and evidence.get("input_stage_count", 0) >= 4
                    and output.stat().st_size >= 50_000
                    and output_evidence.get("sha256") == _sha256_file(output)
                ):
                    return output

            default_video_python = Path(
                "/home/Developer/build_a_claw_workshop-bundle/"
                "comfyui-app/comfyui-env/bin/python"
            )
            configured = os.environ.get("EVOLAB_VIDEO_PYTHON", "").strip()
            executable = Path(configured).expanduser() if configured else default_video_python
            if not executable.is_file():
                executable = Path(sys.executable)
            command = [
                str(executable),
                str(SCRIPT_DIR / "lineage_video.py"),
                "--session",
                str(session_dir / "session.json"),
                "--output",
                str(output),
            ]
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=int(os.environ.get("EVOLAB_VIDEO_TIMEOUT", "180")),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise InteractiveError(
                    "video_generation_failed",
                    "回放没有生成出来，四张阶段图仍然保留。",
                    http_status=502,
                    retryable=True,
                ) from exc
            if result.returncode != 0 or not output.is_file() or output.stat().st_size < 50_000:
                helper.log(
                    "Lineage recap failed: "
                    + (result.stderr or result.stdout or "unknown encoder error")[-800:]
                )
                raise InteractiveError(
                    "video_generation_failed",
                    "回放没有生成出来，四张阶段图仍然保留。",
                    http_status=502,
                    retryable=True,
                )
            return output

    def create_session(self, scenario_id: str | None = None) -> dict[str, Any]:
        scenario = self._scenario(scenario_id)
        scenario_id = scenario["id"]
        catalog = self._catalog_for(scenario_id)
        session_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        session_dir = self.data_root / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        initial = copy.deepcopy(catalog["initial_stage"])
        if scenario.get("constraint_mode") in {"historical_reconstruction", "mixed_evidence"}:
            initial["protected_traits"] = list(
                dict.fromkeys(initial.get("protected_traits") or initial.get("traits", [])[:2])
            )
        else:
            initial.setdefault("protected_traits", [])
        origin_relative = Path(str(scenario.get("origin_asset", "")))
        origin_file = (REPO_ROOT / origin_relative).resolve()
        if REPO_ROOT not in origin_file.parents or origin_file.suffix.lower() not in {".png", ".svg"}:
            raise RuntimeError("unsafe origin asset path")
        if origin_file.is_file():
            filename = "stage_00_origin" + origin_file.suffix.lower()
            shutil.copy2(origin_file, session_dir / filename)
            origin_source = "curated_origin"
        else:
            filename = "stage_00_origin.svg"
            self._write_stage_svg(
                session_dir / filename,
                0,
                initial["organism_name"],
                "origin",
                scenario_id,
            )
            origin_source = "illustrated_start"
        initial.update(
            {
                "round": 0,
                "scenario_id": scenario_id,
                "image_url": f"/api/assets/{session_id}/{filename}",
                "render_source": origin_source,
                "selection": None,
                "knowledge_match": self._knowledge_match(
                    initial.get("knowledge_card_id", "NONE"),
                    initial["transition_id"],
                ),
            }
        )
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        session = {
            "contract_version": self.contract_version,
            "session_id": session_id,
            "scenario_id": scenario_id,
            "status": "ready",
            "round_index": 0,
            "max_rounds": len(catalog["rounds"]),
            "current_stage": initial,
            "history": [initial],
            "review_trace": [],
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }
        self._write_session(session)
        return self.public_envelope(session)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.public_envelope(self._load_session(session_id))

    def _validate_selection(
        self,
        spec: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        required = {"environment_id", "contingency_id", "direction_id", "expected_round"}
        if set(payload) != required:
            raise InteractiveError(
                "invalid_request",
                "请各选一个环境、偶发事件和演化方向，再继续。",
            )
        try:
            expected_round = int(payload["expected_round"])
        except (TypeError, ValueError) as exc:
            raise InteractiveError("invalid_request", "轮次信息不正确，请刷新后重试。") from exc
        selection: dict[str, Any] = {"expected_round": expected_round}
        for payload_key, catalog_key in (
            ("environment_id", "environments"),
            ("contingency_id", "contingencies"),
            ("direction_id", "directions"),
        ):
            value = payload.get(payload_key)
            if not isinstance(value, str) or not SAFE_ID_PATTERN.fullmatch(value):
                raise InteractiveError("invalid_request", "有一个选择无法识别，请重新选择。")
            item = _as_choice_map(spec[catalog_key]).get(value)
            if not item:
                raise InteractiveError("invalid_choice", "这个选项不属于当前阶段，请重新选择。", http_status=409)
            selection[payload_key.removesuffix("_id")] = copy.deepcopy(item)
        return selection

    def evolve(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._session_lock(session_id):
            session = self._load_session(session_id)
            next_round = int(session["round_index"]) + 1
            max_rounds = int(session.get("max_rounds", 3))
            if next_round > max_rounds:
                raise InteractiveError("session_completed", "这条路线已经走完三轮。", http_status=409)
            if session["status"] == "generating":
                if not self._generation_is_stale(session):
                    raise InteractiveError(
                        "session_busy",
                        "这一轮还在生成，请稍等片刻。",
                        http_status=409,
                        retryable=True,
                    )
                stale_updated_at = str(session.get("updated_at", ""))
                recovered_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                session["status"] = "ready"
                session["last_error"] = None
                session["generation_recovery"] = {
                    "stale_updated_at": stale_updated_at,
                    "recovered_at": recovered_at,
                    "lease_seconds": self.generation_lease_seconds,
                }
                session["updated_at"] = recovered_at
                self._write_session(session)
            spec = self._effective_round_spec(session, next_round)
            selection = self._validate_selection(spec, payload)
            if selection["expected_round"] != next_round:
                raise InteractiveError(
                    "round_conflict",
                    "这段记录已经向前走了一步，请刷新后再选。",
                    http_status=409,
                )
            choice_context = session.get("choice_context")
            if (
                isinstance(choice_context, dict)
                and choice_context.get("round") == next_round
                and choice_context.get("environment_id") == selection["environment"]["id"]
                and choice_context.get("contingency_id") == selection["contingency"]["id"]
            ):
                contingency_reason = choice_context.get("contingency_reasons", {}).get(
                    selection["contingency"]["id"]
                )
                direction_reason = choice_context.get("direction_reasons", {}).get(
                    selection["direction"]["id"]
                )
                if not contingency_reason or not direction_reason:
                    raise InteractiveError(
                        "choice_context_conflict",
                        "这组条件已经改变，请按当前环境重新选择。",
                        http_status=409,
                    )
                selection["environment"]["context_reason"] = choice_context.get(
                    "environment_reason", ""
                )
                selection["contingency"]["context_reason"] = contingency_reason
                selection["direction"]["context_reason"] = direction_reason
                selection["context_model"] = copy.deepcopy(
                    choice_context.get("model", {})
                )

            # Resolve the evidence-backed body-plan reference before planning.
            # The planner and the independent review gate receive the same snapshot.
            spec["historical_reference"] = self._historical_reference(selection, spec)
            selection["historical_reference"] = copy.deepcopy(
                spec["historical_reference"]
            )

            session["status"] = "generating"
            session["last_error"] = None
            session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._write_session(session)
            previous = copy.deepcopy(session["current_stage"])
            review_records: list[dict[str, Any]] = []
            try:
                if self.dry_run:
                    result, metadata = self._dry_run_plan(previous, selection, spec)
                else:
                    result, metadata = self._planner(previous, selection, spec)
                result, metadata, review_summary = self._review_plan(
                    result,
                    metadata,
                    previous,
                    selection,
                    spec,
                    review_records,
                )
                # The scientific verdict is durable before the GPU call starts.
                # A renderer crash must not erase which evidence gate released it.
                session.setdefault("review_trace", []).extend(review_records)
                self._write_session(session)
                review_records.clear()
                stage = self._build_stage(
                    next_round,
                    previous,
                    result,
                    selection,
                    spec,
                    metadata,
                )
                stage["review_summary"] = review_summary
                destination = self._session_dir(session_id) / f"stage_{next_round:02d}.png"
                if self.dry_run:
                    destination = destination.with_suffix(".svg")
                    render_metadata = self._dry_run_render(result, previous, selection, destination)
                else:
                    render_metadata = self._renderer(result, previous, selection, destination)
                stage["image_url"] = f"/api/assets/{session_id}/{destination.name}"
                stage["render_source"] = render_metadata["render_source"]
                stage["render_metadata"] = {
                    key: render_metadata.get(key)
                    for key in (
                        "generator",
                        "renderer",
                        "seed",
                        "duration_seconds",
                        "fallback_from",
                        "reference_conditioning",
                        "required_visual_change",
                        "visual_forbidden",
                        "visual_change_score",
                        "technical_visual_gate",
                        "visual_review",
                    )
                    if render_metadata.get(key) is not None
                }
            except InteractiveError as exc:
                session.setdefault("review_trace", []).extend(review_records)
                private_details = getattr(exc, "private_details", None)
                if isinstance(private_details, dict):
                    session.setdefault("visual_review_trace", []).append(
                        copy.deepcopy(private_details)
                    )
                session["status"] = "error"
                session["last_error"] = {
                    "code": exc.code,
                    "message": exc.public_message,
                    "retryable": exc.retryable,
                }
                session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._write_session(session)
                raise
            except Exception as exc:
                session.setdefault("review_trace", []).extend(review_records)
                session["status"] = "error"
                session["last_error"] = {
                    "code": "generation_failed",
                    "message": "这次改变没有生成成功。原来的记录还在，可以直接重试。",
                    "retryable": True,
                }
                session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._write_session(session)
                raise InteractiveError(
                    "generation_failed",
                    "这次改变没有生成成功。原来的记录还在，可以直接重试。",
                    http_status=502,
                    retryable=True,
                ) from exc

            session["round_index"] = next_round
            session["current_stage"] = stage
            session["history"].append(stage)
            session.setdefault("review_trace", []).extend(review_records)
            session["status"] = "completed" if next_round == max_rounds else "ready"
            session["last_error"] = None
            session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._write_session(session)
            return self.public_envelope(session)

    def _review_evidence_pack(
        self,
        selection: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        direction = selection["direction"]
        constraint_mode = spec.get("world", {}).get(
            "constraint_mode", "historical_reconstruction"
        )
        match_scope = direction.get("knowledge_match_scope") or (
            "external_pressure" if constraint_mode == "future_scenario" else "transition"
        )
        card_id = (
            selection["environment"].get("knowledge_card_id", "NONE")
            if match_scope == "external_pressure"
            else direction.get("knowledge_card_id", "NONE")
        )
        match = self._knowledge_match(
            str(card_id),
            str(direction["transition_id"]),
            match_scope=match_scope,
        )
        historical_reference = (
            spec.get("historical_reference")
            if isinstance(spec.get("historical_reference"), dict)
            else {}
        )
        historical_source_ids = {
            str(item)
            for item in historical_reference.get("source_ids", [])
            if str(item).strip()
        }
        sources = list(match.get("sources", [])) + list(match.get("context_sources", []))
        sources.extend(
            source
            for source in self._sources
            if str(source.get("source_id", "")) in historical_source_ids
        )
        card = self._cards.get(str(card_id), {})
        pressure_ids = [
            str(item)
            for item in (
                [selection["environment"].get("pressure_id")]
                + list(card.get("pressure_ids", []))
            )
            if item
        ]
        return {
            "status": str(match.get("status", "no_match")),
            "match_scope": str(match.get("match_scope", match_scope)),
            "knowledge_card_id": str(match.get("knowledge_card_id", "NONE")),
            "historical_match_status": str(
                historical_reference.get("status", "not_applicable")
            ),
            "historical_candidate_names": list(
                dict.fromkeys(
                    str(name)
                    for candidate in historical_reference.get("candidates", [])
                    if isinstance(candidate, dict)
                    for name in (
                        candidate.get("scientific_name"),
                        candidate.get("display_name"),
                    )
                    if name
                )
            )[:12],
            "historical_required_external_traits": [
                str(item)
                for item in historical_reference.get("required_external_traits", [])
                if str(item).strip()
            ][:8],
            "historical_required_internal_traits": [
                str(item)
                for item in historical_reference.get("required_internal_traits", [])
                if str(item).strip()
            ][:8],
            "transition_ids": [str(direction["transition_id"])],
            "pressure_ids": list(dict.fromkeys(pressure_ids)),
            "source_ids": list(
                dict.fromkeys(
                    str(source.get("source_id"))
                    for source in sources
                    if isinstance(source, dict) and source.get("source_id")
                )
            ),
            "prerequisites": [str(item) for item in card.get("prerequisites", [])],
            "sources": [
                {
                    key: str(source.get(key, ""))
                    for key in ("source_id", "supports", "boundary")
                }
                for source in sources
                if isinstance(source, dict)
            ],
        }

    def _review_plan(
        self,
        result: dict[str, Any],
        metadata: dict[str, Any],
        previous: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
        records: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if self.review_mode == "off":
            return result, metadata, {
                "verdict": "not_run",
                "review_mode": "off",
                "revision_count": 0,
                "summary": "本轮沿用单规划器链路，没有运行科学审查。",
            }

        evidence_pack = self._review_evidence_pack(selection, spec)
        revision_count = 0
        current_result = result
        current_metadata = metadata
        for review_index in range(2):
            started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            input_hash = scientific_review.input_summary_hash(
                current_result,
                previous,
                selection,
                spec,
                evidence_pack,
            )
            rule_decision = scientific_review.deterministic_review(
                draft=current_result,
                previous=previous,
                selection=selection,
                spec=spec,
                evidence_pack=evidence_pack,
            )
            review_mode = "agent"
            adapter = "step-scientific-reviewer"
            version = STEP_MODEL
            fallback_path = "none"
            try:
                if self._reviewer is None:
                    raise RuntimeError("reviewer is not configured")
                raw_decision, review_metadata = self._reviewer(
                    copy.deepcopy(current_result),
                    copy.deepcopy(previous),
                    copy.deepcopy(selection),
                    copy.deepcopy(spec),
                    copy.deepcopy(evidence_pack),
                )
                decision = scientific_review.normalize_decision(
                    raw_decision,
                    evidence_pack=evidence_pack,
                )
                adapter = str(review_metadata.get("adapter", adapter))[:80]
                version = str(review_metadata.get("version", version))[:80]
            except Exception as exc:
                if self.review_mode == "required":
                    ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    records.append(
                        {
                            "role": "scientific_reviewer",
                            "adapter": adapter,
                            "adapter_version": version,
                            "input_summary_hash": input_hash,
                            "started_at": started_at,
                            "ended_at": ended_at,
                            "verdict": "block",
                            "transition_ids": evidence_pack["transition_ids"],
                            "pressure_ids": evidence_pack["pressure_ids"],
                            "source_ids": evidence_pack["source_ids"],
                            "issue_codes": ["REVIEWER_UNAVAILABLE"],
                            "summary": "科学审查服务没有在本轮完成。",
                            "revision_count": revision_count,
                            "final_fallback_path": "fail_closed",
                            "review_mode": "required",
                        }
                    )
                    raise InteractiveError(
                        "review_unavailable",
                        "科学审查暂时没有完成。原来的记录还在，可以稍后重试。",
                        http_status=503,
                        retryable=True,
                    ) from exc
                decision = rule_decision
                review_mode = "rules_only"
                adapter = "deterministic-rules-gate"
                version = "1.0"
                fallback_path = "rules_only"

            if rule_decision["verdict"] != "pass":
                decision = rule_decision
                if review_mode == "agent":
                    fallback_path = "agent_plus_rules"
            ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if review_index == 1 and decision["verdict"] != "pass":
                decision = {**decision, "verdict": "block"}
                fallback_path = "blocked_after_one_revision"
            records.append(
                {
                    "role": "scientific_reviewer",
                    "adapter": adapter,
                    "adapter_version": version,
                    "input_summary_hash": input_hash,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "verdict": decision["verdict"],
                    "transition_ids": decision["transition_ids"],
                    "pressure_ids": decision["pressure_ids"],
                    "source_ids": decision["source_ids"],
                    "issue_codes": decision["issue_codes"],
                    "summary": decision["summary"],
                    "revision_count": revision_count,
                    "final_fallback_path": fallback_path,
                    "review_mode": review_mode,
                }
            )
            if decision["verdict"] == "pass":
                return current_result, current_metadata, {
                    "verdict": "pass",
                    "review_mode": review_mode,
                    "revision_count": revision_count,
                    "issue_codes": decision["issue_codes"],
                    "summary": decision["summary"],
                }
            if decision["verdict"] == "block" or review_index == 1:
                raise InteractiveError(
                    "review_blocked",
                    "科学审查没有放行这一轮。原来的记录还在，请换一条路线或稍后重试。",
                    http_status=422,
                    retryable=True,
                )
            revision_count = 1
            revision_spec = copy.deepcopy(spec)
            transformations = {
                str(item.get("from")): str(item.get("to"))
                for item in selection["direction"].get("trait_transformations", [])
                if isinstance(item, dict) and item.get("from") and item.get("to")
            }
            required_protected_traits = [
                transformations.get(str(trait), str(trait))
                for trait in previous.get("protected_traits", [])
                if str(trait).strip()
            ]
            revision_spec["review_revision"] = {
                "issue_codes": decision["issue_codes"],
                "summary": decision["summary"],
                "required_protected_traits": required_protected_traits,
            }
            if self.dry_run:
                current_result, current_metadata = self._dry_run_plan(
                    previous,
                    selection,
                    revision_spec,
                )
            else:
                current_result, current_metadata = self._planner(
                    previous,
                    selection,
                    revision_spec,
                )
        raise AssertionError("review loop exceeded its bound")

    def _build_stage(
        self,
        round_no: int,
        previous: dict[str, Any],
        result: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        direction = selection["direction"]
        constraint_mode = spec.get("world", {}).get(
            "constraint_mode", "historical_reconstruction"
        )
        evidence_tag = direction.get("evidence_tag") or (
            "SCENARIO_EXTRAPOLATION"
            if constraint_mode == "future_scenario"
            else "KNOWN_MECHANISM"
            if direction["knowledge_card_id"] in self._cards
            else "MECHANISM_HYPOTHESIS"
        )
        if constraint_mode == "future_scenario":
            evidence_tag = "SCENARIO_EXTRAPOLATION"
        match_scope = direction.get("knowledge_match_scope") or (
            "external_pressure"
            if constraint_mode == "future_scenario"
            else "transition"
        )
        previous_traits = [str(item) for item in previous.get("traits", []) if str(item).strip()]
        inherited_traits = previous_traits[:2]
        protected_traits = [
            str(item) for item in previous.get("protected_traits", []) if str(item).strip()
        ]
        declared_transformations = [
            item
            for item in direction.get("trait_transformations", [])
            if isinstance(item, dict) and item.get("from") and item.get("to")
        ]
        if constraint_mode == "historical_reconstruction":
            if not protected_traits:
                protected_traits = previous_traits[:2]
            for transformation in declared_transformations:
                source_trait = str(transformation.get("from", "")).strip()
                target_trait = str(transformation.get("to", "")).strip()
                if source_trait in protected_traits and target_trait:
                    protected_traits = [
                        target_trait if trait == source_trait else trait
                        for trait in protected_traits
                    ]
            protected_traits.extend(
                str(item)
                for item in direction.get("continuity_traits", [])
                if str(item).strip()
            )
            protected_traits = list(dict.fromkeys(protected_traits))[:6]
            inherited_traits = protected_traits
        traits = list(dict.fromkeys(protected_traits + inherited_traits + result["traits"]))[:8]
        return {
            "round": round_no,
            "scenario_id": previous.get("scenario_id", self.default_scenario_id),
            "stage_id": f"round_{round_no}",
            "transition_id": direction["transition_id"],
            "lineage_parent": {
                "round": previous["round"],
                "organism_name": previous["organism_name"],
                "lineage_summary": previous["lineage_summary"],
            },
            "organism_name": result["organism_name"],
            "lineage_summary": result["lineage_summary"],
            "change_summary": result["change_summary"],
            "inherited_traits": inherited_traits,
            "protected_traits": protected_traits,
            "traits": traits,
            "internal_causes": result["internal_causes"],
            "external_causes": result["external_causes"],
            "benefits": result["benefits"],
            "costs": result["costs"],
            "evidence_tag": evidence_tag,
            "uncertainty_note": result["uncertainty_note"],
            "time_scope": spec["time_scope"],
            "selection": {
                "environment": selection["environment"],
                "contingency": selection["contingency"],
                "direction": {
                    key: direction[key]
                    for key in (
                        "id",
                        "title",
                        "description",
                        "tradeoff_hint",
                        "transition_id",
                        "context_reason",
                    )
                    if key in direction
                },
            },
            "knowledge_match": self._knowledge_match(
                (
                    selection["environment"].get("knowledge_card_id", "NONE")
                    if match_scope == "external_pressure"
                    else direction["knowledge_card_id"]
                ),
                direction["transition_id"],
                match_scope=match_scope,
            ),
            "historical_reference": self._public_historical_reference(
                spec.get("historical_reference", {})
            ),
            "model": {
                "planner": metadata["planner"],
                "reasoning_effort": metadata["reasoning_effort"],
                "strict_schema": bool(metadata["strict_schema"]),
                "attempts": int(metadata.get("attempts", 1)),
                "choice_planner": copy.deepcopy(selection.get("context_model", {})),
            },
        }

    def _fixture_option_plan(
        self,
        previous: dict[str, Any],
        spec: dict[str, Any],
        environment: dict[str, Any],
        contingency: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Deterministic browser fixture; production uses the constrained Step planner."""
        del previous
        environment_ids = [item["id"] for item in spec["environments"]]
        environment_index = environment_ids.index(environment["id"])
        contingency_ids = [item["id"] for item in spec["contingencies"]]
        contingency_index = (
            contingency_ids.index(contingency["id"]) + 1 if contingency else 0
        )

        def rotated(items: list[dict[str, Any]], offset: int) -> list[dict[str, Any]]:
            if not items:
                return []
            ordered = items[offset % len(items):] + items[: offset % len(items)]
            return ordered[: min(2, len(ordered))]

        contingencies = rotated(spec["contingencies"], environment_index)
        directions = rotated(spec["directions"], environment_index + contingency_index)
        return {
            "contingencies": [
                {
                    "id": item["id"],
                    "reason": f"在“{environment['title']}”里，这次偶然变化更容易改变后果。",
                }
                for item in contingencies
            ],
            "directions": [
                {
                    "id": item["id"],
                    "reason": (
                        f"它同时回应“{environment['title']}”"
                        + (f"和“{contingency['title']}”" if contingency else "带来的选择压力")
                        + "，也没有越过已有性状的边界。"
                    ),
                }
                for item in directions
            ],
        }, {
            "planner": "fixture_context_ranker",
            "reasoning_effort": "not_applicable",
            "strict_schema": True,
        }

    @staticmethod
    def _option_schema(spec: dict[str, Any]) -> dict[str, Any]:
        def ranked_items(ids: list[str]) -> dict[str, Any]:
            return {
                "type": "array",
                "minItems": 1,
                "maxItems": min(2, len(ids)),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "reason"],
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "reason": {"type": "string", "minLength": 8, "maxLength": 160},
                    },
                },
            }

        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["contingencies", "directions"],
            "properties": {
                "contingencies": ranked_items(
                    [str(item["id"]) for item in spec["contingencies"]]
                ),
                "directions": ranked_items(
                    [str(item["id"]) for item in spec["directions"]]
                ),
            },
        }

    def _plan_options_with_step(
        self,
        previous: dict[str, Any],
        spec: dict[str, Any],
        environment: dict[str, Any],
        contingency: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        key = os.environ.get("STEP_API_KEY") or os.environ.get("STEPFUN_API_KEY")
        if not key:
            raise InteractiveError(
                "planner_unavailable",
                "环境重算服务还没有连接好，原来的记录不受影响。",
                http_status=503,
                retryable=True,
            )
        schema = self._option_schema(spec)
        mode = spec.get("world", {}).get(
            "constraint_mode", "historical_reconstruction"
        )
        if mode == "historical_reconstruction":
            boundary = (
                "这是历史重建。只能从候选清单中排序，不得发明新路线、新器官或智能阶段；"
                "理由必须能由当前环境、已有性状和候选描述直接推出。"
            )
        elif mode == "future_scenario":
            boundary = (
                "这是未来情景推演。可以开放比较候选结果，但仍只能选择清单中的路线；"
                "理由要明确是压力下的可能性，不得写成必然预测。"
            )
        else:
            boundary = (
                "这是竞争假说。只能比较清单中的候选局部步骤，不得宣布已经复原唯一历史。"
            )
        protected_traits = [
            str(item) for item in previous.get("protected_traits", []) if str(item).strip()
        ]
        contingency_text = (
            f"已选偶然事件：{contingency['title']}——{contingency['description']}\n"
            if contingency
            else "尚未选择偶然事件；先比较哪些事件在这个环境里更有影响。\n"
        )
        candidate_contingencies = "\n".join(
            f"- {item['id']}｜{item['title']}：{item['description']}"
            for item in spec["contingencies"]
        )
        candidate_directions = "\n".join(
            f"- {item['id']}｜{item['title']}：{item['description']}；机制：{item['mechanism_hint']}；代价：{item['tradeoff_hint']}"
            for item in spec["directions"]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 EvoLab 的候选路线排序器。" + boundary
                    + "每组最多选两个，按与当前条件的贴近程度排序。"
                    "reason 使用一到两句完整、自然、简洁的简体中文，直接解释为什么此刻出现，不能复述标题，"
                    "不能露出候选 ID，也不能把句子截断。"
                    "只返回符合 JSON Schema 的 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"世界：{spec['world']['title']}\n"
                    f"当前阶段：{previous['lineage_summary']}\n"
                    f"已有性状：{'；'.join(previous.get('traits', []))}\n"
                    f"不得无解释消失的性状：{'；'.join(protected_traits) if protected_traits else '无额外登记'}\n"
                    f"已选环境：{environment['title']}——{environment['description']}\n"
                    + contingency_text
                    + "偶然事件候选：\n"
                    + candidate_contingencies
                    + "\n演化方向候选：\n"
                    + candidate_directions
                ),
            },
        ]
        try:
            envelope = helper.post_json(
                configured_step_endpoint(),
                {
                    "model": configured_step_model(),
                    "reasoning_effort": "high",
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "contextual_evolution_choices",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                },
                {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                self.step_timeout,
            )
            if envelope.get("model") != configured_step_model():
                raise ValueError("unexpected model")
            result = json.loads(envelope["choices"][0]["message"]["content"])
        except Exception as exc:
            raise InteractiveError(
                "planner_unavailable",
                "没有算出新的候选顺序，原来的记录不受影响，可以重试。",
                http_status=502,
                retryable=True,
            ) from exc
        errors = helper.validate_schema(result, schema)
        candidate_titles = {
            str(item["id"]): str(item["title"])
            for item in spec["contingencies"] + spec["directions"]
        }
        for group in ("contingencies", "directions"):
            values = result.get(group, []) if isinstance(result, dict) else []
            ids = [item.get("id") for item in values if isinstance(item, dict)]
            if len(ids) != len(set(ids)):
                errors.append(f"$.{group}: duplicate ids")
            if any(
                not _is_natural_chinese(item.get("reason"))
                for item in values
                if isinstance(item, dict)
            ):
                errors.append(f"$.{group}: reasons must use natural Simplified Chinese")
            for item in values:
                if not isinstance(item, dict) or not isinstance(item.get("reason"), str):
                    continue
                reason = item["reason"].strip()
                if not reason.endswith(("。", "！", "？")):
                    errors.append(f"$.{group}: reason must be a complete sentence")
                if any(candidate_id in reason for candidate_id in candidate_titles):
                    errors.append(f"$.{group}: reason must not expose candidate ids")
        if errors:
            helper.log("Contextual option validation failed: " + "; ".join(errors[:10]))
            raise InteractiveError(
                "planner_invalid_output",
                "候选重算没有通过校验，请重新选择当前环境。",
                http_status=502,
                retryable=True,
            )
        return result, {
            "planner": configured_step_model(),
            "reasoning_effort": "high",
            "strict_schema": True,
        }

    def _messages(
        self,
        previous: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
        retry_errors: list[str] | None = None,
    ) -> list[dict[str, str]]:
        direction = selection["direction"]
        chemistry_world = spec.get("world", {}).get("id") == "hydrothermal_origin"
        constraint_mode = spec.get("world", {}).get(
            "constraint_mode", "historical_reconstruction"
        )
        historical_reference = (
            spec.get("historical_reference")
            if isinstance(spec.get("historical_reference"), dict)
            else {}
        )
        historical_status = str(
            historical_reference.get("status", "not_applicable")
        )
        historical_candidates = [
            item
            for item in historical_reference.get("candidates", [])
            if isinstance(item, dict)
        ]
        historical_external = [
            str(item)
            for item in historical_reference.get("required_external_traits", [])
            if str(item).strip()
        ]
        historical_internal = [
            str(item)
            for item in historical_reference.get("required_internal_traits", [])
            if str(item).strip()
        ]
        protected_traits = [
            str(item) for item in previous.get("protected_traits", []) if str(item).strip()
        ]
        declared_transformations = [
            item
            for item in direction.get("trait_transformations", [])
            if isinstance(item, dict) and item.get("from") and item.get("to")
        ]
        retry_note = ""
        if retry_errors:
            retry_note = "\n上次输出未通过结构校验，请只修复这些问题：" + "；".join(retry_errors[:10])
        review_revision = spec.get("review_revision")
        if isinstance(review_revision, dict):
            issue_codes = [
                str(item) for item in review_revision.get("issue_codes", [])[:8]
            ]
            review_summary = str(review_revision.get("summary", "")).strip()[:240]
            required_revision_traits = [
                str(item).strip()
                for item in review_revision.get("required_protected_traits", [])[:6]
                if str(item).strip()
            ]
            retry_note += (
                "\n科学审查只允许本次修订："
                + "；".join(issue_codes)
                + ("。裁决摘要：" + review_summary if review_summary else "")
            )
            if "PROTECTED_TRAIT_MISSING" in issue_codes and required_revision_traits:
                retry_note += (
                    "\n本次必须逐字写入 traits，并在 lineage_summary 中明确说明仍然保留："
                    + "；".join(required_revision_traits)
                    + "。不要用近义词替换这些账本名称。"
                )
            if "HISTORICAL_ANALOG_DIVERGENCE" in issue_codes:
                retry_note += (
                    "\n真实类群参照没有落实。本次须逐字写入至少一项外部锚点和一项内部锚点："
                    + "；".join(historical_external)
                    + " / "
                    + "；".join(historical_internal)
                    + "。"
                )
            if "UNSUPPORTED_TAXON_CLAIM" in issue_codes:
                retry_note += "\n不要把参照类群当作本轮生成物种，也不要声称它是直接祖先。"
        subject_rule = (
            "当前世界位于生命起源之前。主语只能是化学系统、反应网络或原始区室；"
            "使用结构、反应循环和延续，不得写身体、后代、物种或已确认生物。"
            if chemistry_world
            else "当前世界已有生物谱系。你描述的是跨多代选择，不是一个个体主动变形。"
        )
        if constraint_mode == "historical_reconstruction":
            constraint_rule = (
                "历史重建模式：只允许使用场景包给出的有来源路径。关键性状不得无解释消失；"
                "如果方向没有声明有证据支持的性状转化，就必须在名称、摘要、traits 和画面中继续保留。"
                "不要为了让故事更戏剧化而发明器官、智能阶段或直线进步阶梯。"
            )
        elif constraint_mode == "future_scenario":
            constraint_rule = (
                "未来推演模式：允许在已知生理压力和遗传机制上提出多种结果，但每个结果都只是情景假说。"
                "必须区分个体在太空中的短期适应、技术补偿与跨世代可遗传变化；"
                "不能把适应性反应写成可遗传演化，也不能声称某种形态必然出现。"
            )
        else:
            constraint_rule = (
                "起源假说模式：只能拼接实验已证明可行的局部步骤，不能宣布已经复原第一生命或唯一历史。"
            )
        historical_rule = ""
        historical_context = ""
        if historical_status == "historical_reference" and historical_candidates:
            top = historical_candidates[0]
            scientific_name = str(top.get("scientific_name", "")).strip()
            display_name = str(top.get("display_name", "")).strip()
            boundary = str(top.get("boundary", "")).strip()
            historical_rule = (
                "本轮有高匹配度的真实历史类群。生成形态须沿用它已有证据支持的外部和内部性状组合；"
                "名称仍属于当前谱系，不能写成参照物种，也不能写成直接祖先。"
                "traits 与 lineage_summary 至少逐字包含一项外部锚点和一项内部锚点，"
                "image_prompt 也要画出可见的外部锚点。"
            )
            historical_context = (
                f"\n真实类群参照：{scientific_name}（{display_name}）"
                f"\n必须保留的外部锚点：{'；'.join(historical_external)}"
                f"\n必须保留的内部锚点：{'；'.join(historical_internal)}"
                f"\n证据边界：{boundary}"
                f"\n来源编号：{'；'.join(str(item) for item in top.get('source_ids', []))}"
            )
        elif historical_status in {"partial_reference", "bounded_inference"}:
            candidate_names = "；".join(
                str(item.get("scientific_name") or item.get("display_name") or "")
                for item in historical_candidates
                if item.get("scientific_name") or item.get("display_name")
            )
            historical_rule = (
                "本轮没有足够完整的真实类群匹配。候选化石只能提供环境或形态背景；"
                "结果须写成受约束推测，不能借用候选物种名称，也不能补写化石没有保存的器官。"
            )
            historical_context = (
                f"\n有限参照：{candidate_names or '没有直接候选'}"
                f"\n知识边界：{historical_reference.get('message', '')}"
            )
        system = (
            "你是 EvoLab 的受约束科学情景规划器。" + subject_rule + constraint_rule + historical_rule +
            "必须继承上一阶段至少一项可辨识特征，再叠加本轮环境、偶发事件和用户选择的方向。"
            "允许被本轮机制改变的旧性状发生转化，禁止把已经被替代的旧性状机械照搬。"
            "收益和代价必须同时出现，禁止目的论、必然论和没有来源的精确适合度数值。"
            "深时节点可以解释已知机制；未来阶段必须明确是受约束的情景推演，不是确定预测。"
            "竞争假说要明确写出未解之处，不要用宏大口号、论文摘要腔或整齐排比。"
            "image_prompt 必须使用英文，描述一张无文字、无标签、单一主体明确的电影感科学插画；"
            "image_prompt 的第一句必须先说清本轮选定方向造成的可见主变化，不能只写同一种动物换了背景；"
            f"遵循当前世界的视觉锚点：{spec['world'].get('visual_anchor', '')}；"
            f"避免：{spec['world'].get('visual_negative', '')}；并保留上一阶段至少一项可辨认视觉特征。"
            "其余字段只使用自然、简洁的简体中文；除通用缩写外，任何整句英文都会被拒绝。"
            "只返回符合指定 JSON Schema 的 JSON。"
        )
        user = (
            f"当前世界：{spec['world']['title']}（{spec['world']['era']}，{spec['world']['habitat']}）\n"
            f"世界边界：{spec['world']['summary']}\n"
            f"当前章节：{spec['chapter']}\n"
            f"上一阶段名称：{previous['organism_name']}\n"
            f"上一阶段记录：{previous['lineage_summary']}\n"
            f"上一阶段可供继承或转化的性状：{'；'.join(previous['traits'])}\n"
            f"历史关键性状账本：{'；'.join(protected_traits) if protected_traits else '当前没有额外登记项'}\n"
            f"本轮环境：{selection['environment']['title']}——{selection['environment']['description']}\n"
            f"偶发事件：{selection['contingency']['title']}——{selection['contingency']['description']}\n"
            f"演化方向：{direction['title']}——{direction['description']}\n"
            f"机制约束：{direction['mechanism_hint']}\n"
            f"已知权衡：{direction['tradeoff_hint']}\n"
            f"画面主变化约束（英文，必须遵守）：{direction.get('visible_change_prompt', 'follow the selected mechanism without inventing a later anatomical milestone')}\n"
            f"画面禁止项：{'；'.join(direction.get('visual_forbidden', [])) or '遵守世界边界，不提前画出后续阶段的结构'}\n"
            "本轮允许的关键性状转化："
            + (
                "；".join(
                    f"{item['from']} → {item['to']}" for item in declared_transformations
                )
                if declared_transformations
                else "没有；关键性状只能继续保留"
            )
            + "\n"
            + historical_context
            + "\n请生成下一阶段。lineage_summary 必须明确它继承了什么、改变了什么；"
            "traits 至少保留一项上一阶段可辨认特征，再加入本轮新特征；"
            "历史关键性状账本中的项目必须逐项保留，除非本方向明确声明了有证据支持的转化。"
            f"{retry_note}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _plan_with_step(
        self,
        previous: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        key = os.environ.get("STEP_API_KEY") or os.environ.get("STEPFUN_API_KEY")
        if not key:
            raise InteractiveError(
                "planner_unavailable",
                "演化规划服务还没有连接好，请稍后重试。",
                http_status=503,
                retryable=True,
            )
        retry_errors: list[str] | None = None
        for attempt in range(2):
            body = {
                "model": configured_step_model(),
                "reasoning_effort": "high",
                "messages": self._messages(previous, selection, spec, retry_errors),
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "interactive_evolution_stage",
                        "strict": True,
                        "schema": self.schema,
                    },
                },
            }
            try:
                envelope = helper.post_json(
                    configured_step_endpoint(),
                    body,
                    {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    self.step_timeout,
                )
                if envelope.get("model") != configured_step_model():
                    raise ValueError("unexpected model")
                result = json.loads(envelope["choices"][0]["message"]["content"])
            except InteractiveError:
                raise
            except Exception as exc:
                raise InteractiveError(
                    "planner_unavailable",
                    "演化规划服务暂时没有响应。原来的记录还在，可以稍后重试。",
                    http_status=502,
                    retryable=True,
                ) from exc
            errors = helper.validate_schema(result, self.schema)
            image_prompt = result.get("image_prompt")
            if isinstance(image_prompt, str) and re.search(r"[\u4e00-\u9fff]", image_prompt):
                errors.append("$.image_prompt: must be written in English")
            for field in (
                "organism_name",
                "lineage_summary",
                "change_summary",
                "traits",
                "internal_causes",
                "external_causes",
                "benefits",
                "costs",
                "uncertainty_note",
            ):
                value = result.get(field)
                values = value if isinstance(value, list) else [value]
                if any(
                    isinstance(item, str) and helper.PSEUDO_PRECISION_PATTERN.search(item)
                    for item in values
                ):
                    errors.append(f"$.{field}: unsupported pseudo-precision")
                if any(isinstance(item, str) and not _is_natural_chinese(item) for item in values):
                    errors.append(f"$.{field}: must use natural Simplified Chinese")
            if not errors:
                return result, {
                    "planner": configured_step_model(),
                    "reasoning_effort": "high",
                    "strict_schema": True,
                    "attempts": attempt + 1,
                    "finish_reason": envelope["choices"][0].get("finish_reason"),
                }
            helper.log(
                "Interactive Step validation attempt "
                f"{attempt + 1} failed: {'; '.join(errors[:10])}"
            )
            retry_errors = errors
        raise InteractiveError(
            "planner_invalid_output",
            "演化规划没有通过结构校验。原来的记录还在，可以重试。",
            http_status=502,
            retryable=True,
        )

    def _review_with_step(
        self,
        draft: dict[str, Any],
        previous: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
        evidence_pack: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        key = os.environ.get("STEP_API_KEY") or os.environ.get("STEPFUN_API_KEY")
        if not key:
            raise RuntimeError("reviewer credential is not configured")
        bounded_input = {
            "draft": draft,
            "parent_trait_ledger": {
                "traits": previous.get("traits", []),
                "protected_traits": previous.get("protected_traits", []),
            },
            "scenario_rules": {
                "world_id": spec.get("world", {}).get("id"),
                "constraint_mode": spec.get("world", {}).get("constraint_mode"),
                "time_scope": spec.get("time_scope"),
                "selected_transition_id": selection.get("direction", {}).get("transition_id"),
                "declared_trait_transformations": selection.get("direction", {}).get(
                    "trait_transformations", []
                ),
            },
            "evidence_pack": evidence_pack,
        }
        system = (
            "你是 EvoLab 的独立科学审查员。你只审查给定的结构化草案、父代性状账本、"
            "场景规则和证据包，不猜测规划器的隐藏推理。检查历史前置条件、关键性状连续性、"
            "未来情景中个体适应与可遗传演化的区分，以及来源是否确实存在于证据包。"
            "历史重建模式必须逐项保留 protected_traits，除非场景明确声明了转化；"
            "起源假说模式的 traits 是描述性记录，不是强制逐项复制的历史账本，只需保留至少一个可辨认结构，"
            "并确保仍以化学系统、区室、反应或循环为主语，不提前宣布生命、遗传体系或物种已经出现；"
            "未来情景只阻断把个体短期反应或技术补偿直接写成可遗传演化的草案。"
            "轻微措辞、命名风格或非保护性状的取舍不构成 revise 或 block。"
            "verdict 只能是 pass、revise 或 block；revise 必须给出稳定的问题代码。"
            "不得续写故事，不得发明来源，不得输出提示词、密钥或思维过程。"
            "只返回符合指定 JSON Schema 的 JSON。"
        )
        body = {
            "model": configured_step_model(),
            "reasoning_effort": "high",
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(bounded_input, ensure_ascii=False, sort_keys=True),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "evolab_scientific_review",
                    "strict": True,
                    "schema": scientific_review.REVIEW_SCHEMA,
                },
            },
        }
        envelope = helper.post_json(
            configured_step_endpoint(),
            body,
            {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            self.step_timeout,
        )
        if envelope.get("model") != configured_step_model():
            raise ValueError("unexpected review model")
        raw = json.loads(envelope["choices"][0]["message"]["content"])
        errors = helper.validate_schema(raw, scientific_review.REVIEW_SCHEMA)
        if errors:
            raise ValueError("invalid review response")
        decision = scientific_review.normalize_decision(raw, evidence_pack=evidence_pack)
        return decision, {
            "adapter": "step-scientific-reviewer",
            "version": configured_step_model(),
            "strict_schema": True,
        }

    def _render_with_comfy(
        self,
        result: dict[str, Any],
        previous: dict[str, Any],
        selection: dict[str, Any],
        destination: Path,
    ) -> dict[str, Any]:
        previous_traits = ", ".join(str(item) for item in previous.get("traits", [])[:4])
        environment = selection["environment"]
        direction = selection["direction"]
        scenario = self._scenario(previous.get("scenario_id"))
        constraint_mode = scenario.get("constraint_mode", "historical_reconstruction")
        historical_reference = (
            selection.get("historical_reference")
            if isinstance(selection.get("historical_reference"), dict)
            else {}
        )
        historical_candidates = [
            item
            for item in historical_reference.get("candidates", [])
            if isinstance(item, dict)
        ]
        protected_visual_traits = list(
            dict.fromkeys(
                [str(item) for item in previous.get("protected_traits", [])]
                + [str(item) for item in direction.get("continuity_traits", [])]
            )
        )
        declared_transformations = [
            item
            for item in direction.get("trait_transformations", [])
            if isinstance(item, dict) and item.get("from") and item.get("to")
        ]
        for transformation in declared_transformations:
            source_trait = str(transformation["from"])
            target_trait = str(transformation["to"])
            protected_visual_traits = [
                target_trait if trait == source_trait else trait
                for trait in protected_visual_traits
            ]
        protected_visual_traits = list(dict.fromkeys(protected_visual_traits))
        previous_asset = destination.parent / Path(
            str(previous.get("image_url", ""))
        ).name
        reference_image = (
            previous_asset
            if previous_asset.suffix.lower() == ".png" and previous_asset.is_file()
            else None
        )
        continuity = (
            " This is the immediate descendant of the previous lineage, not a new unrelated species."
            f" Previous lineage: {previous.get('organism_name', 'unnamed lineage')}."
            f" Preserve recognizable inherited features: {previous_traits}."
            f" New environmental pressure: {environment['title']} — {environment['description']}"
            f" Chosen evolutionary direction: {direction['title']} — {direction['description']}"
            " Show a gradual modification in the same lineage or chemical system, while allowing a new pose, framing,"
            " proportions, and interaction with the habitat when they make the selected change easier to read."
            " Show the environment and adaptation as one coherent scene, with no comparison grid, text, letters, labels, or watermark."
        )
        catalog_visual_change = str(direction.get("visible_change_prompt", "")).strip()
        visual_forbidden = [
            str(item).strip()
            for item in direction.get("visual_forbidden", [])
            if str(item).strip()
        ]
        if catalog_visual_change:
            required_visual_change = catalog_visual_change
        elif declared_transformations:
            required_visual_change = "; ".join(
                f"change {item['from']} into {item['to']}"
                for item in declared_transformations
            )
        elif direction.get("continuity_traits"):
            required_visual_change = (
                f"visibly strengthen or express {', '.join(direction['continuity_traits'])}"
                f" through {direction['mechanism_hint']}"
            )
        else:
            required_visual_change = (
                f"visibly express the selected route '{direction['title']}' through"
                f" {direction['mechanism_hint']} and behavior in the chosen habitat"
            )
        continuity += (
            " Required visible change: "
            + required_visual_change
            + ". Make this selected change obvious at thumbnail scale through the silhouette,"
            " load-bearing anatomy, surface coverage, posture, behavior, or contact with the environment."
            " The reference defines lineage identity, not an exact pose or tracing template."
            " Do not return an unchanged copy of the parent image."
        )
        if visual_forbidden:
            continuity += (
                " Catalog anatomy gate: do not show "
                + ", ".join(visual_forbidden)
                + ". These structures belong to another stage or lineage and would break the historical evidence chain."
            )
        if declared_transformations:
            continuity += (
                " The structural change should be readable in joint structure, appendage shape,"
                " proportions, or how the body bears weight."
            )
        if constraint_mode == "historical_reconstruction" and protected_visual_traits:
            continuity += (
                " Historical reconstruction constraint: the following established lineage features MUST remain visibly"
                " recognizable unless the selected catalog direction explicitly transforms them: "
                + ", ".join(protected_visual_traits)
                + ". Do not revert the body to an earlier generic form."
            )
            if declared_transformations:
                continuity += (
                    " The only catalog-approved structural transformations in this step are: "
                    + "; ".join(
                        f"{item['from']} -> {item['to']}"
                        for item in declared_transformations
                    )
                    + ". Show the intermediate continuity clearly."
                )
            if reference_image is not None:
                continuity += (
                    " Use the supplied previous-stage image as the visual parent, not as a frame to copy."
                    " Keep the lineage recognizable through homologous relationships among the head, trunk, tail, and paired appendages,"
                    " while visibly changing the catalog-approved anatomy or behavior."
                    " A protected structure already visible in the parent must stay recognizable in the descendant."
                )
            if scenario.get("id") == "devonian_estuary" and any(
                "附肢" in trait or "肉质鳍" in trait
                for trait in protected_visual_traits
            ):
                continuity += (
                    " Devonian appendage lock: keep the four paired, weight-bearing appendages homologous to the shoulder and pelvic"
                    " attachment regions. Their proportions, joints, distal supports, spread, and contact with the substrate may change"
                    " visibly. Do not replace them with ordinary ray fins or reduce their number."
                )
        elif constraint_mode == "future_scenario":
            continuity += (
                " Future scenario constraint: keep the human lineage recognizable and show only gradual, multigenerational"
                " divergence. Do not present technology, acclimatization, or a single astronaut's body response as inherited evolution."
            )
        continuity += (
            f" World-specific visual anchor: {scenario.get('visual_anchor', '')}."
            f" Avoid: {scenario.get('negative_prompt', '')}."
        )
        if (
            historical_reference.get("status") == "historical_reference"
            and historical_candidates
        ):
            historical_top = historical_candidates[0]
            continuity += (
                " Evidence-backed historical analog: "
                + str(historical_top.get("scientific_name", "curated fossil taxon"))
                + ". Use the following fossil-constrained body-plan combination as the anatomical reference,"
                " while keeping the generated lineage distinct and not depicting a literal portrait or direct ancestor: "
                + str(historical_top.get("visual_anchor_en", ""))
                + ". Do not invent soft anatomy or behavior beyond the stated fossil boundary."
            )
        if direction["id"] == "multicellular_body":
            continuity += (
                " Show a physically connected clonal cluster: visible cell-to-cell adhesion bridges and early division of labor,"
                " not several unrelated floating cells."
            )
        if direction["id"] == "thermal_refuge":
            continuity += (
                " Keep the previous attached multicellular or colonial body plan visibly intact under warmer water;"
                " express heat tolerance through membrane texture, protective translucent layers, and behavior, not petals or leaves."
            )
        image_prompt = result["image_prompt"] + continuity
        negative_prompt = (
            str(scenario.get("negative_prompt", ""))
            + ", text, labels, comparison grid, unrelated species"
            + (", " + ", ".join(visual_forbidden) if visual_forbidden else "")
        )
        remove_negative_terms = (
            ("duplicated organism, ",)
            if selection["direction"]["id"] in {"multicellular_body", "cooperative_colony"}
            else ()
        )
        seed = int(uuid.uuid4().hex[:8], 16)
        if os.environ.get("EVOLAB_UNLOAD_OLLAMA", "1") != "0":
            helper.unload_ollama()
        comfy_url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:7000")
        default_output = (
            Path(os.environ.get("WORKSHOP_DIR", "/home/Developer/build_a_claw_workshop-bundle"))
            / "comfyui-app"
            / "ComfyUI"
            / "output"
        )
        comfy_output = Path(os.environ.get("COMFY_OUTPUT_DIR", str(default_output)))
        primary = os.environ.get("EVOLAB_IMAGE_RENDERER", "flux1")
        fallback = os.environ.get("EVOLAB_IMAGE_FALLBACK", "flux1")
        try:
            rendered = rendering.render_image_with_fallback(
                rendering.renderer_chain(primary, fallback),
                prompt=image_prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                filename_prefix=f"interactive_{destination.parent.name}_{destination.stem}",
                destination=destination,
                comfy_url=comfy_url,
                comfy_output=comfy_output,
                timeout=self.comfy_timeout,
                submit_prompt=helper.submit_comfy_prompt,
                wait_for_prompt=helper.wait_for_comfy,
                locate_output=helper.comfy_output_path,
                remove_negative_terms=remove_negative_terms,
                reference_image=reference_image,
                upload_reference=video_generation.upload_image,
                log=helper.log,
            )
        except (rendering.RendererConfigurationError, rendering.RendererExhaustedError) as exc:
            raise InteractiveError(
                "renderer_unavailable",
                "图片没有生成成功。原来的记录还在，可以直接重试这一轮。",
                http_status=502,
                retryable=True,
            ) from exc
        visual_change_score = None
        technical_visual_gate = None
        visual_review: dict[str, Any] = {
            "mode": "not_applicable",
            "verdict": "not_run",
            "summary": "首张生成图没有可供比较的父代 PNG。",
        }
        if reference_image is not None:
            visual_change_score = _visual_change_score(reference_image, destination)
            technical_visual_gate = visual_continuity.technical_visual_gate(
                reference_image,
                destination,
            )
            if not technical_visual_gate["passed"]:
                destination.unlink(missing_ok=True)
                helper.log(
                    "reference-conditioned image rejected by technical visual gate "
                    + ",".join(technical_visual_gate["issue_codes"])
                )
                error = InteractiveError(
                    "visual_continuity_failed",
                    "这次改变没有生成成功。原来的记录还在，可以直接重试。",
                    http_status=502,
                    retryable=True,
                )
                error.private_details = {
                    "mode": "technical_structure_only",
                    "verdict": "block",
                    "issue_codes": technical_visual_gate["issue_codes"],
                    "metrics": technical_visual_gate,
                }
                raise error
            if self.visual_review_mode == "off":
                visual_review = {
                    "mode": "technical_only",
                    "verdict": "not_run",
                    "summary": "只完成了结构与文件门禁，没有进行图像语义审查。",
                }
            else:
                try:
                    if self._visual_reviewer is None:
                        raise RuntimeError("visual reviewer is not configured")
                    decision, review_metadata = self._visual_reviewer(
                        reference_image,
                        destination,
                        protected_visual_traits,
                        visual_forbidden,
                    )
                    visual_review = {
                        "mode": "semantic",
                        "verdict": decision["verdict"],
                        "adapter": str(review_metadata.get("adapter", "unknown")),
                        "adapter_version": str(review_metadata.get("version", "unknown")),
                        "identity_continuity": bool(decision["identity_continuity"]),
                        "protected_traits": decision["protected_traits"],
                        "forbidden_findings": decision["forbidden_findings"],
                        "summary": decision["summary"],
                    }
                    if decision["verdict"] != "pass":
                        destination.unlink(missing_ok=True)
                        error = InteractiveError(
                            "visual_semantic_review_blocked",
                            "图像审查没有确认谱系连续性。原来的记录还在，可以直接重试。",
                            http_status=502,
                            retryable=True,
                        )
                        error.private_details = copy.deepcopy(visual_review)
                        raise error
                except InteractiveError:
                    raise
                except Exception as exc:
                    if self.visual_review_mode == "required":
                        destination.unlink(missing_ok=True)
                        error = InteractiveError(
                            "visual_reviewer_unavailable",
                            "图像审查暂时没有完成。原来的记录还在，可以稍后重试。",
                            http_status=503,
                            retryable=True,
                        )
                        error.private_details = {
                            "mode": "semantic",
                            "verdict": "block",
                            "issue_codes": ["VISUAL_REVIEWER_UNAVAILABLE"],
                        }
                        raise error from exc
                    visual_review = {
                        "mode": "technical_only",
                        "verdict": "not_run",
                        "summary": "图像语义审查暂时不可用，本轮只完成结构与文件门禁。",
                    }
        return {
            "render_source": "generated",
            "generator": rendered.generator,
            "renderer": rendered.renderer,
            "seed": rendered.seed,
            "duration_seconds": rendered.duration_seconds,
            "fallback_from": rendered.fallback_from,
            "reference_conditioning": rendered.reference_conditioning,
            "required_visual_change": required_visual_change,
            "visual_forbidden": visual_forbidden,
            "visual_change_score": visual_change_score,
            "technical_visual_gate": technical_visual_gate,
            "visual_review": visual_review,
        }

    def _review_visual_with_step(
        self,
        parent: Path,
        descendant: Path,
        protected_traits: list[str],
        forbidden_traits: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        key = os.environ.get("STEP_API_KEY") or os.environ.get("STEPFUN_API_KEY")
        if not key:
            raise RuntimeError("visual reviewer credential is not configured")
        decision = visual_continuity.review_with_step(
            parent=parent,
            descendant=descendant,
            protected_traits=protected_traits,
            forbidden_traits=forbidden_traits,
            endpoint=configured_step_endpoint(),
            model=configured_step_model(),
            api_key=key,
            timeout=self.step_timeout,
            post_json=helper.post_json,
            validate_schema=helper.validate_schema,
        )
        return decision, {
            "adapter": "step-multimodal-visual-reviewer",
            "version": configured_step_model(),
        }

    def _dry_run_plan(
        self,
        previous: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        direction = selection["direction"]
        environment = selection["environment"]
        contingency = selection["contingency"]
        inherited = list(
            dict.fromkeys(
                [str(item) for item in previous.get("traits", [])[:2]]
                + [str(item) for item in previous.get("protected_traits", [])]
            )
        )
        transformations = {
            str(item["from"]): str(item["to"])
            for item in direction.get("trait_transformations", [])
            if isinstance(item, dict) and item.get("from") and item.get("to")
        }
        inherited = [transformations.get(item, item) for item in inherited]
        chemistry_world = spec.get("world", {}).get("id") == "hydrothermal_origin"
        constraint_mode = spec.get("world", {}).get(
            "constraint_mode", "historical_reconstruction"
        )
        inherited_phrase = "和".join(inherited) if inherited else ("上一阶段的结构" if chemistry_world else "上一阶段的身体特征")
        scenario = self._scenario(spec.get("world", {}).get("id"))
        if chemistry_world:
            lineage_summary = (
                f"这套系统保留了{previous['organism_name']}的{inherited_phrase}，"
                f"在{environment['title']}中，经由“{contingency['title']}”逐步走向{direction['title']}。"
            )
            change_summary = f"反应系统没有被提前写成生命；能稳定{direction['mechanism_hint']}的结构在多次循环中延续得更久。"
            uncertainty_note = "这是浏览器验收用的离线样例；生命起源仍有多种竞争假说，真实运行会由 Step 严格结构化生成。"
        else:
            lineage_summary = (
                f"它保留了{previous['organism_name']}的{inherited_phrase}，"
                f"在{environment['title']}中，经由“{contingency['title']}”逐步走向{direction['title']}。"
            )
            change_summary = f"谱系没有突然变身；能稳定{direction['mechanism_hint']}的后代在多代中留下得更多。"
            uncertainty_note = (
                "这是浏览器验收用的离线样例。未来形态只是受约束的假说，不能当作预测。"
                if constraint_mode == "future_scenario"
                else "这是浏览器验收用的离线样例；历史路线受场景证据和关键性状账本约束。"
            )
        historical_reference = (
            spec.get("historical_reference")
            if isinstance(spec.get("historical_reference"), dict)
            else {}
        )
        historical_external = [
            str(item)
            for item in historical_reference.get("required_external_traits", [])
            if str(item).strip()
        ]
        historical_internal = [
            str(item)
            for item in historical_reference.get("required_internal_traits", [])
            if str(item).strip()
        ]
        if (
            historical_reference.get("status") == "historical_reference"
            and historical_external
            and historical_internal
        ):
            lineage_summary += (
                f" 真实类群参照要求它保留{historical_external[0]}，"
                f"内部仍有{historical_internal[0]}。"
            )
        result = {
            "organism_name": spec.get(
                "fixture_name",
                f"第 {selection['expected_round']} 轮测试阶段",
            ),
            "lineage_summary": lineage_summary,
            "change_summary": change_summary,
            "traits": list(
                dict.fromkeys(
                    [previous["traits"][0], direction["title"], environment["title"]]
                    + historical_external[:1]
                    + historical_internal[:1]
                )
            ),
            "internal_causes": [direction["mechanism_hint"]],
            "external_causes": [environment["description"], contingency["description"]],
            "benefits": [direction["tradeoff_hint"].split("，")[0]],
            "costs": [
                direction["tradeoff_hint"].split("，", 1)[1]
                if "，" in direction["tradeoff_hint"]
                else "新的结构需要持续投入能量维护"
            ],
            "evidence_tag": direction.get("evidence_tag", "MECHANISM_HYPOTHESIS"),
            "uncertainty_note": uncertainty_note,
            "image_prompt": (
                "A cinematic natural-history scientific illustration of one evolving lineage or chemical system. "
                + str(scenario.get("visual_anchor", ""))
            ),
        }
        return result, {
            "planner": "fixture",
            "reasoning_effort": "not_applicable",
            "strict_schema": True,
            "attempts": 0,
        }

    def _dry_run_render(
        self,
        result: dict[str, Any],
        previous: dict[str, Any],
        selection: dict[str, Any],
        destination: Path,
    ) -> dict[str, Any]:
        self._write_stage_svg(
            destination,
            selection["expected_round"],
            result["organism_name"],
            selection["direction"]["id"],
            previous.get("scenario_id", self.default_scenario_id),
        )
        return {"render_source": "fixture", "generator": "browser fixture", "seed": None}

    def _write_stage_svg(
        self,
        path: Path,
        round_no: int,
        title: str,
        variant: str,
        scenario_id: str | None = None,
    ) -> None:
        """Write an image-only biomorphic placeholder for fixture mode."""
        safe_title = html.escape(title)
        palette = [
            ("#2ec4b6", "#ffbf69", "#0b2430"),
            ("#55d6be", "#ff9f68", "#102c3b"),
            ("#7adfbb", "#f7c55a", "#153343"),
            ("#91d9e8", "#ff735f", "#162a40"),
        ][round_no]
        scenario_color = self._scenario(scenario_id).get("accent")
        colors = (str(scenario_color or palette[0]), palette[1], palette[2])
        satellites = "".join(
            f'<circle cx="{190 + index * 120}" cy="{260 + (index % 2) * 145}" r="{32 + round_no * 8}" fill="{colors[index % 2]}" opacity=".72"/>'
            for index in range(5 + round_no)
        )
        lobes = 8 + round_no * 3
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800" role="img" aria-label="{safe_title}">
<defs>
  <radialGradient id="sea"><stop stop-color="{colors[2]}"/><stop offset="1" stop-color="#040d13"/></radialGradient>
  <radialGradient id="body"><stop stop-color="{colors[1]}" stop-opacity=".96"/><stop offset=".58" stop-color="{colors[0]}" stop-opacity=".72"/><stop offset="1" stop-color="#d7fff5" stop-opacity=".18"/></radialGradient>
  <filter id="glow"><feGaussianBlur stdDeviation="18"/></filter>
</defs>
<rect width="1200" height="800" fill="url(#sea)"/>
<ellipse cx="610" cy="410" rx="330" ry="245" fill="{colors[0]}" opacity=".12" filter="url(#glow)"/>
{satellites}
<path d="M350 420 C390 {190-round_no*10}, 825 {180-round_no*8}, 875 400 C920 615, 710 685, 500 625 C360 585, 305 505, 350 420Z" fill="url(#body)" stroke="#d8fff5" stroke-opacity=".54" stroke-width="5"/>
<g fill="none" stroke="{colors[1]}" stroke-width="13" stroke-linecap="round" opacity=".88">
  {''.join(f'<path d="M610 405 Q {480 + (i%4)*85} {270 + (i//4)*70} {400 + (i%5)*105} {235 + (i%3)*150}"/>' for i in range(lobes))}
</g>
<circle cx="610" cy="410" r="78" fill="{colors[1]}" opacity=".88"/>
<circle cx="585" cy="385" r="24" fill="#fff6ca" opacity=".74"/>
<!-- fixture variant: {html.escape(variant)} -->
</svg>''',
            encoding="utf-8",
        )

    def asset_path(self, session_id: str, filename: str) -> Path:
        if not re.fullmatch(r"stage_[0-9]{2}(?:_origin)?\.(?:png|svg)", filename):
            raise InteractiveError("asset_not_found", "没有找到这张演化图片。", http_status=404)
        path = self._session_dir(session_id) / filename
        if not path.is_file():
            raise InteractiveError("asset_not_found", "没有找到这张演化图片。", http_status=404)
        return path
