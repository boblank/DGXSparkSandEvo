(function () {
  "use strict";

  const API_ROOT = "/api";
  const TIME_LABELS = {
    DEEP_TIME_HISTORY: "深时历史 · 细胞世界",
    MULTIGENERATIONAL_TRANSITION: "跨越许多代 · 个体边界改变",
    FUTURE_SCENARIO: "未来情景 · 不是确定预言",
    PREBIOTIC_CHEMISTRY: "生命出现以前 · 化学演化",
    ORIGIN_OF_LIFE_GAP: "生命起源缺口 · 竞争假说",
    EARLY_ANIMAL_ECOSYSTEM: "晚埃迪卡拉纪 · 海床生态",
    CAMBRIAN_TRANSITION: "埃迪卡拉—寒武纪过渡",
    DEVONIAN_TRANSITION: "泥盆纪 · 水陆边缘",
    HOMININ_TRANSITION: "上新世至中更新世 · 人族分枝",
    AVIAN_TRANSITION: "侏罗纪至早白垩纪 · 鸟类起源",
  };
  const CONSTRAINT_LABELS = {
    historical_reconstruction: "历史重建",
    origin_hypothesis: "起源假说",
    mixed_evidence: "历史与假说交界",
    future_scenario: "未来推演",
  };
  const EVIDENCE_LABELS = {
    KNOWN_MECHANISM: "已知机制",
    MECHANISM_HYPOTHESIS: "机制仍在研究",
    TEACHING_SIMPLIFICATION: "教学重建",
    SCENARIO_EXTRAPOLATION: "未来情景推演",
  };
  const LINEAGE_WAITING_MESSAGES = [
    "正在核对这条路是否走得通…",
    "正在把上一代留下的特征带过来…",
    "正在检查获得了什么，又付出了什么…",
    "DGX Spark 正在画出这一代的环境和身体…",
  ];
  const CHEMISTRY_WAITING_MESSAGES = [
    "正在核对这组反应是否走得通…",
    "正在把上一阶段留下的结构带过来…",
    "正在检查获得了什么，又付出了什么…",
    "DGX Spark 正在画出这一阶段的环境与化学系统…",
  ];
  const FINAL_WAITING_MESSAGES = [
    "第 1 / 2 步：最终阶段正在生成。",
    "图片完成后还会继续制作四阶段回放，请先别关闭页面。",
    "正在保留前面三次选择，回放会从起点开始。",
    "DGX Spark 正在画出最后一张阶段图…",
  ];

  const state = {
    scenarios: [],
    selectedScenarioId: null,
    currentScenarioId: null,
    envelope: null,
    selected: { environment: null, contingency: null, direction: null },
    busy: false,
    choiceBusy: false,
    choiceRequestId: 0,
    waitingTimer: null,
    waitingIndex: 0,
    videoReadyTimer: null,
  };

  const el = {};

  document.addEventListener("DOMContentLoaded", function () {
    cacheElements();
    bindEvents();
    setView("atlas");
    loadWorldAtlas();
  });

  function cacheElements() {
    [
      "brand-link", "world-button", "runtime-state", "world-atlas", "simulation-view",
      "scenario-list", "world-preview", "world-preview-image", "world-preview-era",
      "world-preview-depth", "world-preview-habitat", "world-preview-title",
      "world-preview-summary", "world-preview-question", "world-preview-evidence",
      "enter-world-button", "active-world-era", "active-world-title", "restart-button",
      "round-label", "round-rail", "organism-image", "image-placeholder", "time-scope",
      "habitat-label", "stage-kicker", "organism-name", "organism-summary", "trait-list",
      "generation-overlay", "generation-title", "generation-message", "choice-deck", "choice-content", "ending-content",
      "round-number", "chapter-label", "choice-title", "choice-lead", "contingency-legend", "direction-legend",
      "evolve-button-label", "final-video-hint", "ending-kicker", "ending-title", "evolution-form", "environment-choices",
      "contingency-choices", "direction-choices", "selection-recap", "evolve-button",
      "ending-restart", "ending-worlds", "ending-summary", "ending-video-card", "ending-video",
      "ending-video-label", "ending-video-state", "ending-video-loading", "ending-video-error", "knowledge-note",
      "ending-video-guide", "ending-video-guide-step", "ending-video-guide-title", "ending-video-guide-body",
      "knowledge-kicker", "knowledge-title", "knowledge-body", "evidence-button",
      "lineage-history", "film-kicker", "film-title", "trace-world", "trace-knowledge", "trace-plan", "trace-render",
      "error-toast", "error-message", "dismiss-error", "evidence-dialog", "evidence-tag",
      "evidence-title", "evidence-summary", "evidence-boundary", "evidence-sources",
    ].forEach(function (id) {
      el[id] = document.getElementById(id);
    });
  }

  function bindEvents() {
    el["brand-link"].addEventListener("click", function (event) {
      event.preventDefault();
      showWorldAtlas();
    });
    el["world-button"].addEventListener("click", showWorldAtlas);
    el["enter-world-button"].addEventListener("click", function () {
      startSession(state.selectedScenarioId);
    });
    el["restart-button"].addEventListener("click", function () {
      startSession(state.currentScenarioId);
    });
    el["ending-restart"].addEventListener("click", function () {
      startSession(state.currentScenarioId);
    });
    el["ending-worlds"].addEventListener("click", showWorldAtlas);
    el["evolution-form"].addEventListener("submit", evolve);
    el["dismiss-error"].addEventListener("click", hideError);
    el["evidence-button"].addEventListener("click", openEvidence);
    el["organism-image"].addEventListener("load", function () {
      el["organism-image"].classList.add("is-visible");
      el["image-placeholder"].classList.add("is-hidden");
    });
    el["organism-image"].addEventListener("error", function () {
      el["organism-image"].classList.remove("is-visible");
      el["image-placeholder"].classList.remove("is-hidden");
    });
    ["loadeddata", "canplay"].forEach(function (eventName) {
      el["ending-video"].addEventListener(eventName, function () {
        finishEndingVideoLoad();
      });
    });
    el["ending-video"].addEventListener("error", function () {
      if (el["ending-video-card"].hidden || !el["ending-video"].getAttribute("src")) return;
      window.clearTimeout(state.videoReadyTimer);
      state.videoReadyTimer = null;
      el["ending-video-loading"].hidden = true;
      el["ending-video-state"].textContent = "未能生成";
      el["ending-video-error"].hidden = false;
      setEndingVideoGuide("error");
      setRuntime("回放暂时没有生成", "error");
    });
  }

  async function request(path, options) {
    const response = await fetch(API_ROOT + path, Object.assign({ cache: "no-store" }, options || {}));
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      const message = payload && payload.error && payload.error.message;
      throw new Error(message || "实验室暂时没有回应，请过一会儿再试。");
    }
    return payload;
  }

  async function loadWorldAtlas() {
    hideError();
    setRuntime("正在读取七个世界", "busy");
    try {
      const results = await Promise.all([request("/health"), request("/scenarios")]);
      const health = results[0] || {};
      const registry = results[1] || {};
      state.scenarios = Array.isArray(registry.scenarios) ? registry.scenarios : [];
      renderScenarioList();
      selectScenario(registry.default_scenario_id || (state.scenarios[0] && state.scenarios[0].id));
      const restored = await restoreSessionFromUrl();
      if (!restored) {
        setRuntime(health.mode === "live" ? "DGX Spark 已就绪" : "七个世界可以预演", "ready");
      }
    } catch (error) {
      setRuntime("世界图谱暂时没有打开", "error");
      showError(readableError(error));
    }
  }

  function renderScenarioList() {
    el["scenario-list"].replaceChildren();
    state.scenarios.forEach(function (scenario, index) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "scenario-node";
      button.dataset.scenarioId = safeText(scenario.id);
      button.style.setProperty("--scene-accent", safeColor(scenario.accent));
      button.style.setProperty("--scene-step", String(index % 4));
      button.setAttribute("aria-pressed", "false");
      button.classList.toggle("is-future", scenario.constraint_mode === "future_scenario");

      const image = document.createElement("img");
      image.src = assetUrl(scenario.origin_asset);
      image.alt = "";
      const veil = document.createElement("span");
      veil.className = "scenario-veil";
      const era = document.createElement("small");
      era.textContent = safeText(scenario.era);
      const mode = document.createElement("em");
      mode.textContent = constraintLabel(scenario.constraint_mode);
      const title = document.createElement("strong");
      title.textContent = safeText(scenario.short_title, scenario.title);
      const habitat = document.createElement("span");
      habitat.className = "scenario-habitat";
      habitat.textContent = safeText(scenario.depth, scenario.habitat);
      button.append(image, veil, era, mode, title, habitat);
      button.addEventListener("click", function () {
        selectScenario(scenario.id, true);
      });
      el["scenario-list"].appendChild(button);
    });
  }

  function selectScenario(scenarioId, focusPreview) {
    const scenario = state.scenarios.find(function (item) { return item.id === scenarioId; });
    if (!scenario) return;
    state.selectedScenarioId = scenario.id;
    Array.from(el["scenario-list"].querySelectorAll(".scenario-node")).forEach(function (node) {
      const active = node.dataset.scenarioId === scenario.id;
      node.classList.toggle("is-selected", active);
      node.setAttribute("aria-pressed", active ? "true" : "false");
    });
    el["world-preview"].style.setProperty("--scene-accent", safeColor(scenario.accent));
    el["world-preview-image"].src = assetUrl(scenario.origin_asset);
    el["world-preview-image"].alt = safeText(scenario.title) + "的起始环境";
    el["world-preview-era"].textContent = safeText(scenario.era);
    el["world-preview-depth"].textContent = safeText(scenario.depth, scenario.habitat);
    el["world-preview-habitat"].textContent = constraintLabel(scenario.constraint_mode) + " · " + safeText(scenario.habitat);
    el["world-preview-title"].textContent = safeText(scenario.title);
    el["world-preview-summary"].textContent = safeText(scenario.summary);
    el["world-preview-question"].textContent = safeText(scenario.entry_question);
    el["world-preview-evidence"].textContent = "知识边界：" + safeText(scenario.evidence_note);
    el["enter-world-button"].disabled = false;
    if (focusPreview && window.matchMedia("(max-width: 800px)").matches) {
      el["world-preview"].scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  async function startSession(scenarioId) {
    if (state.busy || !scenarioId) return;
    hideError();
    setBusy(true, "正在把这个时代的起点放进观察舱…");
    setRuntime("正在进入这个世界", "busy");
    try {
      const envelope = await request("/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario_id: scenarioId }),
      });
      state.envelope = envelope;
      state.currentScenarioId = envelope.session.scenario_id;
      state.selected = { environment: null, contingency: null, direction: null };
      rememberSession(envelope.session.session_id);
      render(envelope);
      setView("simulation");
      setRuntime("可以开始第一次改变", "ready");
    } catch (error) {
      setRuntime("这个世界暂时进不去", "error");
      showError(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  function showWorldAtlas() {
    if (state.busy) return;
    hideError();
    clearRememberedSession();
    setView("atlas");
    if (state.currentScenarioId) selectScenario(state.currentScenarioId);
  }

  function setView(view) {
    const atlas = view === "atlas";
    el["world-atlas"].hidden = !atlas;
    el["simulation-view"].hidden = atlas;
    el["world-button"].hidden = atlas;
    if (atlas) el["ending-video"].pause();
    document.body.dataset.view = view;
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function render(envelope) {
    if (!envelope || !envelope.session) return;
    const session = envelope.session;
    const stage = session.current_stage || {};
    const scenario = session.scenario || {};
    const round = numberOr(session.round_index, 0);
    el["active-world-era"].textContent = safeText(scenario.era, "年代未定");
    el["active-world-title"].textContent = safeText(scenario.title, "当前世界");
    document.documentElement.style.setProperty("--active-accent", safeColor(scenario.accent));
    renderRail(round);
    renderStage(stage, round, scenario);
    renderHistory(session.history || [], numberOr(session.max_rounds, 3));
    renderKnowledge(stage, round);
    renderTrace(stage, round, scenario);
    if (round >= numberOr(session.max_rounds, 3)) {
      renderEnding(stage);
    } else {
      renderChoices(envelope.choices || {});
    }
  }

  function renderRail(round) {
    const labels = ["还在起点", "走过第一次改变", "走过第二次改变", "三次改变完成"];
    el["round-label"].textContent = labels[round] || (isChemistryWorld() ? "这组反应还会继续" : "这条谱系还会继续");
    Array.from(el["round-rail"].children).forEach(function (item) {
      const itemRound = Number(item.dataset.round);
      item.classList.toggle("is-done", itemRound < round);
      item.classList.toggle("is-current", itemRound === round);
    });
  }

  function renderStage(stage, round, scenario) {
    const imageUrl = safeText(stage.image_url);
    if (imageUrl && el["organism-image"].getAttribute("src") !== imageUrl) {
      el["organism-image"].classList.remove("is-visible");
      el["organism-image"].src = imageUrl;
    }
    const selection = stage.selection || {};
    el["organism-image"].alt = safeText(stage.organism_name, "当前阶段") + "在当前环境中的图像";
    el["time-scope"].textContent = TIME_LABELS[stage.time_scope] || (round === 0 ? safeText(scenario.era, "演化起点") : "许多代以后");
    el["habitat-label"].textContent = selection.environment && selection.environment.title
      ? selection.environment.title
      : safeText(scenario.habitat, "起始环境");
    el["stage-kicker"].textContent = round === 0
      ? "这个世界的起点"
      : "第 " + round + " 次改变 · " + safeText(selection.direction && selection.direction.title, isChemistryWorld() ? "化学路径发生变化" : "谱系发生变化");
    el["organism-name"].textContent = safeText(stage.organism_name, isChemistryWorld() ? "尚未命名的化学系统" : "尚未命名的谱系");
    el["organism-summary"].textContent = safeText(stage.lineage_summary, stage.change_summary || "它还在等待下一次环境变化。");
    el["trait-list"].replaceChildren();
    (Array.isArray(stage.traits) ? stage.traits.slice(0, 5) : []).forEach(function (trait) {
      const item = document.createElement("li");
      item.textContent = safeText(trait);
      el["trait-list"].appendChild(item);
    });
  }

  function renderChoices(choices) {
    const chemistry = isChemistryWorld();
    const maxRounds = state.envelope && state.envelope.session
      ? Number(state.envelope.session.max_rounds || 3)
      : 3;
    const finalRound = Number(choices.round) === maxRounds;
    el["choice-content"].hidden = false;
    el["ending-content"].hidden = true;
    hideEndingVideo();
    window.scrollTo({ top: 0, behavior: "auto" });
    el["choice-deck"].scrollTo({ top: 0, behavior: "auto" });
    el["round-number"].textContent = String(choices.round || "—");
    el["chapter-label"].textContent = safeText(choices.chapter, "新的难题");
    el["choice-title"].textContent = chemistry ? "这一阶段会发生什么？" : "这一代要面对什么？";
    el["choice-lead"].textContent = chemistry
      ? "先选周围条件。系统会据此重算哪些扰动更关键，再比较更容易延续的路径。"
      : "先选环境。系统会据此重算哪些偶然事件更关键，再比较更受支持的方向。";
    el["contingency-legend"].textContent = chemistry ? "此时哪些扰动更有影响" : "此时哪些偶然事件更有影响";
    el["direction-legend"].textContent = chemistry ? "这组条件更支持哪些反应路径" : "这组条件更支持哪些方向";
    el["final-video-hint"].hidden = !finalRound;
    el["evolve-button-label"].textContent = finalRound
      ? "生成最终阶段并制作回放"
      : chemistry ? "看看下一阶段" : "让下一代出现";
    state.selected = { environment: null, contingency: null, direction: null };
    state.choiceBusy = false;
    state.choiceRequestId += 1;
    renderChoiceGroup("environment", choices.environments || [], el["environment-choices"], false);
    renderChoicePlaceholder(el["contingency-choices"], "选定环境后，这里才会出现。", false);
    renderChoicePlaceholder(el["direction-choices"], "环境和偶然事件确定后，这里才会出现。", false);
    updateSelectionRecap();
  }

  function renderChoicePlaceholder(container, message, busy) {
    container.replaceChildren();
    const status = document.createElement("p");
    status.className = "choice-placeholder" + (busy ? " is-loading" : "");
    status.setAttribute("role", "status");
    const mark = document.createElement("span");
    mark.setAttribute("aria-hidden", "true");
    const copy = document.createElement("span");
    copy.textContent = message;
    status.append(mark, copy);
    container.appendChild(status);
  }

  function renderChoiceGroup(group, choices, container, isDirection) {
    container.replaceChildren();
    choices.forEach(function (choice) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = isDirection ? "direction-option" : "choice-option";
      button.dataset.choiceId = safeText(choice.id);
      button.setAttribute("aria-pressed", "false");
      const mark = document.createElement("span");
      mark.className = isDirection ? "direction-mark" : "choice-mark";
      mark.setAttribute("aria-hidden", "true");
      const title = document.createElement("strong");
      title.textContent = safeText(choice.title);
      const detail = document.createElement("small");
      detail.textContent = safeText(choice.description);
      button.append(mark, title, detail);
      if (choice.context_reason) {
        const reason = document.createElement("em");
        reason.textContent = "为什么现在出现：" + safeText(choice.context_reason);
        button.appendChild(reason);
      }
      if (isDirection) {
        const arrow = document.createElement("i");
        arrow.textContent = "→";
        arrow.setAttribute("aria-hidden", "true");
        button.appendChild(arrow);
      }
      button.addEventListener("click", function () {
        selectChoice(group, choice, container, button);
      });
      container.appendChild(button);
    });
  }

  async function selectChoice(group, choice, container, button) {
    if (state.busy || state.choiceBusy) return;
    state.selected[group] = choice;
    Array.from(container.querySelectorAll("button")).forEach(function (option) {
      const active = option === button;
      option.classList.toggle("is-selected", active);
      option.setAttribute("aria-pressed", active ? "true" : "false");
    });
    if (group === "environment") {
      state.selected.contingency = null;
      state.selected.direction = null;
      renderChoicePlaceholder(el["contingency-choices"], "正在根据当前环境重算候选…", true);
      renderChoicePlaceholder(el["direction-choices"], "先等环境压力把候选重新排好。", false);
      updateSelectionRecap();
      await recomputeChoices("environment");
      return;
    }
    if (group === "contingency") {
      state.selected.direction = null;
      renderChoicePlaceholder(el["direction-choices"], "正在把环境和偶然事件放在一起重算…", true);
      updateSelectionRecap();
      await recomputeChoices("contingency");
      return;
    }
    updateSelectionRecap();
  }

  function setChoiceBusy(busy) {
    state.choiceBusy = busy;
    el["evolution-form"].classList.toggle("is-recomputing", busy);
    el["evolution-form"].setAttribute("aria-busy", busy ? "true" : "false");
    Array.from(el["evolution-form"].querySelectorAll("button")).forEach(function (button) {
      if (button !== el["evolve-button"]) button.disabled = busy;
    });
    updateSelectionRecap();
  }

  async function recomputeChoices(stage) {
    if (!state.envelope) return;
    const session = state.envelope.session;
    const expectedRound = state.envelope.choices && state.envelope.choices.round;
    const selected = state.selected;
    const requestId = ++state.choiceRequestId;
    const payload = {
      expected_round: expectedRound,
      environment_id: selected.environment.id,
    };
    if (stage === "contingency" && selected.contingency) {
      payload.contingency_id = selected.contingency.id;
    }
    hideError();
    setChoiceBusy(true);
    try {
      const choices = await request(
        "/sessions/" + encodeURIComponent(session.session_id) + "/choices",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      if (requestId !== state.choiceRequestId) return;
      if (stage === "environment") {
        renderChoiceGroup("contingency", choices.contingencies || [], el["contingency-choices"], false);
        renderChoicePlaceholder(el["direction-choices"], "再选一个偶然事件，方向会继续收窄。", false);
        const first = el["contingency-choices"].querySelector("button");
        if (first) first.focus({ preventScroll: true });
      } else {
        renderChoiceGroup("direction", choices.directions || [], el["direction-choices"], true);
        const first = el["direction-choices"].querySelector("button");
        if (first) first.focus({ preventScroll: true });
      }
    } catch (error) {
      if (requestId !== state.choiceRequestId) return;
      const target = stage === "environment" ? el["contingency-choices"] : el["direction-choices"];
      renderChoicePlaceholder(target, "候选没有重算出来。可以换一个选择，或再试一次。", false);
      showError(readableError(error));
    } finally {
      if (requestId === state.choiceRequestId) setChoiceBusy(false);
    }
  }

  function updateSelectionRecap() {
    if (!el["selection-recap"]) return;
    const selected = state.selected;
    const complete = selected.environment && selected.contingency && selected.direction;
    el["evolve-button"].disabled = !complete || state.busy || state.choiceBusy;
    if (complete) {
      el["selection-recap"].textContent =
        selected.environment.title + "；" + selected.contingency.title + "。" +
        (isChemistryWorld() ? "接下来更容易延续的是：“" : "接下来留下更多的是：“") + selected.direction.title + "”。";
      return;
    }
    const missing = [];
    if (!selected.environment) missing.push("周围发生的变化");
    if (!selected.contingency) missing.push("偶然事件");
    if (!selected.direction) missing.push(isChemistryWorld() ? "更容易延续的化学路径" : "留下更多的方向");
    el["selection-recap"].textContent = "还要选：" + missing.join("、") + "。";
  }

  async function evolve(event) {
    event.preventDefault();
    if (state.busy || !state.envelope) return;
    const selected = state.selected;
    if (!selected.environment || !selected.contingency || !selected.direction) return;
    const session = state.envelope.session;
    const expectedRound = state.envelope.choices && state.envelope.choices.round;
    const finalRound = Number(expectedRound) === Number(session.max_rounds || 3);
    const payload = {
      environment_id: selected.environment.id,
      contingency_id: selected.contingency.id,
      direction_id: selected.direction.id,
      expected_round: expectedRound,
    };
    hideError();
    setBusy(true);
    startWaitingMessages(finalRound);
    try {
      const envelope = await request("/sessions/" + encodeURIComponent(session.session_id) + "/evolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.envelope = envelope;
      rememberSession(envelope.session.session_id);
      render(envelope);
      setRuntime(
        envelope.session.status === "completed" ? "第 2 / 2 步：正在制作四阶段回放" : "可以继续下一次改变",
        envelope.session.status === "completed" ? "busy" : "ready"
      );
      if (window.matchMedia("(max-width: 820px)").matches) {
        const target = envelope.session.status === "completed"
          ? el["ending-video-guide"]
          : document.querySelector(".evolution-stage");
        target.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
          block: "start",
        });
      }
    } catch (error) {
      setRuntime("原来的记录还在，可以重试", "error");
      showError(readableError(error));
    } finally {
      stopWaitingMessages();
      setBusy(false);
      updateSelectionRecap();
    }
  }

  function renderKnowledge(stage, round) {
    const match = stage.knowledge_match || {};
    const matched = match.status === "matched";
    if (round === 0 && !matched) {
      el["knowledge-kicker"].textContent = "这里是教学用的起点";
      el["knowledge-title"].textContent = "先看清它怎样活着";
      el["knowledge-body"].textContent = safeText(stage.uncertainty_note, "下一步命中已知机制时，这里会给出来源；没有命中时，会直接说明是推演。");
      el["evidence-button"].disabled = true;
      return;
    }
    const outcomeUnknown = match.generated_outcome_status === "no_match";
    el["knowledge-kicker"].textContent = matched
      ? (outcomeUnknown ? "环境事实有来源，生成形态仍是推演" : "这一步能在知识库里找到锚点")
      : "知识库没有对应的历史节点";
    el["knowledge-title"].textContent = matched ? safeText(match.title, "知识注脚") : "这次结果只能算受约束的假说";
    el["knowledge-body"].textContent = safeText(match.summary, isChemistryWorld()
      ? "系统按照环境、已有结构和代价给出这条路，没有把化学演化写成已经出现了生命。"
      : "系统按照环境、继承性状和代价给出这条路，没有把它写成已经发现的物种。");
    el["evidence-button"].disabled = !match.status;
  }

  function openEvidence() {
    if (!state.envelope) return;
    const stage = state.envelope.session.current_stage || {};
    const match = stage.knowledge_match || {};
    const sources = uniqueSources([].concat(match.sources || [], match.context_sources || []));
    el["evidence-tag"].textContent = EVIDENCE_LABELS[stage.evidence_tag]
      || (match.status === "matched" ? "有来源的机制解释" : "受约束的推演");
    el["evidence-title"].textContent = match.status === "matched" ? safeText(match.title, "知识解释") : "这条路线的证据边界";
    el["evidence-summary"].textContent = safeText(match.summary, stage.uncertainty_note || "这次结果用来解释因果关系，不是确定预测。");
    el["evidence-boundary"].textContent = safeText(match.boundary, stage.uncertainty_note || (isChemistryWorld()
      ? "生成结果只是竞争假说下的教学重建，不能据此宣布生命已经出现。"
      : "生成形态不等于已经发现的物种，也不代表演化只有一条路。"));
    el["evidence-sources"].replaceChildren();
    if (!sources.length) {
      const empty = document.createElement("p");
      empty.textContent = "这里没有硬贴一条参考文献。知识库没命中时，我们把空白留出来。";
      el["evidence-sources"].appendChild(empty);
    } else {
      sources.slice(0, 6).forEach(function (source) {
        const link = document.createElement("a");
        link.href = safeUrl(source.url);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = safeText(source.title, "打开来源");
        el["evidence-sources"].appendChild(link);
      });
    }
    if (typeof el["evidence-dialog"].showModal === "function") {
      el["evidence-dialog"].showModal();
    }
  }

  function renderHistory(history, maxRounds) {
    const frameCount = maxRounds + 1;
    el["film-kicker"].textContent = isChemistryWorld() ? "反应记录" : "谱系记录";
    el["film-title"].textContent = isChemistryWorld() ? "这套化学系统留下的四个瞬间" : "这条谱系留下的四个瞬间";
    el["lineage-history"].replaceChildren();
    history.slice(0, frameCount).forEach(function (stage, index) {
      const frame = document.createElement("li");
      frame.className = "film-frame";
      const image = document.createElement("img");
      image.src = safeText(stage.image_url);
      image.alt = safeText(stage.organism_name, "这一阶段") + "的缩略图";
      const number = document.createElement("span");
      number.textContent = String(index).padStart(2, "0");
      const title = document.createElement("b");
      title.textContent = safeText(stage.organism_name, isChemistryWorld() ? "未命名化学系统" : "未命名谱系");
      frame.append(image, number, title);
      el["lineage-history"].appendChild(frame);
    });
    for (let index = history.length; index < frameCount; index += 1) {
      const frame = document.createElement("li");
      frame.className = "film-frame is-empty";
      frame.textContent = index === 0 ? "起点" : "等待第 " + index + " 次改变";
      el["lineage-history"].appendChild(frame);
    }
  }

  function renderEnding(stage) {
    el["choice-content"].hidden = true;
    el["ending-content"].hidden = false;
    window.scrollTo({ top: 0, behavior: "auto" });
    el["choice-deck"].scrollTo({ top: 0, behavior: "auto" });
    renderEndingVideo();
    if (state.busy) {
      window.requestAnimationFrame(function () {
        el["ending-video-guide"].focus({ preventScroll: true });
      });
    }
    el["ending-kicker"].textContent = isChemistryWorld() ? "这套反应走完了三次改变" : "这条谱系走完了三次改变";
    const benefits = Array.isArray(stage.benefits) ? stage.benefits[0] : "适应了新的环境";
    const costs = Array.isArray(stage.costs) ? stage.costs[0] : "也承担了新的代价";
    el["ending-title"].textContent = isChemistryWorld()
      ? "反应继续了下去，生命仍没有被轻易宣布。"
      : "它暂时活了下来，世界没有因此停住。";
    el["ending-summary"].textContent = "最后一次改变带来“" + benefits + "”，代价是“" + costs + "”。换一个起点或选择，留下来的会是另一套答案。";
  }

  function renderEndingVideo() {
    const session = state.envelope && state.envelope.session;
    const videoUrl = session && safeText(session.lineage_video_url);
    if (!session || !videoUrl) {
      hideEndingVideo();
      return;
    }
    const video = el["ending-video"];
    setEndingVideoGuide("loading");
    el["ending-video-card"].hidden = false;
    el["ending-video-label"].textContent = isChemistryWorld()
      ? "这套反应 · 四阶段回放"
      : "这条路线 · 四阶段回放";
    el["ending-video-error"].hidden = true;
    video.poster = safeText(session.current_stage && session.current_stage.image_url);
    if (video.dataset.sessionId === session.session_id && video.readyState >= 2) {
      finishEndingVideoLoad();
      return;
    }
    video.pause();
    video.dataset.sessionId = session.session_id;
    el["ending-video-loading"].hidden = false;
    el["ending-video-state"].textContent = "正在整理";
    const version = encodeURIComponent(safeText(session.updated_at, session.session_id));
    video.src = videoUrl + "?v=" + version;
    video.load();
    watchEndingVideoReady(session.session_id, Date.now() + 30000);
  }

  function finishEndingVideoLoad() {
    const video = el["ending-video"];
    window.clearTimeout(state.videoReadyTimer);
    state.videoReadyTimer = null;
    el["ending-video-loading"].hidden = true;
    el["ending-video-error"].hidden = true;
    el["ending-video-state"].textContent = Number.isFinite(video.duration)
      ? "约 " + Math.max(1, Math.round(video.duration)) + " 秒"
      : "回放就绪";
    setEndingVideoGuide("ready");
    setRuntime("四阶段回放已经生成", "ready");
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      video.pause();
      return;
    }
    const attempt = video.play();
    if (attempt && typeof attempt.catch === "function") {
      attempt.catch(function () {
        el["ending-video-guide-body"].textContent = "从起点到第三次改变都在里面。点一下画面左下角开始播放。";
      });
    }
  }

  function watchEndingVideoReady(sessionId, deadline) {
    window.clearTimeout(state.videoReadyTimer);
    const video = el["ending-video"];
    if (video.dataset.sessionId !== sessionId || el["ending-video-card"].hidden) return;
    if (video.readyState >= 2) {
      finishEndingVideoLoad();
      return;
    }
    if (Date.now() >= deadline) return;
    state.videoReadyTimer = window.setTimeout(function () {
      watchEndingVideoReady(sessionId, deadline);
    }, 250);
  }

  function setEndingVideoGuide(mode) {
    const guide = el["ending-video-guide"];
    guide.hidden = false;
    guide.classList.toggle("is-loading", mode === "loading");
    guide.classList.toggle("is-ready", mode === "ready");
    guide.classList.toggle("is-error", mode === "error");
    if (mode === "ready") {
      el["ending-video-guide-step"].textContent = "完成 · 回放可以播放";
      el["ending-video-guide-title"].textContent = "视频已经生成";
      el["ending-video-guide-body"].textContent = "从起点到第三次改变都在里面。如果没有自动播放，点一下画面左下角。";
      return;
    }
    if (mode === "error") {
      el["ending-video-guide-step"].textContent = "回放暂时没有生成";
      el["ending-video-guide-title"].textContent = "四张阶段图还在";
      el["ending-video-guide-body"].textContent = "刷新页面会再次尝试，不必重走这条路线。";
      return;
    }
    el["ending-video-guide-step"].textContent = "第 2 / 2 步 · 正在制作回放";
    el["ending-video-guide-title"].textContent = "先别离开，四阶段回放还在生成";
    el["ending-video-guide-body"].textContent = "我们正在把起点、三次改变和你的选择整理成约 10 秒视频。完成后会自动出现。";
  }

  function hideEndingVideo() {
    window.clearTimeout(state.videoReadyTimer);
    state.videoReadyTimer = null;
    el["ending-video-guide"].hidden = true;
    el["ending-video-card"].hidden = true;
    el["ending-video"].pause();
    el["ending-video"].removeAttribute("src");
    delete el["ending-video"].dataset.sessionId;
    el["ending-video"].load();
    el["ending-video-loading"].hidden = true;
    el["ending-video-error"].hidden = true;
  }

  function renderTrace(stage, round, scenario) {
    const match = stage.knowledge_match || {};
    const model = stage.model || {};
    setTrace(el["trace-world"], true, safeText(scenario.short_title, scenario.title) + " · " + safeText(TIME_LABELS[stage.time_scope], "起点规则"));
    setTrace(el["trace-knowledge"], Boolean(match.status), match.status === "matched" ? safeText(match.title, "已匹配知识卡") : "没有硬套历史节点，保留为空");
    const plannerLabel = model.planner === "fixture" ? "离线预演" : safeText(model.planner);
    el["trace-plan"].querySelector("strong").textContent = isChemistryWorld() ? "化学路径规划" : "下一代规划";
    setTrace(el["trace-plan"], Boolean(model.planner), model.planner ? plannerLabel + " · 严格结构输出" : "起点来自场景包，还没有生成" + (isChemistryWorld() ? "下一阶段" : "下一代"));
    const renderDone = round > 0 && Boolean(stage.render_source);
    const generator = stage.render_source === "fixture"
      ? "浏览器预演图"
      : safeText(stage.render_metadata && stage.render_metadata.generator, stage.render_source)
        .replace(/\s+via\s+ComfyUI/gi, " · 通过 ComfyUI");
    setTrace(el["trace-render"], renderDone, renderDone ? generator : "起点图来自场景包；" + (isChemistryWorld() ? "下一阶段" : "下一代") + "将在 DGX 绘制");
  }

  function setTrace(item, done, detail) {
    item.classList.toggle("is-done", done);
    const small = item.querySelector("small");
    if (small) small.textContent = detail;
  }

  function setBusy(busy, message) {
    state.busy = busy;
    el["enter-world-button"].disabled = busy || !state.selectedScenarioId;
    el["restart-button"].disabled = busy;
    if (!el["simulation-view"].hidden) {
      el["generation-overlay"].hidden = !busy;
      if (message) el["generation-message"].textContent = message;
    }
    if (busy) setRuntime(isChemistryWorld() ? "下一阶段正在形成" : "下一代正在形成", "busy");
    updateSelectionRecap();
  }

  function startWaitingMessages(finalRound) {
    const messages = finalRound
      ? FINAL_WAITING_MESSAGES
      : isChemistryWorld() ? CHEMISTRY_WAITING_MESSAGES : LINEAGE_WAITING_MESSAGES;
    state.waitingIndex = 0;
    el["generation-title"].textContent = finalRound
      ? "最终阶段正在生成"
      : isChemistryWorld() ? "下一阶段正在形成" : "下一代正在形成";
    el["generation-message"].textContent = messages[0];
    if (finalRound) setRuntime("第 1 / 2 步：生成最终阶段", "busy");
    window.clearInterval(state.waitingTimer);
    state.waitingTimer = window.setInterval(function () {
      state.waitingIndex = (state.waitingIndex + 1) % messages.length;
      el["generation-message"].textContent = messages[state.waitingIndex];
    }, 4200);
  }

  function stopWaitingMessages() {
    window.clearInterval(state.waitingTimer);
    state.waitingTimer = null;
    el["generation-overlay"].hidden = true;
  }

  function setRuntime(label, mode) {
    el["runtime-state"].className = "runtime-state" + (mode ? " is-" + mode : "");
    el["runtime-state"].querySelector("span").textContent = label;
  }

  function constraintLabel(mode) {
    return CONSTRAINT_LABELS[mode] || "证据边界待确认";
  }

  function showError(message) {
    el["error-message"].textContent = message;
    el["error-toast"].hidden = false;
  }

  function hideError() {
    el["error-toast"].hidden = true;
  }

  function uniqueSources(sources) {
    const seen = new Set();
    return sources.filter(function (source) {
      const key = source && safeText(source.source_id, source.title || source.url).toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function assetUrl(value) {
    const path = safeText(value);
    return path ? "/" + path.replace(/^\/+/, "") : "";
  }

  function isChemistryWorld() {
    const session = state.envelope && state.envelope.session;
    const scenarioId = (session && session.scenario_id) || state.currentScenarioId || state.selectedScenarioId;
    return scenarioId === "hydrothermal_origin";
  }

  async function restoreSessionFromUrl() {
    const sessionId = new URLSearchParams(window.location.search).get("session") || "";
    if (!/^[0-9]{8}T[0-9]{6}-[a-f0-9]{8}$/.test(sessionId)) return false;
    try {
      const envelope = await request("/sessions/" + encodeURIComponent(sessionId));
      state.envelope = envelope;
      state.currentScenarioId = envelope.session.scenario_id;
      state.selected = { environment: null, contingency: null, direction: null };
      selectScenario(state.currentScenarioId);
      render(envelope);
      setView("simulation");
      if (envelope.session.status === "completed" && el["ending-video"].readyState >= 2) {
        finishEndingVideoLoad();
      } else {
        setRuntime(
          envelope.session.status === "completed" ? "正在读取四阶段回放" : "可以继续下一次改变",
          envelope.session.status === "completed" ? "busy" : "ready"
        );
      }
      return true;
    } catch (error) {
      clearRememberedSession();
      showError("这段路线暂时没有打开。" + readableError(error));
      return false;
    }
  }

  function rememberSession(sessionId) {
    if (!sessionId || !window.history || typeof window.history.replaceState !== "function") return;
    const url = new URL(window.location.href);
    url.searchParams.set("session", sessionId);
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  }

  function clearRememberedSession() {
    if (!window.history || typeof window.history.replaceState !== "function") return;
    const url = new URL(window.location.href);
    url.searchParams.delete("session");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  }

  function safeText(value, fallback) {
    if (typeof value === "string" && value.trim()) return value.trim();
    return fallback || "";
  }

  function safeColor(value) {
    const text = safeText(value, "#69D3BE");
    return /^#[0-9a-f]{6}$/i.test(text) ? text : "#69D3BE";
  }

  function safeUrl(value) {
    const text = safeText(value, "#");
    try {
      const url = new URL(text, window.location.href);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "#";
    } catch (_error) {
      return "#";
    }
  }

  function numberOr(value, fallback) {
    return Number.isFinite(Number(value)) ? Number(value) : fallback;
  }

  function readableError(error) {
    return error && typeof error.message === "string" ? error.message : "实验室暂时没有回应。原来的选择还在，可以再试一次。";
  }
})();
