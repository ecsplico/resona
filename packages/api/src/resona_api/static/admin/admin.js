import { registerIconLibrary } from "/admin/vendor/webawesome/webawesome.loader.js";

// Icons resolve to the hub's own SVGs: the Font Awesome CDN is never contacted, and the
// built-in `system` icons (data: URLs loaded with fetch, which `connect-src 'self'` blocks)
// are swapped for local equivalents.
const iconUrl = (name) => "/admin/icons/" + name + ".svg";
const SYSTEM_ICONS = {
  check: "check",
  "chevron-down": "chevron-down",
  "chevron-left": "chevron-left",
  "chevron-right": "chevron-right",
  "circle-xmark": "circle-x",
  copy: "copy",
  eye: "eye",
  "eye-slash": "eye-off",
  xmark: "x",
};
registerIconLibrary("default", {
  resolver: (name) => (/^[a-z0-9-]+$/.test(name) ? iconUrl(name) : ""),
});
registerIconLibrary("system", {
  resolver: (name) => iconUrl(SYSTEM_ICONS[name] || "help-circle"),
});

const TOKEN_KEY = "resona-admin-token";
const ADMIN_API = "/v1/admin";
const DAY_MS = 24 * 60 * 60 * 1000;
const ROLE_LABELS = {
  org_admin: "Organization admin",
  hub_operator: "Hub operator",
  client: "Client",
  worker: "Worker",
};
const QUEUE_REASONS = {
  no_capable_worker: "No capable worker online",
  release_not_approved: "Release not approved",
  waiting_capacity: "Waiting for capacity",
};
const TABS = ["workers", "credentials", "jobs", "settings"];

const state = {
  token: null,
  me: null,
  org: null,
  tab: "workers",
  jobsCursor: null,
  // Jobs currently listed, by id; feeds the pending count in the summary strip.
  jobs: new Map(),
  // Bumped whenever the view context changes; responses for an older context are dropped.
  seq: 0,
  // Bumped whenever the selected organization changes; guards the summary strip.
  orgSeq: 0,
  // Latest load sequence per tab; the spinner stops only for the newest load.
  loading: {},
};

const $ = (id) => document.getElementById(id);

// ── Token storage ──────────────────────────────────────────────────────────

function loadToken() {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch (_) {
    return null;
  }
}

function saveToken(token) {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token);
  } catch (_) {
    // Storage unavailable: the token lives only in memory for this page.
  }
}

function clearToken() {
  try {
    window.sessionStorage.removeItem(TOKEN_KEY);
  } catch (_) {
    // Nothing stored.
  }
}

// ── API ────────────────────────────────────────────────────────────────────

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
    this.handled = false;
  }
}

function toApiError(status, statusText, data) {
  const body = data && typeof data === "object" ? data : {};
  if (body.message) {
    return new ApiError(status, body.error ? String(body.error) : "error", String(body.message));
  }
  const detail = body.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.message) {
    return new ApiError(status, detail.error ? String(detail.error) : "error", String(detail.message));
  }
  if (Array.isArray(detail)) {
    const message = detail
      .map((item) => {
        const loc = item && Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
        const msg = item && item.msg !== undefined ? String(item.msg) : "invalid value";
        return loc ? loc + ": " + msg : msg;
      })
      .join("; ");
    return new ApiError(status, "validation_error", message || "Invalid request.");
  }
  if (typeof detail === "string" && detail) {
    return new ApiError(status, "error", detail);
  }
  const reason = statusText ? "HTTP " + status + " " + statusText : "HTTP " + status;
  return new ApiError(status, body.error ? String(body.error) : "error", "Request failed (" + reason + ").");
}

async function api(method, path, body) {
  const headers = {
    Authorization: "Bearer " + state.token,
    Accept: "application/json",
  };
  const init = { method: method, headers: headers, cache: "no-store", credentials: "omit" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (_) {
    throw new ApiError(0, "network_error", "Could not reach the hub.");
  }
  const text = response.status === 204 ? "" : await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_) {
      data = null;
    }
  }
  if (!response.ok) {
    const error = toApiError(response.status, response.statusText, data);
    if (response.status === 401) {
      signOut("Token invalid or revoked");
      error.handled = true;
    }
    throw error;
  }
  return data;
}

function orgPath() {
  return ADMIN_API + "/organizations/" + encodeURIComponent(state.org);
}

// ── Error banner ───────────────────────────────────────────────────────────

function showError(message) {
  // Before sign-in completes the error belongs on the sign-in slip.
  if ($("app").hidden) {
    const note = $("login-message");
    note.textContent = message;
    note.hidden = false;
    return;
  }
  $("banner-text").textContent = message;
  $("banner").hidden = false;
}

function hideBanner() {
  $("banner").hidden = true;
  $("banner-text").textContent = "";
}

function report(error) {
  if (error && error.handled) {
    return;
  }
  showError(error && error.message ? error.message : String(error));
}

// ── DOM helpers ────────────────────────────────────────────────────────────

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  return node;
}

// Creates a (Web Awesome) element from attributes; `true` sets a boolean attribute.
function wa(tag, attrs, text) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === true) {
      node.setAttribute(name, "");
    } else if (value !== false && value !== undefined && value !== null) {
      node.setAttribute(name, String(value));
    }
  });
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  return node;
}

function isBlank(value) {
  return value === undefined || value === null || value === "";
}

function dash() {
  return el("span", "quiet", "—");
}

// A table cell labelled for the stacked phone layout.
function cell(label, content, className) {
  const td = el("td", className);
  td.dataset.label = label;
  if (content instanceof Node) {
    td.appendChild(content);
  } else if (isBlank(content)) {
    td.appendChild(dash());
  } else {
    td.textContent = String(content);
  }
  return td;
}

function stack(...nodes) {
  const wrap = el("div", "stack");
  nodes.filter(Boolean).forEach((node) => wrap.appendChild(node));
  return wrap;
}

// Machine identifiers read as grouped tracking numbers (`wrk · f21e · 64ec …`) while
// the element's text stays the raw id, so selecting and copying yields it unchanged.
function trackingNumber(raw) {
  const text = String(raw);
  const node = el("span", "trk");
  const match = /^([a-z]+)_([0-9a-f]{8,})$/i.exec(text);
  if (!match) {
    node.classList.add("trk-plain");
    node.textContent = text;
    return node;
  }
  node.appendChild(el("span", "trk-prefix", match[1]));
  node.appendChild(el("span", "trk-joint", "_"));
  for (let index = 0; index < match[2].length; index += 4) {
    // <wbr> adds a line-break opportunity between groups without adding text.
    node.appendChild(document.createElement("wbr"));
    node.appendChild(el("span", "trk-group", match[2].slice(index, index + 4)));
  }
  return node;
}

function idWithCopy(raw, copyLabel) {
  if (isBlank(raw)) {
    return dash();
  }
  const wrap = el("span", "id-copy");
  wrap.appendChild(trackingNumber(raw));
  wrap.appendChild(
    wa("wa-copy-button", {
      value: String(raw),
      "copy-label": copyLabel,
      "success-label": "Copied",
      "error-label": "Copy failed",
    })
  );
  return wrap;
}

function mono(text) {
  return isBlank(text) ? dash() : el("span", "machine", text);
}

function numeral(value) {
  return isBlank(value) ? dash() : el("span", "num", value);
}

function absoluteTime(iso) {
  return wa("wa-format-date", {
    date: iso,
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function relativeTime(iso) {
  return wa("wa-relative-time", { date: iso, sync: true });
}

// Absolute timestamp with its distance from now underneath.
function timeNode(iso) {
  if (!iso) {
    return dash();
  }
  const wrap = el("span", "when");
  wrap.title = iso;
  wrap.appendChild(absoluteTime(iso));
  const rel = el("span", "when-rel");
  rel.appendChild(relativeTime(iso));
  wrap.appendChild(rel);
  return wrap;
}

function badge(text, variant, appearance) {
  return wa("wa-badge", { variant: variant, appearance: appearance || "filled-outlined" }, text);
}

// Red double-ruled rubber stamp: revoked tokens and workers, cancelled jobs.
function stamp(text) {
  return el("span", "stamp", text);
}

function actionButton(label, options) {
  const node = wa("wa-button", {
    size: "s",
    variant: options.variant || "neutral",
    appearance: options.appearance || "outlined",
    id: options.id,
  });
  if (options.icon) {
    node.appendChild(wa("wa-icon", { slot: "start", name: options.icon }));
  }
  node.appendChild(document.createTextNode(label));
  node.addEventListener("click", options.onClick);
  return node;
}

function tooltipFor(target, text) {
  return wa("wa-tooltip", { for: target.id }, text);
}

function formatCandidate(candidate) {
  if (!candidate || typeof candidate !== "object") {
    return candidate === undefined || candidate === null ? "" : String(candidate);
  }
  let text = String(candidate.engine) + " / " + String(candidate.model);
  if (candidate.model_revision) {
    text += "@" + String(candidate.model_revision);
  }
  if (candidate.accelerator) {
    text += " · " + String(candidate.accelerator);
  }
  if (candidate.ocr_engine) {
    text += " · ocr " + String(candidate.ocr_engine);
  }
  return text;
}

function capabilityGroup(label, candidates, kind) {
  const wrap = el("div", "cap-group cap-" + kind);
  wrap.appendChild(el("span", "caps", label + " (" + candidates.length + ")"));
  if (candidates.length) {
    const tags = el("div", "cap-tags");
    candidates.forEach((candidate) => {
      const tag = wa(
        "wa-tag",
        {
          size: "s",
          variant: kind === "approved" ? "success" : "neutral",
          appearance: kind === "approved" ? "filled-outlined" : "outlined",
        },
        formatCandidate(candidate)
      );
      tag.classList.add("cap-tag");
      tags.appendChild(tag);
    });
    wrap.appendChild(tags);
  }
  return wrap;
}

// Shows or hides a table and its empty note together.
function setEmpty(name) {
  const empty = $(name + "-body").children.length === 0;
  $(name + "-wrap").hidden = empty;
  $(name + "-empty").hidden = !empty;
}

function skeletonRows(body) {
  const columns = Number(body.dataset.columns) || 1;
  for (let index = 0; index < 3; index += 1) {
    const row = el("tr", "skeleton-row");
    const td = el("td");
    td.colSpan = columns;
    td.appendChild(wa("wa-skeleton", { effect: "sheen" }));
    row.appendChild(td);
    body.appendChild(row);
  }
}

function setLoading(tab, seq, on) {
  if (!on && state.loading[tab] !== seq) {
    return;
  }
  state.loading[tab] = on ? seq : null;
  const panel = $("panel-" + tab);
  panel.querySelector("[data-spinner]").hidden = !on;
  panel.querySelectorAll("tbody").forEach((body) => {
    body.setAttribute("aria-busy", on ? "true" : "false");
    if (on && body.children.length === 0) {
      body.closest(".table-wrap").hidden = false;
      skeletonRows(body);
    }
    if (!on) {
      body.querySelectorAll(".skeleton-row").forEach((row) => row.remove());
    }
  });
}

// Runs an action triggered by a button: marks it busy while pending and reports errors.
async function run(trigger, action) {
  if (trigger) {
    trigger.disabled = true;
    if (trigger.localName === "wa-button") {
      trigger.loading = true;
    }
  }
  hideBanner();
  try {
    await action();
  } catch (error) {
    report(error);
  } finally {
    if (trigger && trigger.isConnected) {
      trigger.disabled = false;
      if (trigger.localName === "wa-button") {
        trigger.loading = false;
      }
    }
  }
}

// Runs `callback` once the component is defined and has rendered its shadow DOM, so
// methods like focus() find their inner control.
function whenReady(node, callback) {
  customElements
    .whenDefined(node.localName)
    .then(() => node.updateComplete)
    .then(() => callback(node));
}

// ── Confirmation dialog ────────────────────────────────────────────────────

let settleConfirmation = null;

function confirmAction(options) {
  const dialog = $("confirm");
  if (settleConfirmation) {
    settleConfirmation(false);
  }
  dialog.label = options.title;
  $("confirm-text").textContent = options.message;
  $("confirm-ok").textContent = options.confirmLabel;
  $("confirm-cancel").textContent = options.dismissLabel || "Go back";
  dialog.open = true;
  return new Promise((resolve) => {
    settleConfirmation = (value) => {
      settleConfirmation = null;
      resolve(value);
    };
  });
}

function closeConfirmation(value) {
  if (settleConfirmation) {
    settleConfirmation(value);
  }
  $("confirm").open = false;
}

// ── Summary strip ──────────────────────────────────────────────────────────

function setTally(id, text) {
  $(id).textContent = text;
}

function resetTally() {
  ["tally-online", "tally-workers", "tally-tokens", "tally-pending"].forEach((id) => setTally(id, "–"));
}

function tallyWorkers(items) {
  const active = items.filter((worker) => !worker.revoked);
  setTally("tally-online", String(active.filter((worker) => worker.online).length));
  setTally("tally-workers", String(active.length));
}

function tallyCredentials(items) {
  setTally("tally-tokens", String(items.filter((credential) => !credential.revoked_at).length));
}

function tallyJobs() {
  let pending = 0;
  state.jobs.forEach((job) => {
    if (job.status === "pending") {
      pending += 1;
    }
  });
  // With more pages unread the count is a lower bound.
  setTally("tally-pending", String(pending) + (state.jobsCursor ? "+" : ""));
}

async function loadSummary() {
  const orgSeq = state.orgSeq;
  const [workers, credentials, jobs] = await Promise.allSettled([
    api("GET", orgPath() + "/workers"),
    api("GET", orgPath() + "/credentials"),
    api("GET", orgPath() + "/jobs"),
  ]);
  if (orgSeq !== state.orgSeq) {
    return;
  }
  if (workers.status === "fulfilled") {
    tallyWorkers((workers.value && workers.value.items) || []);
  }
  if (credentials.status === "fulfilled") {
    tallyCredentials((credentials.value && credentials.value.items) || []);
  }
  // The jobs tab owns the pending count once it has loaded a page.
  if (jobs.status === "fulfilled" && state.jobs.size === 0) {
    const items = (jobs.value && jobs.value.items) || [];
    const pending = items.filter((job) => job.status === "pending").length;
    setTally("tally-pending", String(pending) + (jobs.value && jobs.value.next_cursor ? "+" : ""));
  }
}

// ── Session ────────────────────────────────────────────────────────────────

function resetViews() {
  hideIssued();
  ["workers", "credentials", "jobs", "baselines", "releases"].forEach((name) => {
    $(name + "-body").replaceChildren();
    $(name + "-empty").hidden = true;
    $(name + "-wrap").hidden = false;
  });
  $("settings-revision").textContent = "";
  $("settings-json").textContent = "";
  $("jobs-more").hidden = true;
  state.jobsCursor = null;
  state.jobs.clear();
  resetTally();
}

function setMenu(open) {
  $("rail").classList.toggle("is-open", open);
  $("menu-toggle").setAttribute("aria-expanded", open ? "true" : "false");
}

function showLogin(message) {
  $("app").hidden = true;
  $("login").hidden = false;
  const note = $("login-message");
  note.textContent = message || "";
  note.hidden = !message;
  whenReady($("login-token"), (input) => input.focus());
}

function signOut(message) {
  state.seq += 1;
  state.orgSeq += 1;
  state.token = null;
  state.me = null;
  state.org = null;
  clearToken();
  closeConfirmation(false);
  resetViews();
  $("org-list").replaceChildren();
  $("org-select").replaceChildren();
  $("org-title").textContent = "";
  $("new-org-form").hidden = true;
  setMenu(false);
  hideBanner();
  showLogin(message);
}

async function enter() {
  let me;
  try {
    me = await api("GET", ADMIN_API + "/me");
  } catch (error) {
    if (error.handled) {
      return;
    }
    if (error.status === 403) {
      signOut("This token is not an organization admin or hub operator token.");
      return;
    }
    showLogin();
    report(error);
    return;
  }
  state.me = me;
  $("me-role").textContent = ROLE_LABELS[me.role] || me.role;
  $("me-org").textContent = me.organization_id;
  $("login").hidden = true;
  $("login-message").hidden = true;
  $("app").hidden = false;
  $("new-org-form").hidden = me.role !== "hub_operator";
  try {
    await loadOrganizations(null);
  } catch (error) {
    report(error);
  }
}

// ── Organizations ──────────────────────────────────────────────────────────

async function loadOrganizations(preferred) {
  const data = await api("GET", ADMIN_API + "/organizations");
  const items = (data && data.items) || [];
  const ids = items.map((item) => item.id);
  const home = state.me.organization_id;

  const list = $("org-list");
  const select = $("org-select");
  list.replaceChildren();
  select.replaceChildren();
  items.forEach((item) => {
    const entry = el("li");
    const choose = el("button", "org-item");
    choose.type = "button";
    choose.dataset.org = item.id;
    choose.appendChild(el("span", "org-name", item.id));
    if (item.id === home) {
      choose.appendChild(el("span", "org-home caps", "Home"));
    }
    choose.addEventListener("click", () => chooseOrganization(item.id));
    entry.appendChild(choose);
    list.appendChild(entry);

    const option = wa("wa-option", { value: item.id }, item.id === home ? item.id + " (yours)" : item.id);
    select.appendChild(option);
  });

  let chosen = null;
  for (const candidate of [preferred, state.org, home]) {
    if (candidate && ids.includes(candidate)) {
      chosen = candidate;
      break;
    }
  }
  if (!chosen && ids.length) {
    chosen = ids[0];
  }
  select.disabled = ids.length <= 1;
  selectOrganization(chosen);
}

function chooseOrganization(orgId) {
  setMenu(false);
  if (orgId === state.org) {
    loadTab();
    return;
  }
  selectOrganization(orgId);
}

function selectOrganization(orgId) {
  state.org = orgId;
  state.orgSeq += 1;
  resetViews();
  $("org-title").textContent = orgId || "No organization";
  $("org-select").value = orgId || "";
  document.querySelectorAll(".org-item").forEach((item) => {
    if (item.dataset.org === orgId) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  if (orgId) {
    loadSummary().catch(report);
  }
  loadTab();
}

// ── Tabs ───────────────────────────────────────────────────────────────────

const loaders = {
  workers: loadWorkers,
  credentials: loadCredentials,
  jobs: (seq) => loadJobs(seq, false),
  settings: loadSettings,
};

function setTab(name) {
  if (!TABS.includes(name)) {
    return;
  }
  state.tab = name;
  loadTab();
}

function loadTab() {
  state.seq += 1;
  const seq = state.seq;
  if (!state.token || !state.org) {
    return;
  }
  hideBanner();
  const tab = state.tab;
  setLoading(tab, seq, true);
  loaders[tab](seq)
    .catch(report)
    .finally(() => setLoading(tab, seq, false));
}

// ── Workers ────────────────────────────────────────────────────────────────

async function loadWorkers(seq) {
  const data = await api("GET", orgPath() + "/workers");
  if (seq !== state.seq) {
    return;
  }
  const items = (data && data.items) || [];
  const body = $("workers-body");
  body.replaceChildren();
  items.forEach((worker) => body.appendChild(workerRow(worker)));
  tallyWorkers(items);
  setEmpty("workers");
}

function workerStatus(worker) {
  let mark;
  if (worker.revoked) {
    mark = stamp("Revoked");
  } else if (worker.online) {
    mark = badge("Online", "success");
  } else {
    mark = badge("Offline", "neutral", "outlined");
  }
  const seen = el("span", "when-rel");
  if (worker.last_seen) {
    seen.appendChild(document.createTextNode("Last seen "));
    seen.appendChild(relativeTime(worker.last_seen));
    seen.title = worker.last_seen;
  } else {
    seen.textContent = "Never seen";
  }
  return stack(mark, seen);
}

function workerApproval(worker) {
  if (!worker.approved) {
    return badge("Not approved", "warning");
  }
  return badge(worker.approved_private ? "Approved private" : "Approved non-private", "success");
}

function workerRow(worker) {
  const row = el("tr", worker.revoked ? "is-revoked" : null);
  row.appendChild(cell("Name", worker.name ? el("span", "name", worker.name) : ""));
  row.appendChild(cell("ID", idWithCopy(worker.id, "Copy worker ID")));
  row.appendChild(cell("Status", workerStatus(worker)));
  row.appendChild(cell("Runtime", mono(worker.runtime_version)));
  row.appendChild(cell("Approval", workerApproval(worker)));
  row.appendChild(
    cell(
      "Capabilities",
      stack(
        capabilityGroup("Reported", worker.reported_capabilities || [], "reported"),
        capabilityGroup("Approved", worker.approved_capabilities || [], "approved")
      )
    )
  );

  const actions = el("div", "actions");
  if (!worker.revoked) {
    const privateButton = actionButton("Approve private", {
      variant: "brand",
      appearance: "accent",
      icon: "check",
      id: "approve-private-" + worker.id,
      onClick: (event) => approveWorker(event.currentTarget, worker, true),
    });
    const publicButton = actionButton("Approve non-private", {
      variant: "brand",
      appearance: "outlined",
      icon: "check",
      id: "approve-public-" + worker.id,
      onClick: (event) => approveWorker(event.currentTarget, worker, false),
    });
    actions.appendChild(privateButton);
    actions.appendChild(tooltipFor(privateButton, "Accepts its reported capabilities; it then runs private jobs only."));
    actions.appendChild(publicButton);
    actions.appendChild(tooltipFor(publicButton, "Accepts its reported capabilities; it then runs non-private jobs only."));
    actions.appendChild(
      actionButton("Revoke", {
        variant: "danger",
        icon: "ban",
        onClick: (event) => revokeWorker(event.currentTarget, worker),
      })
    );
  }
  row.appendChild(cell("Actions", actions.children.length ? actions : "", "actions-cell"));
  return row;
}

function workerPath(worker) {
  return orgPath() + "/workers/" + encodeURIComponent(worker.id);
}

function approveWorker(trigger, worker, isPrivate) {
  return run(trigger, async () => {
    await api("POST", workerPath(worker) + "/approve", { private: isPrivate });
    loadTab();
  });
}

async function revokeWorker(trigger, worker) {
  const label = worker.name ? '"' + worker.name + '"' : worker.id;
  const confirmed = await confirmAction({
    title: "Revoke worker?",
    message: "Revoke worker " + label + "? Its token stops working immediately.",
    confirmLabel: "Revoke worker",
    dismissLabel: "Keep worker",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("POST", workerPath(worker) + "/revoke");
    loadTab();
  });
}

// ── Credentials ────────────────────────────────────────────────────────────

async function loadCredentials(seq) {
  const data = await api("GET", orgPath() + "/credentials");
  if (seq !== state.seq) {
    return;
  }
  const items = (data && data.items) || [];
  const body = $("credentials-body");
  body.replaceChildren();
  items.forEach((credential) => body.appendChild(credentialRow(credential)));
  tallyCredentials(items);
  setEmpty("credentials");
}

function credentialRow(credential) {
  const own = Boolean(state.me && credential.id === state.me.credential_id);
  const row = el("tr", credential.revoked_at ? "is-revoked" : null);
  row.appendChild(cell("Credential", idWithCopy(credential.id, "Copy credential ID")));
  row.appendChild(cell("Role", ROLE_LABELS[credential.role] || credential.role));
  row.appendChild(cell("Worker", credential.worker_id ? trackingNumber(credential.worker_id) : ""));
  row.appendChild(cell("Issued", timeNode(credential.created_at)));

  let standing;
  if (credential.revoked_at) {
    standing = stack(stamp("Revoked"), timeNode(credential.revoked_at));
  } else {
    standing = stack(badge("Live", "success"), own ? el("span", "when-rel", "This session") : null);
  }
  row.appendChild(cell("State", standing));

  const actions = el("div", "actions");
  if (!credential.revoked_at) {
    if (credential.role === "worker") {
      actions.appendChild(el("span", "quiet", "Revoke the worker instead"));
    } else {
      const revoke = actionButton("Revoke", {
        variant: "danger",
        icon: "ban",
        onClick: (event) => revokeCredential(event.currentTarget, credential),
      });
      if (own) {
        revoke.disabled = true;
        revoke.title = "You are signed in with this credential.";
        actions.appendChild(revoke);
        actions.appendChild(el("span", "quiet", "You are signed in with this credential."));
      } else {
        actions.appendChild(revoke);
      }
    }
  }
  row.appendChild(cell("Actions", actions.children.length ? actions : "", "actions-cell"));
  return row;
}

async function revokeCredential(trigger, credential) {
  const confirmed = await confirmAction({
    title: "Revoke token?",
    message: "Revoke credential " + credential.id + "? Anything using it stops working immediately.",
    confirmLabel: "Revoke token",
    dismissLabel: "Keep token",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("POST", orgPath() + "/credentials/" + encodeURIComponent(credential.id) + "/revoke");
    loadTab();
  });
}

function issueCredential(trigger, role) {
  return run(trigger, async () => {
    const org = state.org;
    const issued = await api("POST", orgPath() + "/credentials", { role: role });
    if (org !== state.org || !state.token) {
      return;
    }
    showIssued(issued);
    if (state.tab === "credentials") {
      loadTab();
    }
  });
}

function showIssued(issued) {
  const slip = $("issued");
  slip.classList.remove("is-tearing");
  $("issued-id").replaceChildren(trackingNumber(issued.id));
  $("issued-role").textContent = ROLE_LABELS[issued.role] || issued.role;
  $("issued-token").value = issued.token;
  $("issued-copy").value = issued.token;
  $("issued-copied").hidden = true;
  slip.hidden = false;
  whenReady($("issued-token"), (input) => {
    input.focus();
    input.select();
  });
}

function hideIssued() {
  const slip = $("issued");
  slip.hidden = true;
  slip.classList.remove("is-tearing");
  $("issued-token").value = "";
  $("issued-copy").value = "";
  $("issued-role").textContent = "";
  $("issued-id").replaceChildren();
  $("issued-copied").hidden = true;
}

// The slip slides away before it is cleared; without motion it is cleared at once.
function tearOffIssued() {
  const slip = $("issued");
  if (slip.hidden || slip.classList.contains("is-tearing")) {
    return;
  }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    hideIssued();
    $("issue-client").focus();
    return;
  }
  slip.classList.add("is-tearing");
  let done = false;
  const finish = () => {
    if (done) {
      return;
    }
    done = true;
    if (slip.classList.contains("is-tearing")) {
      hideIssued();
      $("issue-client").focus();
    }
  };
  const onEnd = (event) => {
    if (event.target === slip) {
      slip.removeEventListener("animationend", onEnd);
      finish();
    }
  };
  slip.addEventListener("animationend", onEnd);
  window.setTimeout(finish, 600);
}

// ── Jobs ───────────────────────────────────────────────────────────────────

function canManageJobs() {
  return Boolean(state.me && state.me.role === "org_admin" && state.org === state.me.organization_id);
}

async function loadJobs(seq, append) {
  let path = orgPath() + "/jobs";
  if (append && state.jobsCursor) {
    path += "?cursor=" + encodeURIComponent(state.jobsCursor);
  }
  const data = await api("GET", path);
  if (seq !== state.seq) {
    return;
  }
  const body = $("jobs-body");
  if (!append) {
    body.replaceChildren();
    state.jobs.clear();
  }
  $("jobs-actions-head").hidden = !canManageJobs();
  ((data && data.items) || []).forEach((job) => {
    state.jobs.set(job.job_id, job);
    body.appendChild(jobRow(job));
  });
  state.jobsCursor = (data && data.next_cursor) || null;
  $("jobs-more").hidden = !state.jobsCursor;
  tallyJobs();
  setEmpty("jobs");
}

function jobStatus(job) {
  switch (job.status) {
    case "pending":
      return badge("Pending", "warning");
    case "processing":
      return badge("Processing", "warning", "outlined");
    case "completed":
      return badge("Completed", "success");
    case "failed":
      return badge("Failed", "neutral", "outlined");
    case "cancelled":
      return stamp("Cancelled");
    default:
      return badge(String(job.status), "neutral", "outlined");
  }
}

// Thin bar: time since creation, full at 24 hours.
function waitBar(job) {
  const created = Date.parse(job.created_at);
  if (Number.isNaN(created)) {
    return null;
  }
  const share = Math.min(Math.max((Date.now() - created) / DAY_MS, 0), 1);
  const wrap = el("div", "wait");
  const track = el("span", "wait-track");
  track.setAttribute("aria-hidden", "true");
  track.style.setProperty("--wait", String(Math.max(share, 0.02)));
  track.appendChild(el("span", "wait-fill"));
  const label = el("span", "wait-label");
  label.title = "Bar is full at 24 hours.";
  label.appendChild(document.createTextNode("Created "));
  label.appendChild(relativeTime(job.created_at));
  wrap.appendChild(track);
  wrap.appendChild(label);
  return wrap;
}

function jobTimeline(job) {
  const list = el("dl", "timeline");
  [
    ["Created", job.created_at],
    ["Updated", job.updated_at],
    ["Deadline", job.deadline_at],
  ].forEach(([label, iso]) => {
    const item = el("div");
    item.appendChild(el("dt", "caps", label));
    const value = el("dd");
    value.appendChild(iso ? absoluteTime(iso) : dash());
    if (iso) {
      value.title = iso;
    }
    item.appendChild(value);
    list.appendChild(item);
  });
  return list;
}

function jobRow(job) {
  const waiting = job.status === "pending" || job.status === "processing";
  const row = el("tr", job.status === "cancelled" ? "is-revoked" : null);
  row.appendChild(cell("Job", idWithCopy(job.job_id, "Copy job ID")));
  row.appendChild(
    cell(
      "Kind & profile",
      stack(
        el("span", "nowrap", job.kind),
        el("span", "when-rel", job.private ? "Private" : "Non-private"),
        mono(job.execution_profile)
      )
    )
  );

  const reason = job.queue_reason ? el("span", "reason", QUEUE_REASONS[job.queue_reason] || job.queue_reason) : null;
  if (reason) {
    reason.title = job.queue_reason;
  }
  const attempt = isBlank(job.attempt) ? null : el("span", "when-rel", "Attempt ");
  if (attempt) {
    attempt.appendChild(numeral(job.attempt));
  }
  row.appendChild(cell("Status", stack(jobStatus(job), reason, attempt, waiting ? waitBar(job) : null)));

  let candidate = "";
  if (job.candidate) {
    candidate = mono(formatCandidate(job.candidate));
    candidate.classList.add("machine-wrap");
  }
  row.appendChild(cell("Candidate", candidate));
  row.appendChild(
    cell(
      "Error",
      job.error ? stack(mono(String(job.error.code)), el("span", "error-text", String(job.error.message))) : ""
    )
  );
  row.appendChild(cell("Result", job.result_available ? badge("Available", "success", "outlined") : ""));
  row.appendChild(cell("Timeline", jobTimeline(job)));

  if (canManageJobs()) {
    const actions = el("div", "actions");
    if (waiting) {
      actions.appendChild(
        actionButton("Cancel", {
          variant: "danger",
          icon: "x",
          onClick: (event) => cancelJob(event.currentTarget, row, job),
        })
      );
    }
    actions.appendChild(
      actionButton("Delete", {
        variant: "danger",
        appearance: "accent",
        icon: "trash",
        onClick: (event) => deleteJob(event.currentTarget, row, job),
      })
    );
    row.appendChild(cell("Actions", actions, "actions-cell"));
  }
  return row;
}

function jobPath(job) {
  return "/v1/jobs/" + encodeURIComponent(job.job_id);
}

async function cancelJob(trigger, row, job) {
  const confirmed = await confirmAction({
    title: "Cancel job?",
    message: "Cancel job " + job.job_id + "? This cannot be undone.",
    confirmLabel: "Cancel job",
    dismissLabel: "Keep job",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    const updated = await api("POST", jobPath(job) + "/cancel");
    if (row.isConnected && updated && updated.job_id !== undefined) {
      state.jobs.set(updated.job_id, updated);
      row.replaceWith(jobRow(updated));
      tallyJobs();
    } else {
      loadTab();
    }
  });
}

async function deleteJob(trigger, row, job) {
  const confirmed = await confirmAction({
    title: "Delete job?",
    message: "Delete job " + job.job_id + "? Its upload and result are removed.",
    confirmLabel: "Delete job",
    dismissLabel: "Keep job",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("DELETE", jobPath(job));
    row.remove();
    state.jobs.delete(job.job_id);
    tallyJobs();
    setEmpty("jobs");
  });
}

function loadMoreJobs(trigger) {
  const seq = state.seq;
  return run(trigger, () => loadJobs(seq, true));
}

// ── Settings & baselines ───────────────────────────────────────────────────

async function loadSettings(seq) {
  const [settings, baselines] = await Promise.all([
    api("GET", orgPath() + "/settings"),
    api("GET", orgPath() + "/baselines"),
  ]);
  if (seq !== state.seq) {
    return;
  }
  $("settings-revision").textContent = String(settings.revision);
  $("settings-json").textContent = JSON.stringify(settings.settings, null, 2);

  const baselineBody = $("baselines-body");
  baselineBody.replaceChildren();
  ((baselines && baselines.items) || []).forEach((baseline) => {
    const row = el("tr");
    row.appendChild(cell("Execution profile", mono(baseline.execution_profile)));
    row.appendChild(cell("Platform", mono(baseline.platform)));
    row.appendChild(cell("Revision", numeral(baseline.revision)));
    row.appendChild(cell("Evaluation", baseline.evaluation_id ? trackingNumber(baseline.evaluation_id) : ""));
    row.appendChild(cell("Release", baseline.release_id ? trackingNumber(baseline.release_id) : ""));
    let sha = "";
    if (baseline.corpus_sha256) {
      sha = el("span", "id-copy");
      const short = mono(String(baseline.corpus_sha256).slice(0, 12));
      short.title = baseline.corpus_sha256;
      sha.appendChild(short);
      sha.appendChild(
        wa("wa-copy-button", {
          value: String(baseline.corpus_sha256),
          "copy-label": "Copy full SHA-256",
          "success-label": "Copied",
          "error-label": "Copy failed",
        })
      );
    }
    row.appendChild(cell("Corpus SHA-256", sha));
    row.appendChild(cell("Created", timeNode(baseline.created_at)));
    baselineBody.appendChild(row);
  });
  setEmpty("baselines");

  const releaseBody = $("releases-body");
  releaseBody.replaceChildren();
  ((baselines && baselines.approved_releases) || []).forEach((release) => {
    const row = el("tr");
    row.appendChild(cell("Execution profile", mono(release.execution_profile)));
    row.appendChild(cell("Platform", mono(release.platform)));
    row.appendChild(cell("Release", release.release_id ? trackingNumber(release.release_id) : ""));
    row.appendChild(cell("Evaluation", release.evaluation_id ? trackingNumber(release.evaluation_id) : ""));
    row.appendChild(cell("Approved", timeNode(release.approved_at)));
    releaseBody.appendChild(row);
  });
  setEmpty("releases");
}

// ── Wiring ─────────────────────────────────────────────────────────────────

function init() {
  $("banner-close").addEventListener("click", hideBanner);

  $("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("login-token");
    const token = String(input.value || "").trim();
    input.value = "";
    if (!token) {
      return;
    }
    hideBanner();
    $("login-message").hidden = true;
    state.token = token;
    saveToken(token);
    enter();
  });

  $("sign-out").addEventListener("click", () => signOut(""));
  $("menu-toggle").addEventListener("click", () => setMenu(!$("rail").classList.contains("is-open")));

  $("org-select").addEventListener("change", (event) => {
    const value = event.target.value;
    if (value && value !== state.org) {
      chooseOrganization(value);
    }
  });

  $("new-org-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("new-org-id");
    const id = String(input.value || "").trim();
    if (!id) {
      return;
    }
    const submit = event.target.querySelector("[type=submit]");
    run(submit, async () => {
      const created = await api("POST", ADMIN_API + "/organizations", { id: id });
      input.value = "";
      await loadOrganizations(created && created.id ? created.id : id);
    });
  });

  $("tabs").addEventListener("wa-tab-show", (event) => {
    if (event.target === $("tabs") && event.detail && event.detail.name !== state.tab) {
      setTab(event.detail.name);
    }
  });

  document.querySelectorAll("[data-refresh]").forEach((node) => {
    node.addEventListener("click", () => loadTab());
  });

  $("issue-client").addEventListener("click", (event) => issueCredential(event.currentTarget, "client"));
  $("issue-admin").addEventListener("click", (event) => issueCredential(event.currentTarget, "org_admin"));
  $("issued-copy").addEventListener("wa-copy", () => {
    $("issued-copied").hidden = false;
  });
  $("issued-copy").addEventListener("wa-error", (event) => {
    // Icons inside the copy button raise their own (composed) wa-error; only the
    // copy button's own event means the copy failed.
    if (event.composedPath()[0] !== event.currentTarget) {
      return;
    }
    showError("Copy failed. Select the token and copy it manually.");
  });
  $("issued-close").addEventListener("click", tearOffIssued);
  $("jobs-more").addEventListener("click", (event) => loadMoreJobs(event.currentTarget));

  $("confirm-ok").addEventListener("click", () => closeConfirmation(true));
  $("confirm-cancel").addEventListener("click", () => closeConfirmation(false));
  $("confirm").addEventListener("wa-hide", (event) => {
    if (event.target === $("confirm") && settleConfirmation) {
      settleConfirmation(false);
    }
  });

  state.token = loadToken();
  if (state.token) {
    enter();
  } else {
    showLogin("");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
