/* Execution viewer. Explicit intervention exports context; instructions stay in main chat. */
(() => {
  "use strict";

  const byId = id => document.getElementById(id);
  const ui = {
    panel: byId("execution-panel"),
    title: byId("task-title"), picker: byId("session-picker"), select: byId("session-select"),
    selectButton: byId("select-session"), pickerNote: byId("picker-note"),
    progress: byId("progress-section"), count: byId("progress-count"), percent: byId("progress-percent"),
    track: byId("progress-track"), fill: byId("progress-fill"),
    empty: byId("empty-state"), emptyTitle: byId("empty-title"), emptyDescription: byId("empty-description"),
    parallel: byId("parallel-state"), route: byId("route"), changes: byId("plan-changes"), error: byId("connection-error"),
    errorText: byId("connection-error-text"), retry: byId("retry"), runtime: byId("runtime-label"),
    runtimeDot: byId("runtime-dot"), updated: byId("last-updated"), announcement: byId("status-announcement"),
    planVersion: byId("plan-version"), history: byId("plan-history"),
  };
  const params = new URLSearchParams(window.location.search);
  const preview = params.get("preview") === "1";
  const nodeLabels = { pending: "待开始", in_progress: "进行中", completed: "已完成", blocked: "受阻", cancelled: "已取消" };
  const runtimeLabels = { running: "正在执行", working: "正在执行", in_progress: "正在执行", completed: "本轮已结束", idle: "等待继续", waiting: "正在等待", waiting_user: "等待你的操作", blocked: "遇到阻塞", error: "执行出错", failed: "执行失败", interrupted: "已中断", unknown: "运行状态未提供" };
  let host = null;
  let sessionId = preview ? params.get("sessionId") : null;
  let revision = null;
  let snapshot = null;
  let nodesSignature = "";
  let changesSignature = "";
  let previousNodeStates = new Map();
  const manuallyToggledNodes = new Set();
  let timer = null;
  let busy = false;
  let listing = false;
  let failures = 0;
  let stopped = false;
  let receivedInitial = false;
  let activeFetch = null;
  let pendingRead = null;
  let historySignature = "";
  let runtimeRunning = false;
  let boundSessionId = null;
  const draftAttempts = new Map();

  function canAppendDraft() { return !preview && boundSessionId === sessionId && Boolean(host?.supportsDraft()); }
  function interventionButton(nodeId, issueId) {
    const nodeLevel = issueId === undefined;
    const wrapper = element("div", nodeLevel ? "intervention intervention--node" : "intervention");
    const attemptKey = JSON.stringify([sessionId, snapshot.planVersion, nodeId, issueId ?? null]);
    const button = element("button", "intervention-button", canAppendDraft() && !draftAttempts.has(attemptKey) ? "@ 带入主对话" : "@ 复制干预提示");
    button.type = "button";
    button.dataset.focusKey = `intervene:${nodeId}:${issueId}`;
    const feedback = element("p", "intervention-feedback");
    feedback.setAttribute("role", "status"); feedback.hidden = true;
    const manual = element("pre", "intervention-text"); manual.hidden = true;
    if (nodeLevel) {
      const ordinal = String(snapshot.nodes.findIndex(node => node.id === nodeId) + 1).padStart(2, "0");
      button.setAttribute("aria-label", `@ 第 ${ordinal} 项：${canAppendDraft() ? "带入主对话" : "复制上下文"}`);
      button.setAttribute("title", `引用第 ${ordinal} 项`);
    }
    const attempt = draftAttempts.get(attemptKey);
    if (attempt) {
      feedback.hidden = false;
      feedback.textContent = attempt === "confirmed" ? "已加入当前草稿，补充要求后发送。" : attempt === "pending" ?
        "正在加入主对话草稿…" : "客户端未确认加入草稿；可复制提示，粘贴前检查主对话框。";
      if (attempt !== "unknown") {
        button.disabled = true;
        button.textContent = attempt === "confirmed" ? "已加入草稿" : "正在加入草稿…";
      }
    }
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      feedback.hidden = false;
      try {
        const content = window.ExecutionPlanModel.interventionText(snapshot, nodeId, issueId);
        const key = JSON.stringify([sessionId, snapshot.planVersion, nodeId, issueId ?? null]);
        if (canAppendDraft() && !draftAttempts.has(key)) {
          draftAttempts.set(key, "pending");
          feedback.textContent = "正在加入主对话草稿…";
          try {
            await host.appendDraft(content);
            draftAttempts.set(key, "confirmed");
            feedback.textContent = "已加入当前草稿，补充要求后发送。";
            button.textContent = "已加入草稿";
            return;
          } catch {
            draftAttempts.set(key, "unknown");
            // A missing acknowledgement is not proof of failure: never auto retry.
            feedback.textContent = "客户端未确认加入草稿；可复制提示，粘贴前检查主对话框。";
            button.textContent = "@ 复制干预提示";
            return;
          }
        }
        if (!navigator.clipboard?.writeText) throw new Error("请选中下方提示复制到主对话框。");
        try { await navigator.clipboard.writeText(content); }
        catch { throw new Error("复制未成功，请选中下方提示手动复制。"); }
        feedback.textContent = "已复制，在主对话框粘贴并补充后发送。";
        button.textContent = "@ 再次复制";
      } catch (error) {
        feedback.textContent = text(error?.message, "请手动复制干预提示。");
        try { manual.textContent = window.ExecutionPlanModel.interventionText(snapshot, nodeId, issueId); manual.hidden = false; } catch {}
      } finally {
        if (button.textContent !== "已加入草稿") button.disabled = false;
        if (!stopped && snapshot) renderNodes(snapshot.nodes);
      }
    });
    // Keep the node entry visible beside a collapsed summary, without nesting a
    // button in <summary> or changing the user's expansion state.
    if (nodeLevel) button.textContent = attempt === "confirmed" ? "✓" : attempt === "pending" ? "…" : "@";
    wrapper.append(button, feedback, manual);
    return wrapper;
  }

  function element(tag, className, text) {
    const value = document.createElement(tag);
    if (className) value.className = className;
    if (text !== undefined && text !== null) value.textContent = String(text);
    return value;
  }

  function text(value, fallback = "") { return typeof value === "string" ? value : fallback; }
  function unwrap(result) { return result?.structuredContent ?? result?.result?.structuredContent ?? result; }
  function timeLabel(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date.toLocaleTimeString("zh-CN", { hour12: false });
  }
  function showEmpty(title, description) {
    ui.empty.hidden = false;
    ui.emptyTitle.textContent = title;
    ui.emptyDescription.textContent = description;
  }
  function clearTimer() { if (timer !== null) window.clearTimeout(timer); timer = null; }
  function schedule(delay = 1000) {
    clearTimer();
    if (stopped || document.hidden || !sessionId) return;
    timer = window.setTimeout(() => { timer = null; void refresh(); }, delay);
  }
  function updateRuntime(runtime) {
    if (!runtime || typeof runtime !== "object") return;
    const status = text(runtime.status, "unknown");
    ui.runtime.textContent = text(runtime.label, runtimeLabels[status] || "运行状态未提供");
    ui.runtimeDot.dataset.state = status;
    runtimeRunning = ["running", "working", "in_progress"].includes(status);
    updateActivity();
  }
  function updateActivity() {
    const activeNode = snapshot?.nodes.some(node => window.ExecutionPlanModel.nodeState(node).active);
    const executing = runtimeRunning && !document.hidden && !stopped;
    ui.panel.dataset.executing = String(executing && Boolean(activeNode));
    const count = executing ? window.ExecutionPlanModel.parallelCount(snapshot?.nodes || []) : 0;
    ui.parallel.hidden = count < 2;
    ui.parallel.textContent = count > 1 ? `并行执行中 · ${count} 项` : "";
  }
  function updateTimestamp(value) {
    const label = timeLabel(value);
    ui.updated.textContent = label ? `更新于 ${label}` : "尚未同步";
    if (label) ui.updated.dateTime = value;
    else ui.updated.removeAttribute("datetime");
  }

  function evidenceElement(evidence, key, openState) {
    const list = Array.isArray(evidence) ? evidence.filter(item => item && typeof item === "object") : [];
    if (!list.length) return null;
    const details = element("details", "node-evidence");
    details.dataset.key = `evidence:${key}`;
    details.open = openState.get(details.dataset.key) ?? false;
    const summary = element("summary", "", `完成依据 · ${list.length} 项`);
    summary.dataset.focusKey = details.dataset.key;
    const items = element("ul");
    for (const item of list) {
      const row = element("li");
      const label = text(item.label, "验证结果");
      let url = null;
      try { const candidate = new URL(item.url); if (["http:", "https:"].includes(candidate.protocol)) url = candidate; } catch {}
      if (url) {
        const link = element("a", "", label);
        link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
        row.append(link);
      } else row.append(element("span", "", label));
      if (item.detail) row.append(element("p", "", text(item.detail)));
      items.append(row);
    }
    details.append(summary, items);
    return details;
  }

  function requestElement(request) {
    const note = element("div", "request-note");
    const label = request.kind === "pause_after" ? "已请求完成后暂停" : request.kind === "pause_now" ? "暂停与修改请求" : "待 Codex 处理的修改";
    note.append(element("strong", "", label));
    if (request.message) note.append(element("p", "", text(request.message)));
    return note;
  }

  function preserveInteraction(container, render) {
    const open = new Map(Array.from(container.querySelectorAll("details[data-key]"), item => [item.dataset.key, item.open]));
    const active = document.activeElement;
    const focusKey = container.contains(active) ? active?.dataset?.focusKey : null;
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    render(open);
    if (focusKey) {
      const replacement = Array.from(container.querySelectorAll("[data-focus-key]")).find(item => item.dataset.focusKey === focusKey);
      replacement?.focus({ preventScroll: true });
    }
    window.scrollTo(scrollX, scrollY);
  }

  function issueElement(issue, openState, nodeId) {
    const id = text(issue.id);
    const title = text(issue.title, "执行情况");
    if (issue.status === "resolved") {
      const details = element("details", "resolved-note");
      details.dataset.key = `issue:${JSON.stringify([nodeId, id])}`;
      details.open = openState.get(details.dataset.key) ?? false;
      const summary = element("summary", "", `已解决 · ${title}`);
      summary.dataset.focusKey = details.dataset.key;
      details.append(summary, element("p", "", text(issue.resolution, text(issue.detail, "已解决"))));
      return details;
    }
    const kind = ["blocker", "branch", "decision", "verification"].includes(issue.kind) ? issue.kind : "blocker";
    const section = element("section", `issue issue--${kind}`);
    section.dataset.issueId = id;
    const heading = element("div", "issue-heading");
    const kindLabel = kind === "blocker" ? "卡点" : kind === "decision" ? "待你决定" : kind === "verification" ? "待验收" : issue.selected ? "路线调整" : "出现分支";
    heading.append(element("span", "issue-title", title), element("span", "issue-kind", kindLabel));
    section.append(heading);
    if (issue.detail) section.append(element("p", "issue-detail", text(issue.detail)));
    const options = Array.isArray(issue.options) ? issue.options.filter(option => typeof option === "string") : [];
    if (options.length) {
      const list = element("ul", "issue-options");
      for (const option of options) {
        const selected = option === issue.selected;
        const row = element("li", selected ? "option-selected" : "", option);
        if (selected) row.append(element("span", "option-selected-label", "已选"));
        list.append(row);
      }
      section.append(list);
    } else if (issue.selected) {
      section.append(element("p", "issue-detail", `当前选择：${issue.selected}`));
    }
    section.append(interventionButton(nodeId, id));
    return section;
  }

  function renderNodes(nodes) {
    const requests = Array.isArray(snapshot?.requests) ? snapshot.requests : [];
    const signature = JSON.stringify([nodes, requests, snapshot?.pauseAfterNodeId, canAppendDraft(), [...draftAttempts]]);
    if (signature === nodesSignature) return;
    nodesSignature = signature;
    preserveInteraction(ui.route, openState => {
      const fragment = document.createDocumentFragment();
      const nextNodeStates = new Map();
      for (let index = 0; index < nodes.length; index += 1) {
        const node = nodes[index];
        const status = Object.hasOwn(nodeLabels, node.status) ? node.status : "pending";
        const issues = Array.isArray(node.issues) ? node.issues : [];
        const openIssues = issues.filter(issue => issue.status !== "resolved");
        const pendingRequests = requests.filter(request => request.nodeId === node.id && request.status === "pending");
        const row = element("li", `node node--${status}`);
        row.dataset.nodeId = text(node.id);
        const state = window.ExecutionPlanModel.nodeState(node);
        if (state.attention) row.dataset.attention = state.attention;
        const details = element("details", "node-details");
        details.dataset.key = `node:${node.id}`;
        const defaultOpen = (!state.waitingVerification && (status === "in_progress" || status === "blocked" || openIssues.length > 0)) || pendingRequests.length > 0 || node.needsReview;
        const previous = previousNodeStates.get(node.id);
        details.open = manuallyToggledNodes.has(node.id) ? openState.get(details.dataset.key) ?? defaultOpen :
          previous && previous.status !== status ? defaultOpen : openState.get(details.dataset.key) ?? defaultOpen;
        if (openIssues.some(issue => issue.kind !== "verification" && !previous?.openIssues.has(issue.id))) details.open = true;
        if (pendingRequests.some(request => !previous?.pendingRequests?.has(request.id))) details.open = true;
        nextNodeStates.set(node.id, { status, openIssues: new Set(openIssues.map(issue => issue.id)), pendingRequests: new Set(pendingRequests.map(request => request.id)) });
        const summary = element("summary", "node-summary");
        summary.addEventListener("click", () => manuallyToggledNodes.add(node.id));
        summary.dataset.focusKey = details.dataset.key;
        const markerText = String(index + 1).padStart(2, "0");
        const marker = element("span", "node-marker", markerText);
        const label = element("span", "node-title", text(node.title, "未命名节点"));
        const badge = element("span", "node-state", nodeLabels[status]);
        if (state.label) badge.textContent = state.label;
        summary.append(marker, label, badge);
        const body = element("div", "node-body");
        if (node.summary) body.append(element("p", "node-description", text(node.summary)));
        if (node.acceptance) {
          const acceptance = element("details", "node-acceptance");
          acceptance.dataset.key = `acceptance:${node.id}`;
          acceptance.open = openState.get(acceptance.dataset.key) ?? false;
          const heading = element("summary", "", "验收要求");
          heading.dataset.focusKey = acceptance.dataset.key;
          acceptance.append(heading, element("p", "", text(node.acceptance)));
          body.append(acceptance);
        }
        if (node.needsReview) body.append(element("p", "node-review", "要求已变更，待重新验收。"));
        for (const issue of issues) body.append(issueElement(issue, openState, text(node.id)));
        for (const request of pendingRequests) body.append(requestElement(request));
        const evidence = evidenceElement(node.evidence, text(node.id), openState);
        if (evidence) body.append(evidence);
        if (!body.childNodes.length) body.append(element("p", "node-note-empty", status === "completed" ? "该节点已完成。" : status === "pending" ? "等待执行此节点。" : "暂无补充说明。"));
        details.append(summary, body);
        row.append(details, interventionButton(text(node.id)));
        fragment.append(row);
      }
      ui.route.replaceChildren(fragment);
      previousNodeStates = nextNodeStates;
    });
  }

  function renderHistory(history, archivedNodes) {
    const versions = Array.isArray(history) ? history : [];
    const archive = Array.isArray(archivedNodes) ? archivedNodes : [];
    ui.history.hidden = versions.length === 0 && archive.length === 0;
    const signature = JSON.stringify([versions, archive]);
    if (signature === historySignature) return;
    historySignature = signature;
    preserveInteraction(ui.history, openState => {
      const fragment = document.createDocumentFragment();
      if (versions.length) {
        const outer = element("details");
        outer.dataset.key = "history"; outer.open = openState.get("history") ?? false;
        const summary = element("summary", "", `历史版本 · ${versions.length} 个`);
        summary.dataset.focusKey = "history"; outer.append(summary);
        for (const version of versions.slice().reverse()) {
          const entry = element("details", "history-entry");
          entry.dataset.key = `version:${version.version}`; entry.open = openState.get(entry.dataset.key) ?? false;
          const heading = element("summary", "", `v${version.version ?? "—"} · ${text(version.title, "执行计划")}`);
          heading.dataset.focusKey = entry.dataset.key;
          entry.append(heading);
          if (version.reason) entry.append(element("p", "history-reason", text(version.reason)));
          const list = element("ol");
          for (const node of Array.isArray(version.nodes) ? version.nodes : []) {
            const row = element("li", "", text(node.title, "节点"));
            row.append(element("span", "history-node-state", nodeLabels[node.status] || "状态未知"));
            if (node.acceptance) row.append(element("p", "", `验收：${text(node.acceptance)}`));
            for (const issue of Array.isArray(node.issues) ? node.issues : []) row.append(element("p", "", `${issue.status === "resolved" ? "已解决" : "原有问题"}：${text(issue.title)}`));
            const evidence = evidenceElement(node.evidence, `history:${version.version}:${node.id}`, openState);
            if (evidence) row.append(evidence);
            list.append(row);
          }
          entry.append(list); outer.append(entry);
        }
        fragment.append(outer);
      }
      if (archive.length) {
        const details = element("details");
        details.dataset.key = "archive"; details.open = openState.get("archive") ?? false;
        const summary = element("summary", "", `已归档节点 · ${archive.length} 项`);
        summary.dataset.focusKey = "archive";
        const list = element("ul", "archive-list");
        for (const value of archive) {
          const node = value.node || value;
          const row = element("li", "", text(node.title, "已归档节点"));
          if (node.summary) row.append(element("p", "", text(node.summary)));
          for (const issue of Array.isArray(node.issues) ? node.issues : []) row.append(element("p", "", `${issue.status === "resolved" ? "已解决" : "归档时未解决"}：${text(issue.title)}`));
          const evidence = evidenceElement(node.evidence, `archive:${node.id}`, openState);
          if (evidence) row.append(evidence);
          list.append(row);
        }
        details.append(summary, list); fragment.append(details);
      }
      ui.history.replaceChildren(fragment);
    });
  }

  function renderChanges(changes) {
    const list = Array.isArray(changes) ? changes : [];
    ui.changes.hidden = list.length === 0;
    const signature = JSON.stringify(list);
    if (signature === changesSignature) return;
    changesSignature = signature;
    preserveInteraction(ui.changes, openState => {
      ui.changes.replaceChildren();
      if (!list.length) return;
      const details = element("details", "");
      details.dataset.key = "plan-changes";
      details.open = openState.get("plan-changes") ?? false;
      const summary = element("summary", "", `计划调整 · ${list.length} 次`);
      summary.dataset.focusKey = "plan-changes";
      details.append(summary);
      for (const change of list.slice().reverse()) {
        const entry = element("div", "change-entry");
        const at = timeLabel(change.at);
        if (at) entry.append(element("p", "change-date", at));
        entry.append(element("p", "", text(change.reason, "执行节点已调整")));
        for (const [field, prefix] of [["added", "新增"], ["removed", "移除"]]) {
          const values = Array.isArray(change[field]) ? change[field] : [];
          if (values.length) entry.append(element("p", "change-items", `${prefix}：${values.map(value => typeof value === "string" ? value : text(value?.title, text(value?.id))).join("、")}`));
        }
        details.append(entry);
      }
      ui.changes.append(details);
    });
  }

  function renderSnapshot(next) {
    if (!next || next.schemaVersion !== 1 || !Array.isArray(next.nodes) || typeof next.sessionId !== "string") throw new Error("返回的执行状态格式不完整。");
    snapshot = next;
    sessionId = next.sessionId;
    revision = text(next.revision) || null;
    ui.title.textContent = text(next.title, "当前任务");
    ui.picker.hidden = true;
    ui.error.hidden = true;
    ui.planVersion.textContent = Number.isInteger(next.planVersion) ? `Plan v${next.planVersion}` : "Plan 待同步";
    updateRuntime(next.runtime);
    updateTimestamp(next.updatedAt);
    const nodes = next.nodes;
    const rawTotal = next.progress?.total;
    const allCount = Number.isInteger(rawTotal) && rawTotal >= 0 ? rawTotal : nodes.length;
    const rawCancelled = next.progress?.cancelled;
    const cancelled = Number.isInteger(rawCancelled) && rawCancelled >= 0 ? Math.min(rawCancelled, allCount) : nodes.filter(node => node.status === "cancelled").length;
    const total = Math.max(0, allCount - cancelled);
    const rawCompleted = next.progress?.completed;
    const completed = Number.isInteger(rawCompleted) && rawCompleted >= 0 ? Math.min(rawCompleted, total) : nodes.filter(node => node.status === "completed").length;
    const hasProgress = total > 0 && Number.isFinite(next.progress?.percent);
    const percent = hasProgress ? Math.max(0, Math.min(100, Math.round(next.progress.percent))) : null;
    ui.progress.hidden = !nodes.length;
    ui.route.hidden = !nodes.length;
    ui.empty.hidden = nodes.length > 0;
    if (!nodes.length) showEmpty("等待同步执行计划", "Agent 同步主要节点后，这里会自动显示进度。");
    ui.count.textContent = total > 0 ? `已完成 ${completed} / ${total} 个节点` : "没有待执行的节点";
    ui.percent.textContent = percent === null ? "—" : `${percent}%`;
    ui.fill.style.transform = `scaleX(${(percent ?? 0) / 100})`;
    if (percent === null) {
      ui.track.removeAttribute("aria-valuenow");
      ui.track.setAttribute("aria-valuetext", total ? "进度尚未提供" : "没有待执行的节点");
    } else {
      ui.track.setAttribute("aria-valuenow", String(percent));
      ui.track.setAttribute("aria-valuetext", `已完成 ${completed} / ${total} 个主要节点，${percent}%`);
    }
    const openIssues = nodes.flatMap(node => (Array.isArray(node.issues) ? node.issues : []).filter(issue => issue.status !== "resolved"));
    const blocking = openIssues.filter(issue => issue.kind === "blocker").length;
    const decisions = openIssues.filter(issue => issue.kind === "decision").length;
    const verifications = openIssues.filter(issue => issue.kind === "verification").length;
    const pendingChanges = (Array.isArray(next.requests) ? next.requests : []).filter(request => request.status === "pending" && request.kind === "modify").length;
    const active = nodes.find(node => node.status === "blocked") || nodes.find(node => node.status === "in_progress");
    const context = blocking ? `${blocking} 处卡点，详情见对应节点` : decisions ? `${decisions} 处等待你决定` : pendingChanges ? `${pendingChanges} 项修改等待 Codex 处理` : verifications ? `${verifications} 项待验收` : active ? `当前：${text(active.title)}` : total > 0 && completed === total ? "所有主要节点已完成" : total > 0 ? "等待执行下一节点" : "已取消的节点不计入总体进度";
    const announcement = `${ui.count.textContent}。${cancelled ? `${context} · ${cancelled} 项已取消` : context}`;
    if (ui.announcement.textContent !== announcement) ui.announcement.textContent = announcement;
    renderNodes(nodes);
    renderChanges(next.planChanges);
    renderHistory(next.history, next.archivedNodes);
  }

  function applyPayload(payload) {
    const value = unwrap(payload);
    boundSessionId = value?.viewerBinding?.verified === true ? value.viewerBinding.sessionId : null;
    if (payload?.isError || payload?.result?.isError) throw new Error((payload.content || []).map(item => item.text || "").join(" ") || "读取执行状态失败。");
    if (value?.viewerState === "select-session") {
      receivedInitial = true;
      clearTimer();
      sessionId = null;
      ui.picker.hidden = false;
      ui.progress.hidden = true;
      ui.route.hidden = true;
      ui.changes.hidden = true;
      ui.history.hidden = true;
      showEmpty("选择一个任务以查看进度", "当前面板未收到可验证的任务上下文。");
      if (Array.isArray(value.sessions)) renderSessions(value.sessions);
      else void loadSessions();
      return;
    }
    if (value?.unchanged === true) {
      if (typeof value.revision === "string") revision = value.revision;
      updateRuntime(value.runtime);
      if (snapshot) renderNodes(snapshot.nodes);
      ui.error.hidden = true;
      return;
    }
    renderSnapshot(value?.snapshot || value);
    receivedInitial = true;
    schedule();
  }

  function renderSessions(sessions) {
    ui.select.replaceChildren();
    for (const session of sessions) {
      const id = text(session.sessionId, text(session.id));
      if (!id) continue;
      const option = element("option", "", text(session.title, id));
      option.value = id;
      ui.select.append(option);
    }
    ui.selectButton.disabled = !ui.select.options.length;
    ui.pickerNote.textContent = ui.select.options.length ? "查看进度不会启动新的 Agent 轮次。" : "没有找到可查看的本地任务。";
  }

  async function readUpdate(id, currentRevision) {
    if (preview) {
      const query = new URLSearchParams();
      if (id) query.set("sessionId", id);
      if (currentRevision) query.set("revision", currentRevision);
      activeFetch = new AbortController();
      const result = await fetch(`/api/state?${query}`, { cache: "no-store", signal: activeFetch.signal });
      if (!result.ok) throw new Error(`读取状态失败（${result.status}）。`);
      return result.json();
    }
    return host.callTool("get_execution_panel_update", { sessionId: id, ...(currentRevision ? { revision: currentRevision } : {}) });
  }

  function showError(error) {
    if (stopped) return;
    runtimeRunning = false;
    updateActivity();
    ui.error.hidden = false;
    ui.errorText.textContent = snapshot ? "状态刷新失败，保留上次进度；正在重连。" : text(error?.message, "暂时无法读取执行状态，正在重试。");
    ui.runtime.textContent = "状态连接中断";
    ui.runtimeDot.dataset.state = "disconnected";
  }

  async function refresh() {
    if (stopped || busy || document.hidden || !sessionId) return;
    busy = true;
    const requestedSession = sessionId;
    try {
      pendingRead = readUpdate(requestedSession, revision);
      const result = await pendingRead;
      if (stopped || sessionId !== requestedSession) return;
      applyPayload(result);
      failures = 0;
    } catch (error) {
      if (stopped || sessionId !== requestedSession) return;
      failures = Math.min(failures + 1, 8);
      showError(error);
    } finally {
      busy = false;
      pendingRead = null;
      activeFetch = null;
      schedule(Math.min(30000, 1000 * (2 ** failures)));
    }
  }

  async function loadSessions() {
    if (stopped || listing) return;
    listing = true;
    ui.selectButton.disabled = true;
    try {
      let result;
      if (preview) {
        activeFetch = new AbortController();
        const response = await fetch("/api/sessions", { cache: "no-store", signal: activeFetch.signal });
        if (!response.ok) throw new Error(`读取任务列表失败（${response.status}）。`);
        result = await response.json();
      } else result = await host.callTool("list_execution_sessions", {});
      if (stopped) return;
      const value = unwrap(result);
      if (result?.isError) throw new Error("无法读取任务列表。");
      renderSessions(Array.isArray(value?.sessions) ? value.sessions : []);
    } catch (error) {
      showError(error);
      ui.pickerNote.textContent = "任务列表读取失败，可点击重试。";
    } finally { listing = false; activeFetch = null; }
  }

  function applyContext(context) {
    window.executionPanelTheme?.setHostTheme(context?.theme);
    const locale = text(context?.locale);
    if (locale) document.documentElement.lang = locale;
    if (snapshot) { nodesSignature = ""; renderNodes(snapshot.nodes); }
  }

  function shutdown() {
    stopped = true;
    updateActivity();
    clearTimer();
    activeFetch?.abort();
  }

  ui.selectButton.addEventListener("click", () => {
    if (!ui.select.value) return;
    sessionId = ui.select.value;
    revision = null;
    snapshot = null;
    nodesSignature = "";
    changesSignature = "";
    previousNodeStates = new Map();
    historySignature = "";
    ui.picker.hidden = true;
    showEmpty("正在读取执行进度", "进度将与所选任务保持同步。");
    schedule(0);
  });
  ui.retry.addEventListener("click", () => {
    failures = 0;
    ui.error.hidden = true;
    if (sessionId) schedule(0);
    else void loadSessions();
  });
  document.addEventListener("visibilitychange", () => {
    updateActivity();
    clearTimer();
    if (!document.hidden && sessionId) schedule(0);
  });
  window.addEventListener("pagehide", shutdown, { once: true });

  if (preview) {
    if (sessionId) schedule(0);
    else {
      ui.picker.hidden = false;
      showEmpty("选择一个任务以查看进度", "从本地任务列表中选择要观察的执行过程。");
      void loadSessions();
    }
  } else if (typeof window.createTrajectoryHost === "function") {
    host = window.createTrajectoryHost({
      onResult: result => { try { applyPayload(result); } catch (error) { showError(error); } },
      onContext: applyContext,
      onError: showError,
      onTeardown: shutdown,
    });
    void host.start().then(() => {
      if (!receivedInitial && !stopped) {
        ui.picker.hidden = false;
        showEmpty("选择一个任务以查看进度", "面板未收到当前任务的状态，请选择本地任务。");
        void loadSessions();
      }
    });
  } else {
    showEmpty("等待 Codex 面板宿主", "请从 Codex 当前聊天的右侧工具入口打开执行面板。");
    ui.runtime.textContent = "尚未连接宿主";
  }
})();
