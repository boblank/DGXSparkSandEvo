#!/usr/bin/env python3
"""Stateful, three-round EvoLab session engine.

The browser contract is intentionally smaller than the persisted session.  API
keys and absolute host paths never become part of either representation.
"""

from __future__ import annotations

import copy
import html
import importlib.util
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
CATALOG_FILE = SCRIPT_DIR / "interactive_catalog.json"
SCHEMA_FILE = SCRIPT_DIR / "interactive_schema.json"
WORKFLOW_FILE = SCRIPT_DIR / "creature_workflow.json"
KNOWLEDGE_CARDS_FILE = SCRIPT_DIR / "knowledge_cards.json"
CURATED_SOURCES_FILE = REPO_ROOT / "knowledge" / "sources.json"
ORIGIN_IMAGE_FILE = REPO_ROOT / "demo-assets" / "interactive" / "origin.png"

STEP_ENDPOINT = "https://api.stepfun.com/step_plan/v1/chat/completions"
STEP_MODEL = "step-3.7-flash"
SESSION_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}-[a-f0-9]{8}$")
SAFE_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


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


def _as_choice_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _public_source(source: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(source.get(key, ""))
        for key in ("source_id", "title", "url", "supports", "boundary")
    }


class InteractiveEvolutionService:
    """Create, advance, and persist three-round evolution sessions."""

    def __init__(
        self,
        data_root: Path,
        *,
        dry_run: bool = False,
        planner: Planner | None = None,
        renderer: Renderer | None = None,
        step_timeout: int = 240,
        comfy_timeout: int = 900,
    ) -> None:
        self.data_root = data_root.expanduser().resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.dry_run = dry_run
        self.catalog = _read_json(CATALOG_FILE)
        self.schema = _read_json(SCHEMA_FILE)
        self.step_timeout = step_timeout
        self.comfy_timeout = comfy_timeout
        self._planner = planner or self._plan_with_step
        self._renderer = renderer or self._render_with_comfy
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._cards, self._sources = self._load_knowledge()

    @property
    def contract_version(self) -> str:
        return str(self.catalog["contract_version"])

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
            raise InteractiveError("session_not_found", "没有找到这条演化谱系。", http_status=404)
        return self.data_root / session_id

    def _load_session(self, session_id: str) -> dict[str, Any]:
        manifest = self._session_dir(session_id) / "session.json"
        if not manifest.is_file():
            raise InteractiveError("session_not_found", "没有找到这条演化谱系。", http_status=404)
        try:
            session = _read_json(manifest)
        except (OSError, json.JSONDecodeError) as exc:
            raise InteractiveError(
                "session_unavailable",
                "这条谱系的记录暂时无法读取。",
                http_status=500,
                retryable=True,
            ) from exc
        if session.get("session_id") != session_id:
            raise InteractiveError("session_unavailable", "这条谱系的记录不完整。", http_status=500)
        return session

    def _write_session(self, session: dict[str, Any]) -> None:
        _atomic_write_json(
            self._session_dir(session["session_id"]) / "session.json",
            session,
        )

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
            return {
                "status": "matched",
                "match_scope": match_scope,
                "knowledge_card_id": knowledge_card_id,
                "title": card["title"],
                "summary": card["body"],
                "boundary": card.get("boundary", "这是机制解释，不是对生成物种的实证认定。"),
                "sources": [_public_source(source) for source in card_sources],
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
                "下面展示的是按环境压力、遗传差异和生存代价推出来的情景，不是已经发现的物种。"
            ),
            "boundary": "只把它当作受约束的演化假说，不当作确定预测。",
            "sources": [],
            "context_sources": [_public_source(source) for source in transition_sources],
            "generated_outcome_status": "no_match",
            "generated_outcome_reason": "知识库没有与这次形态变化一一对应的已知节点。",
        }

    def _round_spec(self, round_no: int) -> dict[str, Any]:
        try:
            return self.catalog["rounds"][str(round_no)]
        except KeyError as exc:
            raise InteractiveError("session_completed", "这条谱系已经走完三轮演化。", http_status=409) from exc

    def _effective_round_spec(
        self,
        session: dict[str, Any],
        round_no: int,
    ) -> dict[str, Any]:
        """Filter choices that would contradict the branch already taken."""
        spec = copy.deepcopy(self._round_spec(round_no))
        parent_selection = (session.get("current_stage") or {}).get("selection") or {}
        parent_direction_id = (parent_selection.get("direction") or {}).get("id")
        if not parent_direction_id:
            return spec
        spec["directions"] = [
            direction
            for direction in spec["directions"]
            if not direction.get("allowed_parent_direction_ids")
            or parent_direction_id in direction["allowed_parent_direction_ids"]
        ]
        return spec

    def _choices_for(self, session: dict[str, Any]) -> dict[str, Any]:
        next_round = int(session["round_index"]) + 1
        if next_round > 3:
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

    def public_envelope(self, session: dict[str, Any]) -> dict[str, Any]:
        public_session = {
            key: copy.deepcopy(session[key])
            for key in (
                "contract_version",
                "session_id",
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
        return {
            "session": public_session,
            "choices": self._choices_for(session),
        }

    def create_session(self) -> dict[str, Any]:
        session_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        session_dir = self.data_root / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        initial = copy.deepcopy(self.catalog["initial_stage"])
        if ORIGIN_IMAGE_FILE.is_file():
            filename = "stage_00_origin.png"
            shutil.copy2(ORIGIN_IMAGE_FILE, session_dir / filename)
            origin_source = "curated_origin"
        else:
            filename = "stage_00_origin.svg"
            self._write_stage_svg(session_dir / filename, 0, initial["organism_name"], "origin")
            origin_source = "illustrated_start"
        initial.update(
            {
                "round": 0,
                "image_url": f"/api/assets/{session_id}/{filename}",
                "render_source": origin_source,
                "selection": None,
                "knowledge_match": self._knowledge_match("NONE", initial["transition_id"]),
            }
        )
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        session = {
            "contract_version": self.contract_version,
            "session_id": session_id,
            "status": "ready",
            "round_index": 0,
            "max_rounds": 3,
            "current_stage": initial,
            "history": [initial],
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
            if next_round > 3:
                raise InteractiveError("session_completed", "这条谱系已经走完三轮演化。", http_status=409)
            if session["status"] == "generating":
                raise InteractiveError(
                    "session_busy",
                    "这一轮还在生成，请稍等片刻。",
                    http_status=409,
                    retryable=True,
                )
            spec = self._effective_round_spec(session, next_round)
            selection = self._validate_selection(spec, payload)
            if selection["expected_round"] != next_round:
                raise InteractiveError(
                    "round_conflict",
                    "谱系已经向前走了一步，请刷新后再选。",
                    http_status=409,
                )

            session["status"] = "generating"
            session["last_error"] = None
            session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._write_session(session)
            previous = copy.deepcopy(session["current_stage"])
            try:
                if self.dry_run:
                    result, metadata = self._dry_run_plan(previous, selection, spec)
                else:
                    result, metadata = self._planner(previous, selection, spec)
                stage = self._build_stage(
                    next_round,
                    previous,
                    result,
                    selection,
                    spec,
                    metadata,
                )
                destination = self._session_dir(session_id) / f"stage_{next_round:02d}.png"
                if self.dry_run:
                    destination = destination.with_suffix(".svg")
                    render_metadata = self._dry_run_render(result, previous, selection, destination)
                else:
                    render_metadata = self._renderer(result, previous, selection, destination)
                stage["image_url"] = f"/api/assets/{session_id}/{destination.name}"
                stage["render_source"] = render_metadata["render_source"]
                stage["render_metadata"] = {
                    "generator": render_metadata["generator"],
                    "seed": render_metadata.get("seed"),
                }
            except InteractiveError as exc:
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
                session["status"] = "error"
                session["last_error"] = {
                    "code": "generation_failed",
                    "message": "这次演化没有生成成功。原来的谱系还在，可以直接重试。",
                    "retryable": True,
                }
                session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._write_session(session)
                raise InteractiveError(
                    "generation_failed",
                    "这次演化没有生成成功。原来的谱系还在，可以直接重试。",
                    http_status=502,
                    retryable=True,
                ) from exc

            session["round_index"] = next_round
            session["current_stage"] = stage
            session["history"].append(stage)
            session["status"] = "completed" if next_round == 3 else "ready"
            session["last_error"] = None
            session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._write_session(session)
            return self.public_envelope(session)

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
        evidence_tag = (
            "SCENARIO_EXTRAPOLATION"
            if round_no == 3
            else "KNOWN_MECHANISM"
            if direction["knowledge_card_id"] in self._cards
            else "MECHANISM_HYPOTHESIS"
        )
        inherited_traits = list(previous.get("traits", []))[:2]
        traits = list(dict.fromkeys(inherited_traits + result["traits"]))[:6]
        return {
            "round": round_no,
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
                    for key in ("id", "title", "description", "tradeoff_hint", "transition_id")
                },
            },
            "knowledge_match": self._knowledge_match(
                (
                    selection["environment"].get("knowledge_card_id", "NONE")
                    if round_no == 3
                    else direction["knowledge_card_id"]
                ),
                direction["transition_id"],
                match_scope="external_pressure" if round_no == 3 else "transition",
            ),
            "model": {
                "planner": metadata["planner"],
                "reasoning_effort": metadata["reasoning_effort"],
                "strict_schema": bool(metadata["strict_schema"]),
                "attempts": int(metadata.get("attempts", 1)),
            },
        }

    def _messages(
        self,
        previous: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
        retry_errors: list[str] | None = None,
    ) -> list[dict[str, str]]:
        direction = selection["direction"]
        retry_note = ""
        if retry_errors:
            retry_note = "\n上次输出未通过结构校验，请只修复这些问题：" + "；".join(retry_errors[:10])
        system = (
            "你是 EvoLab 的演化生物学情景规划器。你描述的是跨多代选择，不是一个个体主动变形。"
            "必须继承上一阶段的谱系与至少一项可辨识特征，再叠加本轮环境、偶发事件和用户选择的方向。"
            "允许被本轮机制改变的旧性状发生转化，禁止把已经被替代的旧性状机械照搬。"
            "收益和代价必须同时出现，禁止目的论、必然论和没有来源的精确适合度数值。"
            "深时节点可以解释已知机制；未来阶段必须明确是受约束的情景推演，不是确定预测。"
            "image_prompt 必须使用英文，描述一张无文字、无标签、单一主体明确的电影感科学插画，"
            "并保留上一阶段的青绿、琥珀、半透明生物材质等视觉连续性。"
            "其余字段使用自然、简洁的简体中文。只返回符合指定 JSON Schema 的 JSON。"
        )
        user = (
            f"当前章节：{spec['chapter']}\n"
            f"上一阶段名称：{previous['organism_name']}\n"
            f"上一阶段谱系：{previous['lineage_summary']}\n"
            f"上一阶段可供继承或转化的性状：{'；'.join(previous['traits'])}\n"
            f"本轮环境：{selection['environment']['title']}——{selection['environment']['description']}\n"
            f"偶发事件：{selection['contingency']['title']}——{selection['contingency']['description']}\n"
            f"演化方向：{direction['title']}——{direction['description']}\n"
            f"机制约束：{direction['mechanism_hint']}\n"
            f"已知权衡：{direction['tradeoff_hint']}\n"
            "请生成下一阶段。lineage_summary 必须明确它继承了什么、改变了什么；"
            "traits 至少保留一项上一阶段可辨认特征，再加入本轮新特征。"
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
                "model": STEP_MODEL,
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
                    STEP_ENDPOINT,
                    body,
                    {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    self.step_timeout,
                )
                if envelope.get("model") != STEP_MODEL:
                    raise ValueError("unexpected model")
                result = json.loads(envelope["choices"][0]["message"]["content"])
            except InteractiveError:
                raise
            except Exception as exc:
                raise InteractiveError(
                    "planner_unavailable",
                    "演化规划服务暂时没有响应。原来的谱系还在，可以稍后重试。",
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
            if not errors:
                return result, {
                    "planner": STEP_MODEL,
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
            "演化规划没有通过结构校验。原来的谱系还在，可以重试。",
            http_status=502,
            retryable=True,
        )

    def _render_with_comfy(
        self,
        result: dict[str, Any],
        previous: dict[str, Any],
        selection: dict[str, Any],
        destination: Path,
    ) -> dict[str, Any]:
        workflow = copy.deepcopy(_read_json(WORKFLOW_FILE))
        previous_traits = ", ".join(str(item) for item in previous.get("traits", [])[:4])
        environment = selection["environment"]
        direction = selection["direction"]
        continuity = (
            " This is the immediate descendant of the previous lineage, not a new unrelated species."
            f" Previous lineage: {previous.get('organism_name', 'unnamed lineage')}."
            f" Preserve recognizable inherited features: {previous_traits}."
            f" New environmental pressure: {environment['title']} — {environment['description']}"
            f" Chosen evolutionary direction: {direction['title']} — {direction['description']}"
            " Show a gradual biological modification at the same approximate scale and in the same aquatic lineage."
            " Do not jump to a flower, terrestrial plant, coral, anemone, tree, beach animal, or unrelated macroscopic body plan"
            " unless the chosen transition explicitly introduces that capability."
            " Keep translucent teal membranes, amber metabolic structures, and cinematic natural-history documentary lighting."
            " Show the environment and adaptation as one coherent scene, with no comparison grid, text, letters, labels, or watermark."
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
        workflow["2"]["inputs"]["text"] = result["image_prompt"] + continuity
        workflow["3"]["inputs"]["text"] = (
            "flower, petals, terrestrial plant, tree, coral, anemone, moss, unrelated species, "
            + workflow["3"]["inputs"]["text"]
        )
        if selection["direction"]["id"] in {"multicellular_body", "cooperative_colony"}:
            workflow["3"]["inputs"]["text"] = workflow["3"]["inputs"]["text"].replace(
                "duplicated organism, ", ""
            )
        seed = int(uuid.uuid4().hex[:8], 16)
        workflow["6"]["inputs"]["seed"] = seed
        workflow["8"]["inputs"]["filename_prefix"] = (
            f"interactive_{destination.parent.name}_{destination.stem}"
        )
        if os.environ.get("EVOLAB_UNLOAD_OLLAMA", "1") != "0":
            helper.unload_ollama()
        comfy_url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:7000")
        prompt_id = helper.submit_comfy_prompt(workflow, comfy_url)
        outputs = helper.wait_for_comfy(prompt_id, comfy_url, self.comfy_timeout)
        default_output = (
            Path(os.environ.get("WORKSHOP_DIR", "/home/Developer/build_a_claw_workshop-bundle"))
            / "comfyui-app"
            / "ComfyUI"
            / "output"
        )
        comfy_output = Path(os.environ.get("COMFY_OUTPUT_DIR", str(default_output)))
        source = helper.comfy_output_path(outputs, comfy_output)
        if not source.is_file():
            raise InteractiveError(
                "renderer_unavailable",
                "图片生成完成了，但产物没有成功保存。可以直接重试这一轮。",
                http_status=502,
                retryable=True,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return {"render_source": "generated", "generator": "FLUX via ComfyUI", "seed": seed}

    def _dry_run_plan(
        self,
        previous: dict[str, Any],
        selection: dict[str, Any],
        spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        direction = selection["direction"]
        environment = selection["environment"]
        contingency = selection["contingency"]
        result = {
            "organism_name": {
                1: "内潮共生细胞",
                2: "潮纹协作体",
                3: "暖潮迁游体",
            }[selection["expected_round"]],
            "lineage_summary": (
                f"它保留了{previous['organism_name']}的青绿膜与琥珀色能量结构，"
                f"在{environment['title']}中，经由“{contingency['title']}”逐步走向{direction['title']}。"
            ),
            "change_summary": f"谱系没有突然变身；能稳定{direction['mechanism_hint']}的后代在多代中留下得更多。",
            "traits": [previous["traits"][0], direction["title"], environment["title"]],
            "internal_causes": [direction["mechanism_hint"]],
            "external_causes": [environment["description"], contingency["description"]],
            "benefits": [direction["tradeoff_hint"].split("，")[0]],
            "costs": [
                direction["tradeoff_hint"].split("，", 1)[1]
                if "，" in direction["tradeoff_hint"]
                else "新的结构需要持续投入能量维护"
            ],
            "evidence_tag": "SCENARIO_EXTRAPOLATION" if selection["expected_round"] == 3 else "MECHANISM_HYPOTHESIS",
            "uncertainty_note": (
                "这是浏览器验收用的离线样例。未来形态只是受约束的假说。"
                if selection["expected_round"] == 3
                else "这是浏览器验收用的离线样例；真实运行会由 Step 严格结构化生成。"
            ),
            "image_prompt": (
                "A cinematic scientific illustration of one evolving translucent marine lineage, "
                "teal membranes and amber organelles, clearly adapted to a changing shallow-sea environment"
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
        )
        return {"render_source": "fixture", "generator": "browser fixture", "seed": None}

    def _write_stage_svg(self, path: Path, round_no: int, title: str, variant: str) -> None:
        """Write an image-only biomorphic placeholder for fixture mode."""
        safe_title = html.escape(title)
        colors = [
            ("#2ec4b6", "#ffbf69", "#0b2430"),
            ("#55d6be", "#ff9f68", "#102c3b"),
            ("#7adfbb", "#f7c55a", "#153343"),
            ("#91d9e8", "#ff735f", "#162a40"),
        ][round_no]
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
