#!/usr/bin/env python3
"""EvoLab three-stage planner, validator, renderer, and storyboard builder."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_FILE = SCRIPT_DIR / "evolution_schema.json"
RULES_FILE = SCRIPT_DIR / "evolution_rules.json"
KNOWLEDGE_FILE = SCRIPT_DIR / "knowledge_cards.json"


def _load_rendering() -> Any:
    module_name = "evolution_storyboard_rendering"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_DIR / "rendering.py")
    if not spec or not spec.loader:
        raise RuntimeError("cannot load evolution renderer profiles")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


rendering = _load_rendering()

STEP_ENDPOINT = "https://api.stepfun.com/step_plan/v1/chat/completions"
STEP_MODEL = "step-3.7-flash"
DEFAULT_SCENARIO = (
    "让一种生活在浅海的微型祖先，经历真核化和简单多细胞化后，"
    "在氧气持续下降、捕食压力上升的未来环境中继续演化。"
    "生成三条不同路线，并选出最合理的一条。"
)

STAGE_ORDER = ["eukaryogenesis", "multicellularity", "future_route"]
TRANSITION_ORDER = ["M02", "M05", "M12"]
ROUTE_IDS = ["energy_saver", "defense_ambush", "group_cooperation"]
STRATEGY_SLOTS = ["ENERGY_SAVER", "DEFENSE_AMBUSH", "GROUP_COOPERATION"]
ROUTE_SLOT_BY_ID = dict(zip(ROUTE_IDS, STRATEGY_SLOTS))
DEMO_CONFLICT_TRAITS = [
    "HEAVY_ARMOR",
    "EXTREMELY_HIGH_SWIM_SPEED",
    "EXTREMELY_LOW_ENERGY_USE",
]
PSEUDO_PRECISION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:[-–至]\s*\d+(?:\.\d+)?)?\s*"
    r"(?:%|％|倍|mg/L|毫克|个细胞|年|天|小时)"
)
STAGE_FILENAMES = {
    "eukaryogenesis": "stage_01_eukaryogenesis.png",
    "multicellularity": "stage_02_multicellularity.png",
    "future_route": "stage_03_future_route.png",
}
FIXED_CACHE_STAGES = {"eukaryogenesis", "multicellularity"}


class EvolutionError(RuntimeError):
    """Expected, user-safe pipeline error."""


def log(message: str) -> None:
    print(f"[evolution] {message}", file=sys.stderr, flush=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise EvolutionError(f"HTTP {exc.code}: {error_text[:500]}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise EvolutionError(f"request failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise EvolutionError("response root is not an object")
    return payload


def get_json(url: str, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        raise EvolutionError(f"GET failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise EvolutionError("GET response root is not an object")
    return payload


def validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON-Schema subset used by evolution_schema.json."""
    errors: list[str] = []
    expected_type = schema.get("type")
    type_ok = True
    if expected_type == "object":
        type_ok = isinstance(instance, dict)
    elif expected_type == "array":
        type_ok = isinstance(instance, list)
    elif expected_type == "string":
        type_ok = isinstance(instance, str)
    elif expected_type == "integer":
        type_ok = isinstance(instance, int) and not isinstance(instance, bool)
    elif expected_type == "number":
        type_ok = isinstance(instance, (int, float)) and not isinstance(instance, bool)
    elif expected_type == "boolean":
        type_ok = isinstance(instance, bool)
    if expected_type and not type_ok:
        return [f"{path}: expected {expected_type}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = required - set(instance)
        if missing:
            errors.append(f"{path}: missing {sorted(missing)}")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(properties)
            if extra:
                errors.append(f"{path}: extra {sorted(extra)}")
        for key, value in instance.items():
            child_schema = properties.get(key)
            if child_schema:
                errors.extend(validate_schema(value, child_schema, f"{path}.{key}"))

    if isinstance(instance, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(instance) < minimum:
            errors.append(f"{path}: fewer than {minimum} items")
        if maximum is not None and len(instance) > maximum:
            errors.append(f"{path}: more than {maximum} items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(set(canonical)) != len(canonical):
                errors.append(f"{path}: duplicate items")
        child_schema = schema.get("items")
        if child_schema:
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, child_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        minimum = schema.get("minLength")
        if minimum is not None and len(instance) < minimum:
            errors.append(f"{path}: string shorter than {minimum}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            errors.append(f"{path}: number below {minimum}")
    return errors


def semantic_errors(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    stages = plan.get("story_stages", [])
    routes = plan.get("routes", [])

    if [stage.get("stage_id") for stage in stages] != STAGE_ORDER:
        errors.append("story_stages must use the fixed order")
    if [stage.get("transition_id") for stage in stages] != TRANSITION_ORDER:
        errors.append("story_stages transition order mismatch")
    if {route.get("route_id") for route in routes} != set(ROUTE_IDS):
        errors.append("route IDs must be the exact fixed set")
    if {route.get("strategy_slot") for route in routes} != set(STRATEGY_SLOTS):
        errors.append("strategy slots must be the exact fixed set")
    for route in routes:
        route_id = route.get("route_id")
        if route_id in ROUTE_SLOT_BY_ID and route.get("strategy_slot") != ROUTE_SLOT_BY_ID[route_id]:
            errors.append(f"{route_id}: strategy slot mismatch")

    selected = plan.get("selected_route_id")
    if selected not in ROUTE_IDS:
        errors.append("selected_route_id is invalid")
    if len(stages) == 3 and stages[2].get("route_reference_id") != selected:
        errors.append("future stage must reference selected_route_id")

    pressures = set((plan.get("scenario") or {}).get("environment_pressures", []))
    if pressures != {"DECLINING_OXYGEN", "INCREASED_PREDATION"}:
        errors.append("fixed environment pressure set mismatch")

    scenario_event = (plan.get("scenario") or {}).get("contingency_event")
    stage_events = [
        stage.get("contingency_event")
        for stage in stages
        if stage.get("contingency_event") != "NONE"
    ]
    if len(stage_events) != 1 or stage_events[0] != scenario_event:
        errors.append("exactly one stage must carry the scenario contingency event")

    expected_stage_fields = [
        ("eukaryogenesis", "M02", "DEEP_TIME_HISTORY", "ENDOSYMBIOSIS", "NONE"),
        (
            "multicellularity",
            "M05",
            "MULTIGENERATIONAL_TRANSITION",
            "MULTICELLULARITY",
            "NONE",
        ),
    ]
    for index, expected in enumerate(expected_stage_fields):
        if len(stages) <= index:
            continue
        stage = stages[index]
        actual = (
            stage.get("stage_id"),
            stage.get("transition_id"),
            stage.get("time_scope"),
            stage.get("knowledge_card_id"),
            stage.get("route_reference_id"),
        )
        if actual != expected:
            errors.append(f"stage {index + 1} fixed contract mismatch")
    if len(stages) == 3:
        final_stage = stages[2]
        if final_stage.get("evidence_tag") != "SCENARIO_EXTRAPOLATION":
            errors.append("future stage must be SCENARIO_EXTRAPOLATION")

    for route in routes:
        trait_ids = route.get("trait_ids", [])
        if len(trait_ids) != len(set(trait_ids)):
            errors.append(f"{route.get('route_id')}: duplicate trait IDs")
        if not route.get("benefits") or not route.get("costs"):
            errors.append(f"{route.get('route_id')}: benefit/cost required")
    for path, text_value in public_narrative_strings(plan):
        if PSEUDO_PRECISION_PATTERN.search(text_value):
            errors.append(f"{path}: unsupported pseudo-precision")
    return errors


def public_narrative_strings(plan: dict[str, Any]) -> list[tuple[str, str]]:
    """Return user-visible scientific claims while excluding prompts and IDs."""
    strings: list[tuple[str, str]] = []
    scenario = plan.get("scenario") or {}
    for key in ("user_prompt", "starting_lineage"):
        value = scenario.get(key)
        if isinstance(value, str):
            strings.append((f"scenario.{key}", value))
    for index, stage in enumerate(plan.get("story_stages", [])):
        for key in (
            "title",
            "before_state",
            "after_state",
            "lineage_anchor",
            "internal_drivers",
            "external_drivers",
            "benefits",
            "costs",
        ):
            value = stage.get(key)
            values = value if isinstance(value, list) else [value]
            strings.extend(
                (f"story_stages[{index}].{key}", item)
                for item in values
                if isinstance(item, str)
            )
    for index, route in enumerate(plan.get("routes", [])):
        for key in ("title", "description", "benefits", "costs"):
            value = route.get(key)
            values = value if isinstance(value, list) else [value]
            strings.extend(
                (f"routes[{index}].{key}", item)
                for item in values
                if isinstance(item, str)
            )
    summary = plan.get("decision_summary")
    if isinstance(summary, str):
        strings.append(("decision_summary", summary))
    return strings


def _stable_unique(values: list[Any]) -> list[Any]:
    """Remove duplicate scalar values while preserving their first-seen order."""
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def normalize_plan_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize deterministic contract fields before semantic validation.

    Strict JSON Schema guarantees the shape and vocabulary, but it cannot express
    positional array contracts or cross-field references. Those are application
    rules, so the helper—not the language model—owns them.
    """
    scenario = plan.get("scenario")
    if isinstance(scenario, dict):
        scenario["starting_lineage"] = "浅海微型原核宿主—细菌共生生态"
        pressures = scenario.get("environment_pressures")
        if isinstance(pressures, list) and set(pressures) == {
            "DECLINING_OXYGEN",
            "INCREASED_PREDATION",
        }:
            scenario["environment_pressures"] = [
                "DECLINING_OXYGEN",
                "INCREASED_PREDATION",
            ]

    routes = plan.get("routes")
    if isinstance(routes, list):
        for route in routes:
            if isinstance(route, dict) and isinstance(route.get("trait_ids"), list):
                route["trait_ids"] = _stable_unique(route["trait_ids"])
        if len(routes) == len(ROUTE_IDS) and {
            route.get("route_id") for route in routes if isinstance(route, dict)
        } == set(ROUTE_IDS):
            routes.sort(key=lambda route: ROUTE_IDS.index(route["route_id"]))
            route_templates = {
                "energy_saver": {
                    "strategy_slot": "ENERGY_SAVER",
                    "title": "节能适应路线",
                    "description": "降低代谢和持续游速，提高有限氧气下的能量利用效率。",
                    "trait_ids": [
                        "LOW_METABOLISM",
                        "EFFICIENT_OXYGEN_USE",
                        "REDUCED_SUSTAINED_SPEED",
                    ],
                    "benefits": [
                        "较低能量需求有利于在持续低氧环境中维持基本活动",
                        "较高氧利用效率可缓冲资源供给不足",
                    ],
                    "costs": [
                        "持续运动和主动觅食能力受限",
                        "面对快速捕食者时逃逸能力较弱",
                    ],
                },
                "defense_ambush": {
                    "strategy_slot": "DEFENSE_AMBUSH",
                    "title": "防御伏击路线",
                    "description": "初始候选尝试同时采用厚重装甲、极高游速和极低能耗。",
                    "trait_ids": list(DEMO_CONFLICT_TRAITS),
                    "benefits": ["防护和速度可降低部分捕食风险"],
                    "costs": ["结构、运动和能量约束存在冲突，必须由规则修订"],
                },
                "group_cooperation": {
                    "strategy_slot": "GROUP_COOPERATION",
                    "title": "群体协作路线",
                    "description": "通过群聚、预警和初步分工共同应对低氧与捕食压力。",
                    "trait_ids": [
                        "GROUPING",
                        "EARLY_WARNING",
                        "ROLE_SPECIALIZATION",
                        "HIGH_SOCIAL_COOPERATION",
                    ],
                    "benefits": [
                        "协作预警和共同防御可分散个体被捕食风险",
                        "功能分工有助于在资源受限时协调能量使用",
                    ],
                    "costs": [
                        "群体内部会出现资源竞争与协调成本",
                        "个体自主性下降，协作失效会削弱整体稳定性",
                    ],
                },
            }
            for route in routes:
                route_id = route["route_id"]
                route.update(copy.deepcopy(route_templates[route_id]))

    stages = plan.get("story_stages")
    if isinstance(stages, list) and len(stages) == len(STAGE_ORDER) and {
        stage.get("stage_id") for stage in stages if isinstance(stage, dict)
    } == set(STAGE_ORDER):
        stages.sort(key=lambda stage: STAGE_ORDER.index(stage["stage_id"]))
        canonical_fields = {
            "eukaryogenesis": {
                "transition_id": "M02",
                "title": "真核化：内共生带来的复杂细胞",
                "time_scope": "DEEP_TIME_HISTORY",
                "before_state": "浅海中的原核宿主—共生伙伴系统",
                "after_state": "具有线粒体样能量系统的早期真核细胞",
                "lineage_anchor": "浅海、青绿与琥珀配色、半透明圆形细胞谱系",
                "internal_drivers": [
                    "宿主与细菌伙伴形成稳定共生",
                    "基因转移与膜系统逐步整合",
                ],
                "external_drivers": ["浅海微环境中的能量互补使稳定共生受益"],
                "benefits": ["更高效的能量供给支持更复杂的细胞结构"],
                "costs": ["宿主与共生伙伴相互依赖，并需协调复制与资源分配"],
                "route_reference_id": "NONE",
                "knowledge_card_id": "ENDOSYMBIOSIS",
                "evidence_tag": "KNOWN_MECHANISM",
            },
            "multicellularity": {
                "transition_id": "M05",
                "title": "多细胞化：从细胞群到初步分工",
                "time_scope": "MULTIGENERATIONAL_TRANSITION",
                "before_state": "具有线粒体样能量系统的早期真核细胞",
                "after_state": "具黏附、通讯和初步分工的简单多细胞群体",
                "lineage_anchor": "继承青绿与琥珀配色、半透明细胞膜和线粒体样结构",
                "internal_drivers": ["细胞黏附", "细胞间通讯", "初步功能分化"],
                "external_drivers": ["捕食与资源梯度使群体策略在部分环境中受益"],
                "benefits": ["群体结构能缓冲部分捕食并促进资源共享"],
                "costs": ["细胞自主性下降，并产生资源竞争和内部冲突"],
                "route_reference_id": "NONE",
                "knowledge_card_id": "MULTICELLULARITY",
                "evidence_tag": "KNOWN_MECHANISM",
            },
            "future_route": {
                "transition_id": "M12",
                "time_scope": "FUTURE_SCENARIO",
                "before_state": "具黏附、通讯和初步分工的简单多细胞群体",
                "lineage_anchor": "继承青绿与琥珀配色及简单多细胞谱系结构",
                "external_drivers": ["氧气持续下降", "捕食压力上升"],
                "route_reference_id": plan.get("selected_route_id"),
                "knowledge_card_id": "NONE",
                "evidence_tag": "SCENARIO_EXTRAPOLATION",
            },
        }
        for stage in stages:
            stage.update(canonical_fields[stage["stage_id"]])
            stage["contingency_event"] = "NONE"
        scenario_event = (scenario or {}).get("contingency_event")
        if scenario_event == "SYMBIOSIS_ESTABLISHED":
            stages[0]["contingency_event"] = scenario_event
        elif scenario_event:
            stages[2]["contingency_event"] = scenario_event
    return plan


def planner_messages(scenario: str, retry_errors: list[str] | None = None) -> list[dict[str, str]]:
    retry_note = ""
    if retry_errors:
        retry_note = "\n上次结果未通过本地校验，请修复这些问题：" + "；".join(retry_errors[:12])
    system = (
        "你是 EvoLab 的演化生物学情景规划器。输出是教学性重建和受约束的未来假说，"
        "不是科研预测。只输出符合指定 JSON Schema 的 JSON，不输出 Markdown 或思维过程。"
        "必须严格按 eukaryogenesis、multicellularity、future_route 三阶段顺序。"
        "Stage 1/2 是深时历史教学节点，低氧和高捕食只驱动 Stage 3。"
        "三路线固定为 ENERGY_SAVER、DEFENSE_AMBUSH、GROUP_COOPERATION，且都有收益和代价。"
        "为了展示确定性规则验证，DEFENSE_AMBUSH 的初始 trait_ids 必须包含 "
        "HEAVY_ARMOR、EXTREMELY_HIGH_SWIM_SPEED、EXTREMELY_LOW_ENERGY_USE；"
        "本地规则会把它修订为局部防护、伪装和短时爆发。"
        "visual_prompt 必须使用英文，采用统一的科学插画风格，禁止让图像模型绘制文字。"
        "除 visual_prompt 外，所有面向用户的标题、描述、收益、代价和总结必须使用简体中文。"
        "禁止给出没有数据来源的百分比、倍数、数量阈值、时间阈值或适合度数值；"
        "所有收益与代价只能做定性比较，例如较高、较低、增强、受限。"
    )
    user = (
        f"用户场景：{scenario}\n"
        "请生成完整三阶段计划、三个差异路线、最佳路线和简短 decision_summary。"
        "contingency_seed 使用非负整数；environment_pressures 必须精确包含 "
        "DECLINING_OXYGEN 与 INCREASED_PREDATION。"
        f"{retry_note}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_step_plan(scenario: str, schema: dict[str, Any], timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    key = os.environ.get("STEP_API_KEY") or os.environ.get("STEPFUN_API_KEY")
    if not key:
        raise EvolutionError("STEP_API_KEY is not set")
    retry_errors: list[str] | None = None
    last_error = "unknown validation error"
    for attempt in range(2):
        payload = {
            "model": STEP_MODEL,
            "reasoning_effort": "high",
            "messages": planner_messages(scenario, retry_errors),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "evolution_story_plan",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        envelope = post_json(
            STEP_ENDPOINT,
            payload,
            {
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            timeout,
        )
        try:
            actual_model = envelope.get("model")
            if actual_model != STEP_MODEL:
                raise EvolutionError(f"unexpected model {actual_model!r}")
            content = envelope["choices"][0]["message"]["content"]
            plan = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise EvolutionError("Step response content is not valid plan JSON") from exc
        normalize_plan_contract(plan)
        errors = validate_schema(plan, schema) + semantic_errors(plan)
        if not errors:
            metadata = {
                "planner": actual_model,
                "reasoning_effort": "high",
                "finish_reason": envelope["choices"][0].get("finish_reason"),
                "usage": envelope.get("usage", {}),
                "attempts": attempt + 1,
            }
            return plan, metadata
        retry_errors = errors
        last_error = "; ".join(errors[:12])
        if attempt == 0:
            log("Step plan validation failed on attempt 1; retrying safely")
        else:
            log("Step plan validation failed on final attempt")
    raise EvolutionError(f"Step plan failed validation: {last_error}")


def call_ollama_plan(scenario: str, schema: dict[str, Any], timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    body = {
        "model": os.environ.get("OLLAMA_MODEL", "qwen3.6:35b"),
        "messages": planner_messages(scenario),
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": 0,
    }
    envelope = post_json(
        f"{ollama_url}/api/chat",
        body,
        {"Content-Type": "application/json"},
        timeout,
    )
    try:
        plan = json.loads(envelope["message"]["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise EvolutionError("Ollama response content is not valid plan JSON") from exc
    normalize_plan_contract(plan)
    errors = validate_schema(plan, schema) + semantic_errors(plan)
    if errors:
        raise EvolutionError("Ollama plan failed validation: " + "; ".join(errors[:12]))
    return plan, {
        "planner": body["model"],
        "reasoning_effort": "local_fallback",
        "finish_reason": "stop",
        "usage": {},
        "attempts": 1,
    }


def load_plan_file(path: Path, schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    loaded = read_json(path)
    plan = {
        key: loaded[key]
        for key in (
            "scenario",
            "story_stages",
            "routes",
            "selected_route_id",
            "decision_summary",
        )
        if key in loaded
    }
    for stage in plan.get("story_stages", []):
        for runtime_key in ("status", "render_source", "image_path", "visual_review_status"):
            stage.pop(runtime_key, None)
    errors = validate_schema(plan, schema) + semantic_errors(plan)
    if errors:
        raise EvolutionError("plan file failed validation: " + "; ".join(errors[:12]))
    return plan, {
        "planner": "fixture",
        "reasoning_effort": "not_applicable",
        "finish_reason": "fixture",
        "usage": {},
        "attempts": 0,
        "fixture_rejections": copy.deepcopy(loaded.get("rejections", [])),
    }


def apply_deterministic_rules(
    plan: dict[str, Any],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    rejections: list[dict[str, Any]] = []
    for route in plan["routes"]:
        traits = list(route["trait_ids"])
        trait_set = set(traits)
        for rule in rules.get("forbidden_trait_sets", []):
            required = set(rule.get("trait_ids", []))
            if rule.get("match") != "ALL" or not required.issubset(trait_set):
                continue
            revision = rule["deterministic_revision"]
            removed = list(revision.get("remove_trait_ids", []))
            added = list(revision.get("add_trait_ids", []))
            traits = [trait for trait in traits if trait not in set(removed)]
            for trait in added:
                if trait not in traits:
                    traits.append(trait)
            route["trait_ids"] = traits
            summary = revision.get("summary", "")
            if summary and summary not in route["description"]:
                route["description"] = route["description"].rstrip("。") + "；" + summary
            if rule["rule_id"] == "TRAIT_CONFLICT_HEAVY_FAST_CHEAP":
                route["benefits"] = [
                    "局部防护与伪装可降低被捕食风险",
                    "短时爆发能力可支持伏击或紧急规避",
                ]
                route["costs"] = [
                    "局部矿化和爆发运动仍需要额外能量投入",
                    "持续游速受限，爆发后需要恢复",
                ]
            rejections.append(
                {
                    "rule_id": rule["rule_id"],
                    "route_id": route["route_id"],
                    "rejected_combination": sorted(required),
                    "reason": rule["reason"],
                    "revision": {
                        "removed_trait_ids": removed,
                        "added_trait_ids": added,
                        "summary": summary,
                    },
                }
            )
            trait_set = set(traits)
    if not any(item["rule_id"] == "TRAIT_CONFLICT_HEAVY_FAST_CHEAP" for item in rejections):
        raise EvolutionError("required visible rejection was not triggered")
    selected = plan["selected_route_id"]
    final_stage = plan["story_stages"][2]
    selected_route = next(route for route in plan["routes"] if route["route_id"] == selected)
    final_stage["route_reference_id"] = selected
    final_stage["title"] = "未来路线：" + selected_route["title"]
    final_stage["after_state"] = selected_route["description"]
    final_stage["internal_drivers"] = [
        "可遗传的群聚、预警和分工差异在多代选择中积累"
        if selected == "group_cooperation"
        else "可遗传性状差异在多代选择中积累"
    ]
    final_stage["external_drivers"] = ["氧气持续下降", "捕食压力上升"]
    final_stage["benefits"] = list(selected_route["benefits"])
    final_stage["costs"] = list(selected_route["costs"])
    plan["decision_summary"] = {
        "energy_saver": "节能路线最能缓冲持续低氧，但必须接受运动和逃逸能力下降。",
        "defense_ambush": "修订后的防御伏击路线在捕食压力下保留短时反应能力，同时承担能量与持续速度代价。",
        "group_cooperation": "群体协作路线同时回应低氧与捕食压力，但其稳定性依赖持续协调并承担内部竞争成本。",
    }[selected]
    visible_traits = ", ".join(
        trait.lower().replace("_", " ") for trait in selected_route["trait_ids"]
    )
    subject = (
        "six clearly separate small related organisms with visible gaps between their "
        "bodies, moving together as one coordinated school; multiple countable bodies, "
        "not one merged animal"
        if selected == "group_cooperation"
        else "a single coherent future descendant"
    )
    final_stage["visual_prompt"] = (
        subject
        + " expressing the selected "
        + selected_route["strategy_slot"].lower().replace("_", " ")
        + " evolutionary strategy, visible traits: "
        + visible_traits
        + ". Show one selected evolutionary outcome with inherited multicellular "
        "lineage continuity, not three alternatives and not a comparison grid."
    )
    return rejections


def build_knowledge_payload(knowledge: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cards: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for card in knowledge.get("cards", []):
        card_ids = list(card.get("source_ids", []))
        source_ids.update(card_ids)
        cards.append(
            {
                "knowledge_card_id": card["knowledge_card_id"],
                "title": card["title"],
                "body": card["body"],
                "content_labels": card.get("content_labels", []),
                "source_ids": card_ids,
            }
        )
    sources = [
        source
        for source in knowledge.get("sources", [])
        if source.get("source_id") in source_ids
    ]
    return cards, sources


def default_output_root() -> Path:
    openclaw_home = Path(
        os.environ.get(
            "OPENCLAW_HOME",
            "/home/Developer/build_a_claw_workshop-bundle/openclaw-home",
        )
    )
    workspace = openclaw_home / ".openclaw" / "workspace"
    if workspace.exists():
        return workspace / "outputs" / "evolution-runs"
    return Path.cwd() / "runs"


def make_manifest(
    run_id: str,
    plan: dict[str, Any],
    planner_metadata: dict[str, Any],
    rejections: list[dict[str, Any]],
    knowledge: dict[str, Any],
) -> dict[str, Any]:
    cards, sources = build_knowledge_payload(knowledge)
    stages = copy.deepcopy(plan["story_stages"])
    for stage in stages:
        stage.update(
            {
                "status": "pending",
                "render_source": "pending",
                "image_path": "",
                "visual_review_status": "not_run",
            }
        )
    return {
        "contract_version": "0.5.0",
        "run_id": run_id,
        "status": "validating",
        "current_stage": 0,
        "model": {
            "planner": planner_metadata["planner"],
            "reasoning_effort": planner_metadata["reasoning_effort"],
            "image_generator": "FLUX via ComfyUI",
        },
        "scenario": plan["scenario"],
        "story_stages": stages,
        "routes": plan["routes"],
        "selected_route_id": plan["selected_route_id"],
        "decision_summary": plan["decision_summary"],
        "rejections": rejections,
        "knowledge_cards": cards,
        "sources": sources,
        "image_paths": [],
        "storyboard_path": "",
        "fallbacks_used": [],
        "visual_review_status": "not_run",
        "planner_metadata": {
            "finish_reason": planner_metadata.get("finish_reason"),
            "attempts": planner_metadata.get("attempts", 1),
            "usage": planner_metadata.get("usage", {}),
        },
        "failure": None,
    }


def unload_ollama() -> None:
    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        post_json(
            f"{url}/api/generate",
            {
                "model": os.environ.get("OLLAMA_MODEL", "qwen3.6:35b"),
                "keep_alive": 0,
            },
            {"Content-Type": "application/json"},
            15,
        )
        log("Ollama model unloaded before FLUX rendering")
    except Exception as exc:
        log(f"Ollama unload skipped: {type(exc).__name__}")


def submit_comfy_prompt(workflow: dict[str, Any], comfy_url: str) -> str:
    response = post_json(
        f"{comfy_url.rstrip('/')}/api/prompt",
        {"prompt": workflow},
        {"Content-Type": "application/json"},
        30,
    )
    if response.get("error"):
        raise EvolutionError("ComfyUI rejected workflow")
    prompt_id = response.get("prompt_id")
    if not isinstance(prompt_id, str):
        raise EvolutionError("ComfyUI did not return prompt_id")
    return prompt_id


def wait_for_comfy(prompt_id: str, comfy_url: str, timeout: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            history = get_json(
                f"{comfy_url.rstrip('/')}/api/history/{prompt_id}",
                15,
            )
        except EvolutionError:
            continue
        if prompt_id not in history:
            continue
        entry = history[prompt_id]
        status = entry.get("status", {})
        for message in status.get("messages", []):
            if isinstance(message, list) and message and message[0] == "execution_error":
                raise EvolutionError("ComfyUI execution error")
        if status.get("completed") or status.get("status_str") == "success":
            return entry.get("outputs", {})
    raise EvolutionError(f"ComfyUI prompt timed out after {timeout}s")


def comfy_output_path(outputs: dict[str, Any], comfy_output: Path) -> Path:
    for node_output in outputs.values():
        for image in node_output.get("images", []) or []:
            filename = image.get("filename")
            if not filename:
                continue
            subfolder = image.get("subfolder") or ""
            return comfy_output / subfolder / filename if subfolder else comfy_output / filename
    raise EvolutionError("ComfyUI history contained no image")


def render_stage(
    stage: dict[str, Any],
    stage_index: int,
    renderer_ids: list[str],
    run_dir: Path,
    output_root: Path,
    comfy_url: str,
    comfy_output: Path,
    timeout: int,
    use_cache: bool,
) -> tuple[Path, dict[str, Any]]:
    filename = STAGE_FILENAMES[stage["stage_id"]]
    destination = run_dir / filename
    cache_file = output_root / "_cache" / renderer_ids[0] / filename
    if use_cache and stage["stage_id"] in FIXED_CACHE_STAGES and cache_file.is_file():
        shutil.copy2(cache_file, destination)
        _, profile = rendering.resolve_renderer(renderer_ids[0])
        return destination, {
            "render_source": "cached",
            "renderer": renderer_ids[0],
            "generator": profile["generator"],
            "seed": None,
            "duration_seconds": 0.0,
            "fallback_from": None,
        }

    continuity = (
        " consistent lineage anchor: "
        + stage["lineage_anchor"]
        + ". scientific evolutionary illustration, coherent teal and amber palette, "
        "realistic translucent biological materials, cinematic shallow-sea lighting, "
        "one clear focal composition, no text, no letters, no labels, no watermark."
    )
    negative_prompt = ""
    remove_negative_terms: tuple[str, ...] = ()
    if stage.get("route_reference_id") == "group_cooperation":
        remove_negative_terms = ("duplicated organism, ",)
    seed = int((stage_index + 1) * 1000003 + int(time.time() * 1000)) % (2**32 - 1)
    try:
        rendered = rendering.render_image_with_fallback(
            renderer_ids,
            prompt=stage["visual_prompt"] + continuity,
            negative_prompt=negative_prompt,
            seed=seed,
            filename_prefix=(
                f"evolution_{run_dir.name}_stage_{stage_index + 1:02d}_{stage['stage_id']}"
            ),
            destination=destination,
            comfy_url=comfy_url,
            comfy_output=comfy_output,
            timeout=timeout,
            submit_prompt=submit_comfy_prompt,
            wait_for_prompt=wait_for_comfy,
            locate_output=comfy_output_path,
            remove_negative_terms=remove_negative_terms,
            log=log,
        )
    except (rendering.RendererConfigurationError, rendering.RendererExhaustedError) as exc:
        raise EvolutionError("all configured image renderers failed") from exc
    if stage["stage_id"] in FIXED_CACHE_STAGES and rendered.fallback_from is None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, cache_file)
    return destination, {
        "render_source": "generated",
        "renderer": rendered.renderer,
        "generator": rendered.generator,
        "seed": rendered.seed,
        "duration_seconds": rendered.duration_seconds,
        "fallback_from": rendered.fallback_from,
    }


def find_font(size: int, bold: bool = False) -> Any:
    from PIL import ImageFont

    candidates = [
        (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        ),
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def wrap_by_width(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for character in str(text):
        candidate = current + character
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if current and width > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def draw_wrapped(
    draw: Any,
    position: tuple[int, int],
    text: str,
    font: Any,
    fill: str,
    max_width: int,
    line_gap: int = 8,
    max_lines: int | None = None,
) -> int:
    x, y = position
    lines = wrap_by_width(draw, text, font, max_width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    line_height = draw.textbbox((0, 0), "测试Ag", font=font)[3] + line_gap
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def build_storyboard(manifest: dict[str, Any], run_dir: Path) -> Path:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise EvolutionError("Pillow is required for storyboard rendering") from exc

    canvas = Image.new("RGB", (1920, 1400), "#07141c")
    draw = ImageDraw.Draw(canvas)
    title_font = find_font(42, bold=True)
    subtitle_font = find_font(24)
    card_title_font = find_font(25, bold=True)
    body_font = find_font(22)
    small_font = find_font(18)
    accent = "#5ee0b5"
    muted = "#a7bdc8"
    panel = "#102631"
    border = "#28505d"

    draw.text((48, 30), "EvoLab｜演化岔路", font=title_font, fill="#f2fafb")
    draw.text(
        (48, 88),
        "深时历史 → 多代转变 → 受约束的未来情景",
        font=subtitle_font,
        fill=accent,
    )
    draw.text(
        (1370, 48),
        "已知机制 / 情景推演 / 艺术表达",
        font=small_font,
        fill=muted,
    )

    card_width = 592
    card_x = [48, 664, 1280]
    time_labels = {
        "DEEP_TIME_HISTORY": "深时历史",
        "MULTIGENERATIONAL_TRANSITION": "多代转变",
        "FUTURE_SCENARIO": "未来情景",
    }
    for index, stage in enumerate(manifest["story_stages"]):
        x = card_x[index]
        draw.rounded_rectangle(
            (x, 140, x + card_width, 870),
            radius=24,
            fill=panel,
            outline=border,
            width=2,
        )
        title_bottom = draw_wrapped(
            draw,
            (x + 24, 164),
            f"0{index + 1}  {stage['title']}",
            card_title_font,
            "#f2fafb",
            card_width - 48,
            line_gap=3,
            max_lines=2,
        )
        draw.text(
            (x + 24, min(max(title_bottom + 2, 210), 236)),
            time_labels.get(stage["time_scope"], stage["time_scope"]),
            font=small_font,
            fill=accent,
        )
        image = Image.open(stage["image_path"]).convert("RGB")
        image.thumbnail((520, 520))
        image_x = x + (card_width - image.width) // 2
        image_y = 270
        canvas.paste(image, (image_x, image_y))
        status = "实时生成" if stage["render_source"] == "generated" else "缓存节点"
        draw.rounded_rectangle(
            (x + 24, 812, x + 150, 850),
            radius=16,
            fill="#183b43",
        )
        draw.text((x + 40, 820), status, font=small_font, fill=accent)
        after = stage.get("after_state", "")
        draw_wrapped(
            draw,
            (x + 170, 812),
            after,
            small_font,
            muted,
            card_width - 200,
            max_lines=1,
        )
        if index < 2:
            draw.polygon(
                [
                    (x + card_width + 8, 480),
                    (x + card_width + 28, 495),
                    (x + card_width + 8, 510),
                ],
                fill=accent,
            )

    draw.rounded_rectangle((48, 910, 1230, 1352), radius=24, fill=panel, outline=border, width=2)
    draw.text((76, 936), "核心知识", font=card_title_font, fill="#f2fafb")
    knowledge_y = 990
    for card in manifest.get("knowledge_cards", []):
        draw.text((76, knowledge_y), card["title"], font=subtitle_font, fill=accent)
        knowledge_y = draw_wrapped(
            draw,
            (76, knowledge_y + 38),
            card["body"],
            body_font,
            "#dce9ed",
            1100,
            max_lines=4,
        ) + 22

    draw.rounded_rectangle((1254, 910, 1872, 1130), radius=24, fill=panel, outline=border, width=2)
    draw.text((1282, 936), "规则驳回", font=card_title_font, fill="#ffcc73")
    rejection = next(
        (
            item
            for item in manifest.get("rejections", [])
            if item.get("rule_id") == "TRAIT_CONFLICT_HEAVY_FAST_CHEAP"
        ),
        (manifest.get("rejections") or [{}])[0],
    )
    rejection_text = rejection.get("reason", "没有触发规则驳回")
    revision_text = (rejection.get("revision") or {}).get("summary", "")
    y = draw_wrapped(draw, (1282, 985), rejection_text, body_font, "#f3e2c0", 560, max_lines=2)
    draw_wrapped(draw, (1282, y + 8), "修订：" + revision_text, small_font, muted, 560, max_lines=1)

    draw.rounded_rectangle((1254, 1154, 1872, 1352), radius=24, fill="#15333a", outline=accent, width=2)
    draw.text((1282, 1178), "最佳路线", font=card_title_font, fill=accent)
    selected = manifest["selected_route_id"]
    route = next(route for route in manifest["routes"] if route["route_id"] == selected)
    draw.text((1282, 1226), route["title"], font=subtitle_font, fill="#f2fafb")
    draw_wrapped(
        draw,
        (1282, 1266),
        manifest["decision_summary"],
        small_font,
        "#dce9ed",
        560,
        max_lines=2,
    )

    output = run_dir / "evolution_storyboard.png"
    canvas.save(output, format="PNG", optimize=True)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a three-stage EvoLab storyboard")
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--step-timeout", type=int, default=240)
    parser.add_argument("--ollama-timeout", type=int, default=300)
    parser.add_argument("--comfy-timeout", type=int, default=600)
    parser.add_argument(
        "--image-renderer",
        default=os.environ.get("EVOLAB_IMAGE_RENDERER", "flux1"),
        help="Image renderer profile. FLUX.1 remains the default until A/B passes.",
    )
    parser.add_argument(
        "--image-fallback",
        default=os.environ.get("EVOLAB_IMAGE_FALLBACK", "flux1"),
        help="Fallback image renderer profile, or 'none' to disable fallback.",
    )
    parser.add_argument("--no-ollama-fallback", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    schema = read_json(SCHEMA_FILE)
    rules = read_json(RULES_FILE)
    knowledge = read_json(KNOWLEDGE_FILE)

    output_root = (args.output_root or default_output_root()).expanduser().resolve()
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = run_dir / "evolution_manifest.json"

    fallbacks: list[str] = []
    try:
        if args.plan_file:
            plan, planner_metadata = load_plan_file(args.plan_file.resolve(), schema)
        else:
            try:
                log("Calling Step 3.7 Flash with strict JSON Schema")
                plan, planner_metadata = call_step_plan(
                    args.scenario,
                    schema,
                    args.step_timeout,
                )
            except EvolutionError as step_error:
                if args.no_ollama_fallback:
                    raise
                log(f"Step planning unavailable ({step_error}); trying local Qwen fallback")
                plan, planner_metadata = call_ollama_plan(
                    args.scenario,
                    schema,
                    args.ollama_timeout,
                )
                fallbacks.append("STEP_TO_OLLAMA")

        fixture_rejections = planner_metadata.pop("fixture_rejections", [])
        rejections = (
            fixture_rejections
            if fixture_rejections
            else apply_deterministic_rules(plan, rules)
        )
        manifest = make_manifest(
            run_id,
            plan,
            planner_metadata,
            rejections,
            knowledge,
        )
        manifest["fallbacks_used"].extend(fallbacks)
        atomic_write_json(manifest_path, manifest)
        if args.plan_only:
            manifest["status"] = "completed"
            atomic_write_json(manifest_path, manifest)
            print(f"MANIFEST:{manifest_path}")
            return 0

        renderer_ids = rendering.renderer_chain(args.image_renderer, args.image_fallback)
        _, requested_renderer = rendering.resolve_renderer(renderer_ids[0])
        manifest["image_renderer_requested"] = renderer_ids[0]
        manifest["image_renderer_chain"] = renderer_ids
        manifest["model"]["image_generator"] = requested_renderer["generator"]
        comfy_url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:7000")
        workshop_dir = Path(
            os.environ.get(
                "WORKSHOP_DIR",
                "/home/Developer/build_a_claw_workshop-bundle",
            )
        )
        comfy_output = workshop_dir / "comfyui-app" / "ComfyUI" / "output"
        unload_ollama()
        manifest["status"] = "rendering"
        atomic_write_json(manifest_path, manifest)

        for index, stage in enumerate(manifest["story_stages"]):
            manifest["current_stage"] = index + 1
            stage["status"] = "rendering"
            atomic_write_json(manifest_path, manifest)
            image_path, render_metadata = render_stage(
                stage,
                index,
                renderer_ids,
                run_dir,
                output_root,
                comfy_url,
                comfy_output,
                args.comfy_timeout,
                not args.no_cache,
            )
            stage["image_path"] = str(image_path)
            stage["render_source"] = render_metadata["render_source"]
            stage["render_metadata"] = render_metadata
            stage["status"] = (
                "generated" if render_metadata["render_source"] == "generated" else "cached"
            )
            if render_metadata.get("fallback_from"):
                fallback_marker = (
                    f"{render_metadata['fallback_from'].upper()}_TO_"
                    f"{render_metadata['renderer'].upper()}"
                )
                if fallback_marker not in manifest["fallbacks_used"]:
                    manifest["fallbacks_used"].append(fallback_marker)
            manifest["image_paths"].append(str(image_path))
            atomic_write_json(manifest_path, manifest)

        storyboard = build_storyboard(manifest, run_dir)
        manifest["storyboard_path"] = str(storyboard)
        manifest["status"] = "completed" if not fallbacks else "degraded"
        manifest["current_stage"] = 3
        atomic_write_json(manifest_path, manifest)
        log(f"Manifest: {manifest_path}")
        log(f"Storyboard: {storyboard}")
        print(f"MEDIA:{storyboard}")
        return 0
    except Exception as exc:
        failure = {
            "contract_version": "0.5.0",
            "run_id": run_id,
            "status": "failed",
            "current_stage": 0,
            "story_stages": [],
            "routes": [],
            "rejections": [],
            "knowledge_cards": [],
            "sources": [],
            "image_paths": [],
            "storyboard_path": "",
            "fallbacks_used": fallbacks,
            "failure": {
                "type": type(exc).__name__,
                "message": str(exc)[:800],
            },
        }
        atomic_write_json(manifest_path, failure)
        log(f"FAILED: {type(exc).__name__}: {str(exc)[:500]}")
        log(f"Failure manifest: {manifest_path}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
