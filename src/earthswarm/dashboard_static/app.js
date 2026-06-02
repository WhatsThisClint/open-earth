const token = window.__OPENEARTH_SESSION_TOKEN__ || window.__EARTHSWARM_SESSION_TOKEN__;
const projectRoot = window.__OPENEARTH_PROJECT_ROOT__ || window.__EARTHSWARM_PROJECT_ROOT__;

const state = {
  agents: [],
  workflows: [],
  mcps: [],
  providers: [],
  dataRecipes: [],
  evidenceRecords: [],
  claimRecords: [],
  reviewItems: [],
  fieldTasks: [],
  memoryRecords: [],
  graphNetwork: null,
  selectedAgent: null,
};

const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-OpenEarth-Session-Token", token);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...options, headers });
  const text = await res.text();
  const payload = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(payload.detail || `${res.status} ${res.statusText}`);
  return payload;
}

function lines(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

function splitLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function toast(message, kind = "ok") {
  const el = qs("#toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 3600);
}

function setView(name) {
  qsa(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  qsa(".view").forEach((view) => view.classList.toggle("active", view.id === `${name}-view`));
  qs("#view-title").textContent = {
    agents: "Agents",
    workflows: "Workflows",
    data: "Data Sources",
    mcps: "MCP Servers",
    providers: "Provider Routes",
    evidence: "Evidence",
    graph: "Graph Memory",
    memory: "Learning Memory",
    review: "Human Review",
    run: "Dry Run",
  }[name] || "Open Earth";
  if (name === "graph" && !state.graphNetwork) queryGraph().catch((err) => toast(err.message, "error"));
}

async function refresh() {
  const [status, agents, workflows, mcps, providers, dataRecipes, evidence, claims, review, field, memory] = await Promise.all([
    api("/api/status"),
    api("/api/agents"),
    api("/api/workflows"),
    api("/api/mcps"),
    api("/api/providers"),
    api("/api/data/recipes"),
    api("/api/evidence?status=all&limit=25"),
    api("/api/claims?status=all&limit=25"),
    api("/api/review?status=open&limit=25"),
    api("/api/field?status=all&limit=25"),
    api("/api/memory?status=active&limit=25"),
  ]);
  state.agents = agents.agents;
  state.workflows = workflows.workflows;
  state.mcps = mcps.mcps;
  state.providers = providers.profiles;
  state.dataRecipes = dataRecipes.recipes;
  state.evidenceRecords = evidence.records;
  state.claimRecords = claims.records;
  state.reviewItems = review.items;
  state.fieldTasks = field.tasks;
  state.memoryRecords = memory.records;
  renderStatus(status);
  renderAgents();
  renderWorkflows();
  renderDataRecipes();
  renderMcps();
  renderProviders();
  renderEvidence();
  renderClaims();
  renderRunWorkflows();
  renderReview();
  renderField();
  renderMemory();
}

function renderStatus(status) {
  qs("#project-root").textContent = projectRoot || status.project_root;
  qs("#status-panel").innerHTML = `
    <div><strong class="${status.valid ? "ok" : "error"}">${status.valid ? "Valid project" : "Needs attention"}</strong></div>
    <div>Agents: ${status.counts.agents}</div>
    <div>Workflows: ${status.counts.workflows}</div>
    <div>MCPs: ${status.counts.mcps}</div>
    <div>Graph nodes: ${status.graph.nodes}</div>
    <div>Evidence: ${Object.values(status.evidence.evidence || {}).reduce((a, b) => a + b, 0)}</div>
    <div>Claims: ${Object.values(status.evidence.claims || {}).reduce((a, b) => a + b, 0)}</div>
    <div>Providers: ${status.providers || 0}</div>
    <div>Open reviews: ${status.review.open || 0}</div>
    <div>Open field tasks: ${status.field.open || 0}</div>
    <div>Active memories: ${status.memory.active || 0}</div>
    ${status.errors.length ? `<div class="error">${status.errors.length} validation issue(s)</div>` : ""}
  `;
}

function renderAgents() {
  const list = qs("#agent-list");
  list.innerHTML = "";
  state.agents.forEach((agent) => {
    const button = document.createElement("button");
    button.className = `list-item ${state.selectedAgent === agent.slug ? "active" : ""}`;
    const summary = truncate(agent.description || agent.role || agent.slug, 112);
    button.innerHTML = `
      <strong>${escapeHtml(agent.name)}</strong>
      <span>${escapeHtml(agent.role || agent.slug)}</span>
      <small>${escapeHtml(summary)}</small>
    `;
    button.addEventListener("click", () => selectAgent(agent.slug));
    list.appendChild(button);
  });
  if (!state.selectedAgent && state.agents[0]) selectAgent(state.agents[0].slug);
}

async function selectAgent(slug) {
  state.selectedAgent = slug;
  renderAgents();
  const payload = await api(`/api/agents/${encodeURIComponent(slug)}`);
  const form = qs("#agent-form");
  qs("#agent-editor-title").textContent = payload.name;
  renderAgentProfile(payload);
  form.name.value = payload.name || "";
  form.role.value = payload.role || "";
  form.description.value = payload.description || "";
  form.model.value = payload.model || "";
  form.reasoning.value = payload.reasoning || "medium";
  form.tools.value = lines(payload.tools);
  form.mcp_servers.value = lines(payload.mcp_servers);
  form.skills.value = lines(payload.skills);
  form.outputs.value = lines(payload.outputs);
  form.prompt.value = payload.prompt || "";
}

function renderAgentProfile(agent) {
  const profile = qs("#agent-profile");
  if (!profile) return;
  const chips = [
    `model: ${agent.model || "unset"}`,
    `reasoning: ${agent.reasoning || "unset"}`,
    `${(agent.tools || []).length} tools`,
    `${(agent.skills || []).length} skills`,
    `${(agent.outputs || []).length} outputs`,
  ];
  const toolChips = (agent.tools || []).slice(0, 8).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
  const skillChips = (agent.skills || []).slice(0, 8).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
  profile.innerHTML = `
    <div>
      <p class="eyebrow">${escapeHtml(agent.slug)}</p>
      <h3>${escapeHtml(agent.role || "Agent role")}</h3>
      <p>${escapeHtml(agent.description || "No description yet.")}</p>
    </div>
    <div class="chips">${chips.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>
    ${toolChips ? `<div><p class="meta">Tools</p><div class="chips">${toolChips}</div></div>` : ""}
    ${skillChips ? `<div><p class="meta">Skills</p><div class="chips">${skillChips}</div></div>` : ""}
  `;
}

async function saveAgent() {
  if (!state.selectedAgent) return;
  const form = qs("#agent-form");
  const payload = {
    name: form.name.value,
    role: form.role.value,
    description: form.description.value,
    model: form.model.value,
    reasoning: form.reasoning.value,
    tools: splitLines(form.tools.value),
    mcp_servers: splitLines(form.mcp_servers.value),
    skills: splitLines(form.skills.value),
    outputs: splitLines(form.outputs.value),
    prompt: form.prompt.value,
  };
  const result = await api(`/api/agents/${encodeURIComponent(state.selectedAgent)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  toast(result.ok ? "Agent saved and project validated." : `Saved with ${result.errors.length} validation issue(s).`, result.ok ? "ok" : "warn");
  await refresh();
  await selectAgent(state.selectedAgent);
}

function renderWorkflows() {
  const list = qs("#workflow-list");
  list.innerHTML = "";
  state.workflows.forEach((workflow) => {
    const card = document.createElement("article");
    card.className = "card";
    const stepCount = workflow.steps?.length || 0;
    card.innerHTML = `
      <h3>${workflow.name}</h3>
      <p class="meta">${workflow.description || workflow.slug}</p>
      <div class="chips"><span class="chip">${stepCount} steps</span>${(workflow.outputs || []).map((o) => `<span class="chip">${o}</span>`).join("")}</div>
    `;
    list.appendChild(card);
  });
}

function renderDataRecipes() {
  const list = qs("#data-recipe-list");
  const select = qs("#data-stage-recipe");
  if (!list || !select) return;
  list.innerHTML = "";
  select.innerHTML = state.dataRecipes.map((recipe) => `<option value="${escapeAttr(recipe.id)}">${escapeHtml(recipe.name)}</option>`).join("");
  if (!state.dataRecipes.length) {
    list.innerHTML = `<p class="meta">No acquisition recipes found.</p>`;
    return;
  }
  state.dataRecipes.forEach((recipe) => {
    const card = document.createElement("article");
    card.className = "card";
    const blockers = (recipe.blockers || []).slice(0, 2).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
    card.innerHTML = `
      <h3>${escapeHtml(recipe.name)}</h3>
      <p class="meta">${escapeHtml(recipe.id)} - ${escapeHtml(recipe.theme)} - ${escapeHtml(recipe.access_method)}</p>
      <p>${escapeHtml(recipe.description || "")}</p>
      <div class="chips">
        <span class="chip">${recipe.can_download ? "direct download possible" : "stage or blocker"}</span>
        <span class="chip">${escapeHtml(recipe.auth || "auth unknown")}</span>
        <span class="chip">${escapeHtml(recipe.resolution || "resolution varies")}</span>
      </div>
      ${blockers ? `<div class="chips">${blockers}</div>` : ""}
    `;
    list.appendChild(card);
  });
}

async function stageDataRecipe() {
  const form = qs("#data-stage-form");
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.allow_download = Boolean(form.allow_download.checked);
  const result = await api("/api/data/stage", { method: "POST", body: JSON.stringify(payload) });
  qs("#data-stage-output").textContent = JSON.stringify(result.result, null, 2);
  toast(result.result.status === "staged" ? "Dataset staged and evidence recorded." : "Blocker recorded as evidence.", result.result.status === "staged" ? "ok" : "warn");
  await refresh();
}

function renderMcps() {
  const list = qs("#mcp-list");
  list.innerHTML = "";
  state.mcps.forEach((mcp) => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${mcp.name}</h3>
      <p class="meta">${mcp.transport} ${mcp.enabled ? "enabled" : "disabled"}</p>
      <div class="chips">
        <span class="chip">${mcp.approval || "ask"}</span>
        ${mcp.command ? `<span class="chip">${mcp.command}</span>` : ""}
        ${mcp.url ? `<span class="chip">${mcp.url}</span>` : ""}
      </div>
    `;
    list.appendChild(card);
  });
}

function renderProviders() {
  const list = qs("#provider-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.providers.length) {
    list.innerHTML = `<p class="meta">No provider routes found.</p>`;
    return;
  }
  state.providers.forEach((provider) => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${escapeHtml(provider.id)}</h3>
      <p class="meta">${escapeHtml(provider.provider)} - ${escapeHtml(provider.runtime)} - ${escapeHtml(provider.default_model || "model unset")}</p>
      <div class="chips">
        <span class="chip ${provider.available ? "ok-chip" : "warn-chip"}">${provider.available ? "available" : "needs setup"}</span>
        <span class="chip">${escapeHtml(provider.auth || "auth unknown")}</span>
        ${provider.role ? `<span class="chip">role: ${escapeHtml(provider.role)}</span>` : ""}
      </div>
      <p>${escapeHtml(provider.detail || provider.notes || "")}</p>
    `;
    list.appendChild(card);
  });
}

function renderEvidence() {
  const list = qs("#evidence-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.evidenceRecords.length) {
    list.innerHTML = `<p class="meta">No evidence records yet. Run a workflow or stage data.</p>`;
    return;
  }
  state.evidenceRecords.forEach((record) => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${escapeHtml(record.title)}</h3>
      <p class="meta">${escapeHtml(record.id)} - ${escapeHtml(record.evidence_type)} - ${escapeHtml(record.reviewer_status)}</p>
      <div class="chips">
        ${record.source ? `<span class="chip">${escapeHtml(record.source)}</span>` : ""}
        ${record.resolution ? `<span class="chip">${escapeHtml(record.resolution)}</span>` : ""}
        ${record.crs ? `<span class="chip">${escapeHtml(record.crs)}</span>` : ""}
      </div>
      ${record.local_path ? `<p class="meta">${escapeHtml(record.local_path)}</p>` : ""}
      ${record.uncertainty ? `<p>${escapeHtml(record.uncertainty)}</p>` : ""}
    `;
    list.appendChild(card);
  });
}

function renderClaims() {
  const list = qs("#claim-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.claimRecords.length) {
    list.innerHTML = `<p class="meta">No claim records yet.</p>`;
    return;
  }
  state.claimRecords.forEach((record) => {
    const card = document.createElement("article");
    card.className = "card";
    const evidence = (record.evidence_ids || []).slice(0, 5).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
    card.innerHTML = `
      <h3>${escapeHtml(record.claim_type)}</h3>
      <p class="meta">${escapeHtml(record.id)} - ${escapeHtml(record.confidence)} - ${escapeHtml(record.reviewer_status)}${record.agent ? ` - ${escapeHtml(record.agent)}` : ""}</p>
      <p>${escapeHtml(record.statement)}</p>
      ${evidence ? `<div class="chips">${evidence}</div>` : ""}
    `;
    list.appendChild(card);
  });
}

function renderRunWorkflows() {
  const select = qs("#run-workflow");
  select.innerHTML = state.workflows.map((workflow) => `<option value="${workflow.slug}">${workflow.name}</option>`).join("");
}

async function validateProject() {
  const result = await api("/api/validate", { method: "POST", body: "{}" });
  toast(result.ok ? "Manifest validation passed." : result.errors.join(" | "), result.ok ? "ok" : "error");
  await refresh();
}

async function createAgent(event) {
  event.preventDefault();
  const form = qs("#new-agent-form");
  const payload = Object.fromEntries(new FormData(form).entries());
  const result = await api("/api/agents", { method: "POST", body: JSON.stringify(payload) });
  qs("#new-agent-dialog").close();
  form.reset();
  toast("Agent created.");
  state.selectedAgent = result.agent.slug;
  await refresh();
  await selectAgent(result.agent.slug);
}

async function ingestGraph() {
  const result = await api("/api/graph/ingest", { method: "POST", body: "{}" });
  toast(`Graph rebuilt: ${result.stats.nodes} nodes, ${result.stats.chunks} chunks.`);
  await refresh();
}

async function evaluateTrigger() {
  const form = qs("#trigger-form");
  const payload = Object.fromEntries(new FormData(form).entries());
  const value = Number(payload.value);
  if (!Number.isNaN(value) && payload.value !== "") payload.value = value;
  payload[payload.metric] = payload.value;
  const result = await api("/api/trigger/evaluate", { method: "POST", body: JSON.stringify(payload) });
  qs("#trigger-output").textContent = JSON.stringify(result, null, 2);
  toast(result.matches.length ? `Matched ${result.matches.length} trigger rule(s).` : "No trigger matches.", result.matches.length ? "warn" : "ok");
  await refresh();
}

function renderReview() {
  const list = qs("#review-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.reviewItems.length) {
    list.innerHTML = `<p class="meta">No open review items.</p>`;
    return;
  }
  state.reviewItems.forEach((item) => {
    const card = document.createElement("article");
    card.className = "card";
    const suggested = item.recommendation?.suggested_action || "";
    card.innerHTML = `
      <h3>${item.title}</h3>
      <p class="meta">${item.id} - ${item.severity} - ${item.status}</p>
      ${suggested ? `<p>${suggested}</p>` : ""}
      <div class="review-actions">
        <button data-action="approve" data-id="${item.id}">Approve</button>
        <button class="secondary" data-action="correct" data-id="${item.id}">Correct</button>
        <button class="secondary" data-action="field-check" data-id="${item.id}">Field Check</button>
        <button class="secondary" data-action="uncertain" data-id="${item.id}">Uncertain</button>
        <button class="secondary" data-action="reject" data-id="${item.id}">Reject</button>
      </div>
    `;
    list.appendChild(card);
  });
}

async function reviewAction(id, action) {
  const result = await api(`/api/review/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
    body: JSON.stringify({ note: `Dashboard marked ${action}`, actor: "dashboard" }),
  });
  toast(result.field_task ? `Review item marked ${action}; field task created.` : `Review item marked ${action}.`);
  await refresh();
}

function renderField() {
  const list = qs("#field-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.fieldTasks.length) {
    list.innerHTML = `<p class="meta">No field validation tasks.</p>`;
    return;
  }
  state.fieldTasks.forEach((task) => {
    const card = document.createElement("article");
    card.className = "card";
    const expected = (task.expected_evidence || []).map((item) => `<span class="chip">${item}</span>`).join("");
    const canStart = task.status === "open";
    const canComplete = task.status === "open" || task.status === "in_progress";
    const canCancel = task.status === "open" || task.status === "in_progress";
    card.innerHTML = `
      <h3>${task.title}</h3>
      <p class="meta">${task.id} - ${task.method} - ${task.status}${task.location ? ` - ${task.location}` : ""}</p>
      <p>${task.question}</p>
      ${expected ? `<div class="chips">${expected}</div>` : ""}
      ${task.observations ? `<p><strong>Observed:</strong> ${task.observations}</p>` : ""}
      <div class="review-actions">
        ${canStart ? `<button class="secondary" data-action="start" data-id="${task.id}">Start</button>` : ""}
        ${canComplete ? `<button data-action="complete" data-id="${task.id}">Complete</button>` : ""}
        ${canCancel ? `<button class="secondary" data-action="cancel" data-id="${task.id}">Cancel</button>` : ""}
      </div>
    `;
    list.appendChild(card);
  });
}

async function fieldAction(id, action) {
  const body = { actor: "dashboard", note: `Dashboard marked field task ${action}` };
  if (action === "complete") {
    body.observations = "Completed from dashboard. Add detailed field notes, photos, GPS tracks, and measurements through CLI or project artifacts.";
    body.confidence_update = "Field evidence has been added; the related review item should be revisited.";
  }
  await api(`/api/field/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  toast(`Field task marked ${action}.`);
  await refresh();
}

function renderMemory() {
  const list = qs("#memory-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.memoryRecords.length) {
    list.innerHTML = `<p class="meta">No active learning memory yet.</p>`;
    return;
  }
  state.memoryRecords.forEach((record) => {
    const card = document.createElement("article");
    card.className = "card";
    const applies = (record.applies_to || []).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
    const tags = (record.tags || []).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
    card.innerHTML = `
      <h3>${escapeHtml(record.title)}</h3>
      <p class="meta">${escapeHtml(record.id)} - ${escapeHtml(record.kind)} - ${escapeHtml(record.confidence)}</p>
      <p>${escapeHtml(record.body)}</p>
      ${applies ? `<div class="chips">${applies}</div>` : ""}
      ${tags ? `<div class="chips">${tags}</div>` : ""}
      <div class="review-actions">
        <button class="secondary" data-action="archive" data-id="${record.id}">Archive</button>
        <button class="secondary" data-action="supersede" data-id="${record.id}">Supersede</button>
      </div>
    `;
    list.appendChild(card);
  });
}

async function saveMemory(event) {
  event.preventDefault();
  const form = qs("#memory-form");
  const payload = {
    kind: form.kind.value,
    confidence: form.confidence.value,
    title: form.title.value,
    body: form.body.value,
    source: "dashboard",
    created_by: "human",
    applies_to: splitLines(form.applies_to.value),
    tags: splitLines(form.tags.value),
    evidence_refs: splitLines(form.evidence_refs.value),
  };
  if (!payload.title.trim() || !payload.body.trim()) {
    toast("Learning memory needs a title and body.", "warn");
    return;
  }
  await api("/api/memory", { method: "POST", body: JSON.stringify(payload) });
  form.reset();
  toast("Learning memory added and indexed.");
  await refresh();
  const q = payload.applies_to[0] || payload.tags[0] || payload.title;
  qs("#graph-query-input").value = q;
  state.graphNetwork = null;
}

async function memoryAction(id, action) {
  await api(`/api/memory/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
    body: JSON.stringify({ actor: "dashboard", note: `Dashboard marked learning memory ${action}` }),
  });
  toast(`Learning memory marked ${action}.`);
  await refresh();
}

async function queryGraph() {
  const q = qs("#graph-query-input").value.trim();
  const [payload, network] = await Promise.all([
    api(`/api/graph/query?q=${encodeURIComponent(q)}&limit=12`),
    api(`/api/graph/network?q=${encodeURIComponent(q)}&limit=48&edge_limit=140`),
  ]);
  state.graphNetwork = network;
  renderGraphNetwork(network);
  const results = qs("#graph-results");
  results.innerHTML = "";
  payload.results.forEach((item) => {
    const el = document.createElement("article");
    el.className = "result";
    if (item.type === "node") {
      el.innerHTML = `<h3>${escapeHtml(item.label)}</h3><p class="meta">${escapeHtml(item.id)} - ${escapeHtml(item.kind)}</p>`;
    } else {
      el.innerHTML = `<h3>${escapeHtml(item.title)}</h3><p class="meta">${escapeHtml(item.path || item.node_id || "")}</p><p>${escapeHtml(item.snippet || "")}</p>`;
    }
    results.appendChild(el);
  });
  if (!payload.results.length) results.innerHTML = `<p class="meta">No matches yet.</p>`;
}

function renderGraphNetwork(network) {
  const canvas = qs("#graph-canvas");
  if (!canvas) return;
  if (!network.nodes.length) {
    canvas.innerHTML = `<div class="graph-empty"><p class="meta">No graph nodes matched.</p></div>`;
    qs("#graph-node-details").innerHTML = `<h3>Knowledge Graph</h3><p class="meta">No matching nodes. Rebuild memory or try a broader query.</p>`;
    return;
  }
  const width = 1000;
  const height = 580;
  const positions = graphPositions(network.nodes, width, height);
  const edgeHtml = network.edges
    .filter((edge) => positions[edge.source] && positions[edge.target])
    .map((edge, index) => {
      const source = positions[edge.source];
      const target = positions[edge.target];
      const label = network.edges.length <= 45 && index % 2 === 0 ? graphEdgeLabel(edge, source, target) : "";
      return `
        <line class="graph-edge" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"></line>
        ${label}
      `;
    })
    .join("");
  const nodeHtml = network.nodes
    .map((node) => {
      const pos = positions[node.id];
      const color = graphColor(node.kind);
      const radius = node.seed ? 11 : 8;
      const label = truncate(node.label || node.id, 24);
      return `
        <g class="graph-node ${node.seed ? "seed" : ""}" data-id="${escapeAttr(node.id)}" transform="translate(${pos.x}, ${pos.y})">
          <circle r="${radius}" fill="${color}"></circle>
          <text x="${radius + 5}" y="4">${escapeHtml(label)}</text>
          <title>${escapeHtml(node.id)} (${escapeHtml(node.kind)})</title>
        </g>
      `;
    })
    .join("");
  const kinds = [...new Set(network.nodes.map((node) => node.kind))].sort();
  const legend = kinds.map((kind, index) => {
    const x = 20 + (index % 4) * 170;
    const y = height - 24 - Math.floor(index / 4) * 22;
    return `<g transform="translate(${x}, ${y})"><circle r="6" fill="${graphColor(kind)}"></circle><text x="12" y="4">${escapeHtml(kind)}</text></g>`;
  }).join("");
  canvas.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Open Earth knowledge graph">
      <rect x="0" y="0" width="${width}" height="${height}" fill="#fbfcfa"></rect>
      ${edgeHtml}
      ${nodeHtml}
      <g class="graph-legend">${legend}</g>
    </svg>
  `;
  canvas.querySelectorAll(".graph-node").forEach((nodeEl) => {
    nodeEl.addEventListener("click", () => {
      const node = network.nodes.find((item) => item.id === nodeEl.dataset.id);
      if (node) renderGraphNodeDetails(node, network);
    });
  });
  renderGraphNodeDetails(network.nodes.find((node) => node.seed) || network.nodes[0], network);
}

function graphPositions(nodes, width, height) {
  const positions = {};
  const seeds = nodes.filter((node) => node.seed);
  const others = nodes.filter((node) => !node.seed);
  const center = { x: width / 2 - 60, y: height / 2 - 20 };
  placeCircle(positions, seeds, center.x, center.y, Math.max(35, 20 + seeds.length * 5));

  const groups = {};
  others.forEach((node) => {
    if (!groups[node.kind]) groups[node.kind] = [];
    groups[node.kind].push(node);
  });
  const kinds = Object.keys(groups).sort();
  const ringRadiusX = Math.min(360, width / 2 - 130);
  const ringRadiusY = Math.min(205, height / 2 - 115);
  kinds.forEach((kind, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(1, kinds.length);
    const anchorX = center.x + Math.cos(angle) * ringRadiusX;
    const anchorY = center.y + Math.sin(angle) * ringRadiusY;
    placeCircle(positions, groups[kind], anchorX, anchorY, Math.max(28, 12 + groups[kind].length * 4));
  });
  return positions;
}

function placeCircle(positions, nodes, cx, cy, radius) {
  if (!nodes.length) return;
  if (nodes.length === 1) {
    positions[nodes[0].id] = { x: Math.round(cx), y: Math.round(cy) };
    return;
  }
  nodes.forEach((node, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / nodes.length;
    positions[node.id] = {
      x: Math.round(cx + Math.cos(angle) * radius),
      y: Math.round(cy + Math.sin(angle) * radius),
    };
  });
}

function graphEdgeLabel(edge, source, target) {
  const x = Math.round((source.x + target.x) / 2);
  const y = Math.round((source.y + target.y) / 2);
  return `<text class="graph-edge-label" x="${x}" y="${y}">${escapeHtml(edge.kind)}</text>`;
}

function renderGraphNodeDetails(node, network) {
  const connected = network.edges.filter((edge) => edge.source === node.id || edge.target === node.id);
  const detail = qs("#graph-node-details");
  detail.innerHTML = `
    <h3>${escapeHtml(node.label)}</h3>
    <p class="meta">${escapeHtml(node.id)} - ${escapeHtml(node.kind)}</p>
    <div class="chips">${connected.slice(0, 8).map((edge) => `<span class="chip">${escapeHtml(edge.kind)}</span>`).join("")}</div>
    <pre>${escapeHtml(JSON.stringify(node.properties || {}, null, 2))}</pre>
  `;
}

function graphColor(kind) {
  const colors = {
    agent: "#22736a",
    workflow: "#386641",
    workflow_step: "#6a994e",
    skill: "#5a4fcf",
    mcp: "#168aad",
    data_source: "#b76f26",
    data_catalog: "#99582a",
    trigger_rule: "#d00000",
    review_item: "#9d4edd",
    field_task: "#0077b6",
    learning_record: "#c9184a",
    memory_tag: "#ffb703",
    data_recipe: "#bc6c25",
    evidence: "#0a9396",
    claim: "#ae2012",
    run: "#495057",
    artifact: "#6c757d",
  };
  return colors[kind] || "#64706b";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

function truncate(value, length) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1)}...` : text;
}

async function runDry() {
  const output = qs("#run-output");
  output.textContent = "Running dry workflow...";
  const payload = {
    workflow: qs("#run-workflow").value,
    task: qs("#run-task").value,
  };
  const result = await api("/api/run", { method: "POST", body: JSON.stringify(payload) });
  output.textContent = JSON.stringify(result.result, null, 2);
  toast("Dry run complete and indexed.");
  await refresh();
}

function bind() {
  qsa(".nav-button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  qs("#refresh-button").addEventListener("click", () => refresh().catch((err) => toast(err.message, "error")));
  qs("#validate-button").addEventListener("click", () => validateProject().catch((err) => toast(err.message, "error")));
  qs("#data-refresh-button").addEventListener("click", () => refresh().catch((err) => toast(err.message, "error")));
  qs("#stage-data-button").addEventListener("click", (event) => {
    event.preventDefault();
    stageDataRecipe().catch((err) => toast(err.message, "error"));
  });
  qs("#evidence-refresh-button").addEventListener("click", () => refresh().catch((err) => toast(err.message, "error")));
  qs("#save-agent-button").addEventListener("click", (event) => {
    event.preventDefault();
    saveAgent().catch((err) => toast(err.message, "error"));
  });
  qs("#new-agent-button").addEventListener("click", () => qs("#new-agent-dialog").showModal());
  qs("#new-agent-form").addEventListener("submit", (event) => createAgent(event).catch((err) => toast(err.message, "error")));
  qs("#ingest-graph-button").addEventListener("click", () => ingestGraph().catch((err) => toast(err.message, "error")));
  qs("#graph-query-button").addEventListener("click", () => queryGraph().catch((err) => toast(err.message, "error")));
  qs("#graph-query-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") queryGraph().catch((err) => toast(err.message, "error"));
  });
  qs("#trigger-evaluate-button").addEventListener("click", (event) => {
    event.preventDefault();
    evaluateTrigger().catch((err) => toast(err.message, "error"));
  });
  qs("#review-refresh-button").addEventListener("click", () => refresh().catch((err) => toast(err.message, "error")));
  qs("#review-list").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement)) return;
    reviewAction(target.dataset.id, target.dataset.action).catch((err) => toast(err.message, "error"));
  });
  qs("#field-refresh-button").addEventListener("click", () => refresh().catch((err) => toast(err.message, "error")));
  qs("#field-list").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement)) return;
    fieldAction(target.dataset.id, target.dataset.action).catch((err) => toast(err.message, "error"));
  });
  qs("#memory-save-button").addEventListener("click", (event) => saveMemory(event).catch((err) => toast(err.message, "error")));
  qs("#memory-refresh-button").addEventListener("click", () => refresh().catch((err) => toast(err.message, "error")));
  qs("#memory-list").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement)) return;
    memoryAction(target.dataset.id, target.dataset.action).catch((err) => toast(err.message, "error"));
  });
  qs("#run-button").addEventListener("click", () => runDry().catch((err) => toast(err.message, "error")));
}

bind();
refresh().catch((err) => toast(err.message, "error"));
