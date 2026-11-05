"use strict";

(function () {
  const TOKEN_KEY = "resona-admin-token";
  const ADMIN_API = "/v1/admin";
  const ROLE_LABELS = {
    org_admin: "Organization admin",
    hub_operator: "Hub operator",
    client: "Client",
    worker: "Worker",
  };

  const state = {
    token: null,
    me: null,
    org: null,
    tab: "workers",
    jobsCursor: null,
    // Bumped whenever the view context changes; responses for an older context are dropped.
    seq: 0,
  };

  const $ = (id) => document.getElementById(id);

  // ── Token storage ────────────────────────────────────────────────────────

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

  // ── API ──────────────────────────────────────────────────────────────────

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

  // ── Banner ───────────────────────────────────────────────────────────────

  function showError(message) {
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

  // ── DOM helpers ──────────────────────────────────────────────────────────

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

  function cell(content, className) {
    const td = el("td", className);
    if (content instanceof Node) {
      td.appendChild(content);
    } else {
      td.textContent = content === undefined || content === null || content === "" ? "—" : String(content);
    }
    return td;
  }

  function mono(text) {
    return el("span", "mono", text === undefined || text === null || text === "" ? "—" : text);
  }

  function yesNo(value) {
    return value ? "yes" : "no";
  }

  function timeNode(iso) {
    if (!iso) {
      return el("span", "muted", "—");
    }
    const node = el("time");
    node.dateTime = iso;
    node.title = iso;
    const date = new Date(iso);
    node.textContent = Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
    return node;
  }

  function button(label, className, onClick) {
    const node = el("button", className, label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
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

  function candidateList(label, candidates) {
    const wrap = el("div");
    wrap.appendChild(el("div", "caps-label", label + " (" + candidates.length + ")"));
    if (candidates.length) {
      const list = el("ul", "caps");
      candidates.forEach((candidate) => list.appendChild(el("li", "mono", formatCandidate(candidate))));
      wrap.appendChild(list);
    }
    return wrap;
  }

  function setEmpty(bodyId, emptyId) {
    $(emptyId).hidden = $(bodyId).children.length > 0;
  }

  // Runs an action triggered by a button: disables it while pending and reports errors.
  async function run(trigger, action) {
    if (trigger) {
      trigger.disabled = true;
    }
    hideBanner();
    try {
      await action();
    } catch (error) {
      report(error);
    } finally {
      if (trigger && trigger.isConnected) {
        trigger.disabled = false;
      }
    }
  }

  // ── Session ──────────────────────────────────────────────────────────────

  function resetViews() {
    hideIssued();
    ["workers-body", "credentials-body", "jobs-body", "baselines-body", "releases-body"].forEach((id) =>
      $(id).replaceChildren()
    );
    ["workers-empty", "credentials-empty", "jobs-empty", "baselines-empty", "releases-empty"].forEach((id) => {
      $(id).hidden = true;
    });
    $("settings-revision").textContent = "";
    $("settings-json").textContent = "";
    $("jobs-more").hidden = true;
    state.jobsCursor = null;
  }

  function showLogin(message) {
    $("app").hidden = true;
    $("session-info").hidden = true;
    $("login").hidden = false;
    const note = $("login-message");
    note.textContent = message || "";
    note.hidden = !message;
    $("login-token").focus();
  }

  function signOut(message) {
    state.seq += 1;
    state.token = null;
    state.me = null;
    state.org = null;
    clearToken();
    resetViews();
    $("org-select").replaceChildren();
    $("new-org-form").hidden = true;
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
    $("session-info").hidden = false;
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

  // ── Organizations ────────────────────────────────────────────────────────

  async function loadOrganizations(preferred) {
    const data = await api("GET", ADMIN_API + "/organizations");
    const items = (data && data.items) || [];
    const ids = items.map((item) => item.id);
    const select = $("org-select");
    select.replaceChildren();
    items.forEach((item) => {
      const option = el("option", null, item.id === state.me.organization_id ? item.id + " (yours)" : item.id);
      option.value = item.id;
      select.appendChild(option);
    });
    let chosen = null;
    for (const candidate of [preferred, state.org, state.me.organization_id]) {
      if (candidate && ids.includes(candidate)) {
        chosen = candidate;
        break;
      }
    }
    if (!chosen && ids.length) {
      chosen = ids[0];
    }
    select.disabled = ids.length <= 1;
    if (chosen) {
      select.value = chosen;
    }
    selectOrganization(chosen);
  }

  function selectOrganization(orgId) {
    state.org = orgId;
    resetViews();
    loadTab();
  }

  // ── Tabs ─────────────────────────────────────────────────────────────────

  const loaders = {
    workers: loadWorkers,
    credentials: loadCredentials,
    jobs: (seq) => loadJobs(seq, false),
    settings: loadSettings,
  };

  function setTab(name) {
    state.tab = name;
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.setAttribute("aria-selected", tab.dataset.tab === name ? "true" : "false");
    });
    Object.keys(loaders).forEach((key) => {
      $("panel-" + key).hidden = key !== name;
    });
    loadTab();
  }

  function loadTab() {
    state.seq += 1;
    const seq = state.seq;
    if (!state.token || !state.org) {
      return;
    }
    hideBanner();
    loaders[state.tab](seq).catch(report);
  }

  // ── Workers ──────────────────────────────────────────────────────────────

  async function loadWorkers(seq) {
    const data = await api("GET", orgPath() + "/workers");
    if (seq !== state.seq) {
      return;
    }
    const body = $("workers-body");
    body.replaceChildren();
    ((data && data.items) || []).forEach((worker) => body.appendChild(workerRow(worker)));
    setEmpty("workers-body", "workers-empty");
  }

  function workerRow(worker) {
    const row = el("tr", worker.revoked ? "inactive" : null);
    row.appendChild(cell(worker.name));
    row.appendChild(cell(mono(worker.id)));

    const status = el("span");
    status.appendChild(el("span", worker.online ? "dot online" : "dot"));
    status.appendChild(document.createTextNode(worker.online ? "online" : "offline"));
    row.appendChild(cell(status));

    row.appendChild(cell(timeNode(worker.last_seen)));
    row.appendChild(cell(worker.runtime_version));
    row.appendChild(cell(yesNo(worker.approved)));
    row.appendChild(cell(yesNo(worker.approved_private)));
    row.appendChild(cell(yesNo(worker.revoked)));

    const caps = el("div");
    caps.appendChild(candidateList("Reported", worker.reported_capabilities || []));
    caps.appendChild(candidateList("Approved", worker.approved_capabilities || []));
    row.appendChild(cell(caps));

    const actions = el("div", "actions");
    if (!worker.revoked) {
      actions.appendChild(
        button("Approve private", "small", (event) => approveWorker(event.currentTarget, worker, true))
      );
      actions.appendChild(
        button("Approve non-private", "small secondary", (event) => approveWorker(event.currentTarget, worker, false))
      );
      actions.appendChild(button("Revoke", "small danger", (event) => revokeWorker(event.currentTarget, worker)));
    }
    row.appendChild(cell(actions));
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

  function revokeWorker(trigger, worker) {
    const label = worker.name ? '"' + worker.name + '"' : worker.id;
    if (!window.confirm("Revoke worker " + label + "? Its token stops working immediately.")) {
      return;
    }
    return run(trigger, async () => {
      await api("POST", workerPath(worker) + "/revoke");
      loadTab();
    });
  }

  // ── Credentials ──────────────────────────────────────────────────────────

  async function loadCredentials(seq) {
    const data = await api("GET", orgPath() + "/credentials");
    if (seq !== state.seq) {
      return;
    }
    const body = $("credentials-body");
    body.replaceChildren();
    ((data && data.items) || []).forEach((credential) => body.appendChild(credentialRow(credential)));
    setEmpty("credentials-body", "credentials-empty");
  }

  function credentialRow(credential) {
    const row = el("tr", credential.revoked_at ? "inactive" : null);
    row.appendChild(cell(mono(credential.id)));
    row.appendChild(cell(ROLE_LABELS[credential.role] || credential.role));
    row.appendChild(cell(credential.worker_id ? mono(credential.worker_id) : ""));
    row.appendChild(cell(timeNode(credential.created_at)));
    row.appendChild(cell(timeNode(credential.revoked_at)));

    const actions = el("div", "actions");
    if (!credential.revoked_at) {
      if (credential.role === "worker") {
        actions.appendChild(el("span", "muted", "Revoke the worker instead"));
      } else {
        const revoke = button("Revoke", "small danger", (event) => revokeCredential(event.currentTarget, credential));
        if (state.me && credential.id === state.me.credential_id) {
          revoke.disabled = true;
          revoke.title = "You are signed in with this credential.";
        }
        actions.appendChild(revoke);
      }
    }
    row.appendChild(cell(actions));
    return row;
  }

  function revokeCredential(trigger, credential) {
    if (!window.confirm("Revoke credential " + credential.id + "? Anything using it stops working immediately.")) {
      return;
    }
    return run(trigger, async () => {
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
    $("issued-role").textContent = ROLE_LABELS[issued.role] || issued.role;
    $("issued-id").textContent = issued.id;
    $("issued-token").value = issued.token;
    $("issued-copied").hidden = true;
    $("issued").hidden = false;
    $("issued-token").focus();
    $("issued-token").select();
  }

  function hideIssued() {
    $("issued").hidden = true;
    $("issued-token").value = "";
    $("issued-role").textContent = "";
    $("issued-id").textContent = "";
    $("issued-copied").hidden = true;
  }

  async function copyIssued() {
    const input = $("issued-token");
    let copied = false;
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(input.value);
        copied = true;
      } catch (_) {
        copied = false;
      }
    }
    if (!copied) {
      input.focus();
      input.select();
      try {
        copied = document.execCommand("copy");
      } catch (_) {
        copied = false;
      }
    }
    if (copied) {
      $("issued-copied").hidden = false;
    } else {
      showError("Copy failed. Select the token and copy it manually.");
    }
  }

  // ── Jobs ─────────────────────────────────────────────────────────────────

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
    }
    $("jobs-actions-head").hidden = !canManageJobs();
    ((data && data.items) || []).forEach((job) => body.appendChild(jobRow(job)));
    state.jobsCursor = (data && data.next_cursor) || null;
    $("jobs-more").hidden = !state.jobsCursor;
    setEmpty("jobs-body", "jobs-empty");
  }

  function jobRow(job) {
    const row = el("tr");
    row.appendChild(cell(mono(job.job_id)));
    row.appendChild(cell(job.kind));
    row.appendChild(cell(job.status));
    row.appendChild(cell(job.queue_reason));
    row.appendChild(cell(yesNo(job.private)));
    row.appendChild(cell(job.execution_profile));
    row.appendChild(cell(job.candidate ? mono(formatCandidate(job.candidate)) : ""));
    row.appendChild(cell(job.attempt));
    row.appendChild(cell(job.error ? String(job.error.code) + ": " + String(job.error.message) : ""));
    row.appendChild(cell(job.result_available ? "available" : "—"));
    row.appendChild(cell(timeNode(job.created_at)));
    row.appendChild(cell(timeNode(job.updated_at)));
    row.appendChild(cell(timeNode(job.deadline_at)));

    if (canManageJobs()) {
      const actions = el("div", "actions");
      if (job.status === "pending" || job.status === "processing") {
        actions.appendChild(button("Cancel", "small secondary", (event) => cancelJob(event.currentTarget, row, job)));
      }
      actions.appendChild(button("Delete", "small danger", (event) => deleteJob(event.currentTarget, row, job)));
      row.appendChild(cell(actions));
    }
    return row;
  }

  function jobPath(job) {
    return "/v1/jobs/" + encodeURIComponent(job.job_id);
  }

  function cancelJob(trigger, row, job) {
    return run(trigger, async () => {
      const updated = await api("POST", jobPath(job) + "/cancel");
      if (row.isConnected && updated && updated.job_id !== undefined) {
        row.replaceWith(jobRow(updated));
      } else {
        loadTab();
      }
    });
  }

  function deleteJob(trigger, row, job) {
    if (!window.confirm("Delete job " + job.job_id + "? Its upload and result are removed.")) {
      return;
    }
    return run(trigger, async () => {
      await api("DELETE", jobPath(job));
      row.remove();
      setEmpty("jobs-body", "jobs-empty");
    });
  }

  function loadMoreJobs(trigger) {
    const seq = state.seq;
    return run(trigger, () => loadJobs(seq, true));
  }

  // ── Settings & baselines ─────────────────────────────────────────────────

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
      row.appendChild(cell(baseline.execution_profile));
      row.appendChild(cell(baseline.platform));
      row.appendChild(cell(baseline.revision));
      row.appendChild(cell(mono(baseline.evaluation_id)));
      row.appendChild(cell(baseline.release_id ? mono(baseline.release_id) : ""));
      const sha = mono(baseline.corpus_sha256 ? String(baseline.corpus_sha256).slice(0, 12) : "");
      if (baseline.corpus_sha256) {
        sha.title = baseline.corpus_sha256;
      }
      row.appendChild(cell(sha));
      row.appendChild(cell(timeNode(baseline.created_at)));
      baselineBody.appendChild(row);
    });
    setEmpty("baselines-body", "baselines-empty");

    const releaseBody = $("releases-body");
    releaseBody.replaceChildren();
    ((baselines && baselines.approved_releases) || []).forEach((release) => {
      const row = el("tr");
      row.appendChild(cell(release.execution_profile));
      row.appendChild(cell(release.platform));
      row.appendChild(cell(mono(release.release_id)));
      row.appendChild(cell(mono(release.evaluation_id)));
      row.appendChild(cell(timeNode(release.approved_at)));
      releaseBody.appendChild(row);
    });
    setEmpty("releases-body", "releases-empty");
  }

  // ── Wiring ───────────────────────────────────────────────────────────────

  function init() {
    $("banner-close").addEventListener("click", hideBanner);

    $("login-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("login-token");
      const token = input.value.trim();
      input.value = "";
      if (!token) {
        return;
      }
      hideBanner();
      state.token = token;
      saveToken(token);
      enter();
    });

    $("sign-out").addEventListener("click", () => signOut(""));

    $("org-select").addEventListener("change", (event) => selectOrganization(event.target.value));

    $("new-org-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("new-org-id");
      const id = input.value.trim();
      if (!id) {
        return;
      }
      const submit = event.target.querySelector("button[type=submit]");
      run(submit, async () => {
        const created = await api("POST", ADMIN_API + "/organizations", { id: id });
        input.value = "";
        await loadOrganizations(created && created.id ? created.id : id);
      });
    });

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => setTab(tab.dataset.tab));
    });

    document.querySelectorAll("[data-refresh]").forEach((node) => {
      node.addEventListener("click", () => loadTab());
    });

    $("issue-client").addEventListener("click", (event) => issueCredential(event.currentTarget, "client"));
    $("issue-admin").addEventListener("click", (event) => issueCredential(event.currentTarget, "org_admin"));
    $("issued-copy").addEventListener("click", copyIssued);
    $("issued-close").addEventListener("click", hideIssued);
    $("jobs-more").addEventListener("click", (event) => loadMoreJobs(event.currentTarget));

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
})();
