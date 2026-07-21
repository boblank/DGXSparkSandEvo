(function () {
  "use strict";

  const API_ROOT = "/api";
  const TIME_LABELS = {
    DEEP_TIME_HISTORY: "深时历史 · 细胞世界",
    MULTIGENERATIONAL_TRANSITION: "许多代以后 · 个体边界改变",
    FUTURE_SCENARIO: "未来情景 · 不是确定预言",
  };
  const EVIDENCE_LABELS = {
    KNOWN_MECHANISM: "已知机制",
    MECHANISM_HYPOTHESIS: "机制仍在研究",
    TEACHING_SIMPLIFICATION: "教学简化",
    SCENARIO_EXTRAPOLATION: "未来情景推演",
  };
  const WAITING_MESSAGES = [
    "先读懂上一代留下的特征…",
    "把环境压力放进下一代…",
    "让收益和代价彼此制衡…",
    "DGX Spark 正在绘制新的形态…",
  ];

  const state = {
    envelope: null,
    selected: { environment: null, contingency: null, direction: null },
    busy: false,
    waitingTimer: null,
    waitingIndex: 0,
  };

  const el = {};

  document.addEventListener("DOMContentLoaded", function () {
    cacheElements();
    bindEvents();
    startSession();
  });

  function cacheElements() {
    [
      "runtime-state", "restart-button", "round-label", "round-rail", "organism-image",
      "image-placeholder", "time-scope", "habitat-label", "stage-kicker", "organism-name",
      "organism-summary", "trait-list", "generation-overlay", "generation-message",
      "choice-content", "ending-content", "round-number", "evolution-form",
      "environment-choices", "contingency-choices", "direction-choices", "selection-recap",
      "evolve-button", "ending-restart", "ending-summary", "knowledge-note", "knowledge-kicker",
      "knowledge-title", "knowledge-body", "evidence-button", "lineage-history", "error-toast",
      "error-message", "dismiss-error", "evidence-dialog", "evidence-tag", "evidence-title",
      "evidence-summary", "evidence-boundary", "evidence-sources",
    ].forEach(function (id) {
      el[id] = document.getElementById(id);
    });
  }

  function bindEvents() {
    el["evolution-form"].addEventListener("submit", evolve);
    el["restart-button"].addEventListener("click", startSession);
    el["ending-restart"].addEventListener("click", startSession);
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
      throw new Error(message || "实验室暂时没有回应，请稍后再试。");
    }
    return payload;
  }

  async function startSession() {
    if (state.busy) return;
    hideError();
    setBusy(true, "正在准备一片新的远古浅海…");
    try {
      const health = await request("/health");
      const envelope = await request("/sessions", { method: "POST" });
      state.envelope = envelope;
      state.selected = { environment: null, contingency: null, direction: null };
      render(envelope);
      const mode = health && health.mode === "live" ? "DGX Spark 已就绪" : "互动预演已就绪";
      setRuntime(mode, "ready");
    } catch (error) {
      setRuntime("实验室暂未连接", "error");
      showError(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  function render(envelope) {
    if (!envelope || !envelope.session) return;
    const session = envelope.session;
    const stage = session.current_stage || {};
    const round = numberOr(session.round_index, 0);
    renderRail(round);
    renderStage(stage, round);
    renderHistory(session.history || []);
    renderKnowledge(stage, round);

    if (round >= numberOr(session.max_rounds, 3)) {
      renderEnding(stage);
    } else {
      renderChoices(envelope.choices || {});
    }
  }

  function renderRail(round) {
    const labels = ["起点", "第一次改变", "第二次改变", "走向未来"];
    el["round-label"].textContent = labels[round] || "仍在继续";
    Array.from(el["round-rail"].children).forEach(function (item) {
      const itemRound = Number(item.dataset.round);
      item.classList.toggle("is-done", itemRound < round);
      item.classList.toggle("is-current", itemRound === round);
    });
  }

  function renderStage(stage, round) {
    const imageUrl = safeText(stage.image_url);
    if (imageUrl && el["organism-image"].getAttribute("src") !== imageUrl) {
      el["organism-image"].classList.remove("is-visible");
      el["organism-image"].src = imageUrl;
    }
    const selection = stage.selection || {};
    el["organism-image"].alt = safeText(stage.organism_name) + "在当前环境中的生成图";
    el["time-scope"].textContent = TIME_LABELS[stage.time_scope] || (round === 0 ? "深时历史 · 演化起点" : "许多代以后");
    el["habitat-label"].textContent = selection.environment && selection.environment.title
      ? selection.environment.title
      : "古老浅海 · 潮池边缘";
    el["stage-kicker"].textContent = round === 0
      ? "演化起点"
      : "第 " + round + " 次改变 · " + safeText(selection.direction && selection.direction.title, "谱系发生改变");
    el["organism-name"].textContent = safeText(stage.organism_name, "尚未命名的生命谱系");
    el["organism-summary"].textContent = safeText(stage.lineage_summary, stage.change_summary || "这条谱系仍在等待下一次改变。");
    el["trait-list"].replaceChildren();
    (Array.isArray(stage.traits) ? stage.traits.slice(0, 5) : []).forEach(function (trait) {
      const item = document.createElement("li");
      item.textContent = safeText(trait);
      el["trait-list"].appendChild(item);
    });
  }

  function renderChoices(choices) {
    el["choice-content"].hidden = false;
    el["ending-content"].hidden = true;
    el["round-number"].textContent = String(choices.round || "—");
    state.selected = { environment: null, contingency: null, direction: null };
    renderChoiceGroup("environment", choices.environments || [], el["environment-choices"], false);
    renderChoiceGroup("contingency", choices.contingencies || [], el["contingency-choices"], false);
    renderChoiceGroup("direction", choices.directions || [], el["direction-choices"], true);
    updateSelectionRecap();
  }

  function renderChoiceGroup(group, choices, container, isDirection) {
    container.replaceChildren();
    choices.forEach(function (choice) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = isDirection ? "direction-option" : "choice-option";
      button.dataset.choiceId = safeText(choice.id);
      button.setAttribute("aria-pressed", "false");
      if (isDirection) {
        const mark = document.createElement("span");
        mark.className = "direction-mark";
        mark.setAttribute("aria-hidden", "true");
        const title = document.createElement("strong");
        title.textContent = safeText(choice.title);
        const detail = document.createElement("small");
        detail.textContent = safeText(choice.description);
        const arrow = document.createElement("i");
        arrow.textContent = "→";
        arrow.setAttribute("aria-hidden", "true");
        button.append(mark, title, detail, arrow);
      } else {
        button.textContent = safeText(choice.title);
        button.title = safeText(choice.description);
      }
      button.addEventListener("click", function () {
        selectChoice(group, choice, container, button);
      });
      container.appendChild(button);
    });
  }

  function selectChoice(group, choice, container, button) {
    if (state.busy) return;
    state.selected[group] = choice;
    Array.from(container.querySelectorAll("button")).forEach(function (option) {
      const active = option === button;
      option.classList.toggle("is-selected", active);
      option.setAttribute("aria-pressed", active ? "true" : "false");
    });
    updateSelectionRecap();
  }

  function updateSelectionRecap() {
    const selected = state.selected;
    const complete = selected.environment && selected.contingency && selected.direction;
    el["evolve-button"].disabled = !complete || state.busy;
    if (complete) {
      el["selection-recap"].textContent =
        "在“" + selected.environment.title + "”中，遇上“" + selected.contingency.title + "”，这条谱系选择“" + selected.direction.title + "”。";
      return;
    }
    const missing = [];
    if (!selected.environment) missing.push("环境");
    if (!selected.contingency) missing.push("偶然事件");
    if (!selected.direction) missing.push("演化方向");
    el["selection-recap"].textContent = "还需要选择：" + missing.join("、") + "。";
  }

  async function evolve(event) {
    event.preventDefault();
    if (state.busy || !state.envelope) return;
    const selected = state.selected;
    if (!selected.environment || !selected.contingency || !selected.direction) return;
    const session = state.envelope.session;
    const expectedRound = state.envelope.choices && state.envelope.choices.round;
    const payload = {
      environment_id: selected.environment.id,
      contingency_id: selected.contingency.id,
      direction_id: selected.direction.id,
      expected_round: expectedRound,
    };
    hideError();
    setBusy(true);
    startWaitingMessages();
    try {
      const envelope = await request("/sessions/" + encodeURIComponent(session.session_id) + "/evolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.envelope = envelope;
      render(envelope);
      setRuntime(envelope.session.status === "completed" ? "三轮演化完成" : "可以继续选择", "ready");
      if (window.matchMedia("(max-width: 820px)").matches) {
        document.querySelector(".evolution-stage").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    } catch (error) {
      setRuntime("这一轮可以重试", "error");
      showError(readableError(error));
    } finally {
      stopWaitingMessages();
      setBusy(false);
      updateSelectionRecap();
    }
  }

  function renderKnowledge(stage, round) {
    if (round === 0) {
      el["knowledge-kicker"].textContent = "这是一片教学用的起始生态";
      el["knowledge-title"].textContent = "先做一次选择";
      el["knowledge-body"].textContent = "如果下一步对应已知的演化节点，我们会告诉你它在人类知识里叫什么；如果没有，就把推演依据和不确定性说清楚。";
      el["evidence-button"].disabled = true;
      return;
    }
    const match = stage.knowledge_match || {};
    const matched = match.status === "matched";
    const outcomeUnknown = match.generated_outcome_status === "no_match";
    el["knowledge-kicker"].textContent = matched
      ? (outcomeUnknown ? "环境事实有证据，未来形态仍是推演" : "这一步在人类知识里有名字")
      : "没有对应到已知的历史节点";
    el["knowledge-title"].textContent = matched ? safeText(match.title, "知识注脚") : "这是一次有约束的推演";
    el["knowledge-body"].textContent = safeText(match.summary, "系统按环境压力、遗传差异和生存代价给出这条路线，但不会把它冒充成已经发现的物种。");
    el["evidence-button"].disabled = false;
  }

  function openEvidence() {
    if (!state.envelope) return;
    const stage = state.envelope.session.current_stage || {};
    const match = stage.knowledge_match || {};
    const sources = uniqueSources([].concat(match.sources || [], match.context_sources || []));
    el["evidence-tag"].textContent = EVIDENCE_LABELS[stage.evidence_tag] || (match.status === "matched" ? "有来源的知识解释" : "受约束的演化假说");
    el["evidence-title"].textContent = match.status === "matched" ? safeText(match.title, "知识解释") : "这条路线的证据边界";
    el["evidence-summary"].textContent = safeText(match.summary, stage.uncertainty_note || "这次结果用于解释因果关系，不是确定预测。");
    el["evidence-boundary"].textContent = safeText(match.boundary, stage.uncertainty_note || "生成形态不等于已发现物种，也不代表演化只有一条路。");
    el["evidence-sources"].replaceChildren();
    if (!sources.length) {
      const empty = document.createElement("p");
      empty.textContent = "这里没有硬贴一条参考文献。知识库未命中时，我们宁愿明确留白。";
      el["evidence-sources"].appendChild(empty);
    } else {
      sources.slice(0, 5).forEach(function (source) {
        const link = document.createElement("a");
        link.href = safeUrl(source.url);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = safeText(source.title, "查看来源");
        el["evidence-sources"].appendChild(link);
      });
    }
    if (typeof el["evidence-dialog"].showModal === "function") {
      el["evidence-dialog"].showModal();
    }
  }

  function renderHistory(history) {
    el["lineage-history"].replaceChildren();
    history.slice(0, 4).forEach(function (stage, index) {
      const frame = document.createElement("li");
      frame.className = "film-frame";
      const image = document.createElement("img");
      image.src = safeText(stage.image_url);
      image.alt = safeText(stage.organism_name) + "的阶段缩略图";
      const number = document.createElement("span");
      number.textContent = String(index).padStart(2, "0");
      const title = document.createElement("b");
      title.textContent = safeText(stage.organism_name, "未命名谱系");
      frame.append(image, number, title);
      el["lineage-history"].appendChild(frame);
    });
    for (let index = history.length; index < 4; index += 1) {
      const frame = document.createElement("li");
      frame.className = "film-frame is-empty";
      frame.textContent = index === 0 ? "起点" : "等待第 " + index + " 次改变";
      el["lineage-history"].appendChild(frame);
    }
  }

  function renderEnding(stage) {
    el["choice-content"].hidden = true;
    el["ending-content"].hidden = false;
    const benefits = Array.isArray(stage.benefits) ? stage.benefits[0] : "适应了新的环境";
    const costs = Array.isArray(stage.costs) ? stage.costs[0] : "也承担了新的代价";
    el["ending-summary"].textContent = "最后一轮里，它“" + benefits + "”，同时也“" + costs + "”。回到起点，换一个选择，就会得到另一条仍然讲得通的谱系。";
  }

  function setBusy(busy, message) {
    state.busy = busy;
    el["restart-button"].disabled = busy;
    el["generation-overlay"].hidden = !busy;
    if (message) el["generation-message"].textContent = message;
    if (busy) setRuntime("下一代正在形成", "busy");
    updateSelectionRecap();
  }

  function startWaitingMessages() {
    state.waitingIndex = 0;
    el["generation-message"].textContent = WAITING_MESSAGES[0];
    window.clearInterval(state.waitingTimer);
    state.waitingTimer = window.setInterval(function () {
      state.waitingIndex = (state.waitingIndex + 1) % WAITING_MESSAGES.length;
      el["generation-message"].textContent = WAITING_MESSAGES[state.waitingIndex];
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
      const key = source && safeText(source.title, source.url || source.source_id).toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function safeText(value, fallback) {
    if (typeof value === "string" && value.trim()) return value.trim();
    return fallback || "";
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
    return error && typeof error.message === "string" ? error.message : "服务暂时没有回应。你的选择还在，可以再试一次。";
  }
})();
