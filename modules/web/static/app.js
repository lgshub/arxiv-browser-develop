"use strict";

const $ = id => document.getElementById(id);
const state = { history: [], view: null, page: 1, pageSize: Number(document.body.dataset.pageSize), job: null, timer: null, lastFetched: -1, resultVersion: 0, downloadFile: null, selectedHistory: new Set(), deletingHistory: false };
const terminal = new Set(["completed", "failed", "cancelled"]);

function readLocal(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function writeLocal(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* History still lives on the server. */ }
}
async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
function post(url, data) {
  return api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function showError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}
function values() {
  return { keywords: $("keywords").value, field: $("field").value, expression: $("expression").value,
    mode: $("search-mode").value, recent_days: Number($("recent-days").value),
    limit: Number($("limit").value), include_abstract: $("include-abstract").checked };
}
function updateSearchMode() {
  const days = $("search-mode").value === "days";
  $("count-options").hidden = days; $("limit").disabled = days;
  $("date-options").hidden = !days; $("recent-days").disabled = !days;
  $("search-mode-hint").textContent = days ? "Fetch all matching papers first submitted within the past N × 24 hours (UTC)." : "Newest submissions first. Fetch up to the selected paper limit.";
}
function remember(options) {
  const entries = readLocal("paper-atlas-input-history", []);
  const signature = entry => JSON.stringify([entry.keywords, entry.field, entry.expression, entry.mode || "count", entry.mode === "days" ? entry.recent_days : entry.limit]);
  const key = signature(options);
  const unique = entries.filter(entry => signature(entry) !== key);
  writeLocal("paper-atlas-input-history", [options, ...unique].slice(0, 80));
  populateSuggestions();
}
function previousInputs() {
  return [...readLocal("paper-atlas-input-history", []), ...state.history];
}
function populateSuggestions() {
  const keywords = [...new Set(previousInputs().map(entry => entry.keywords).filter(Boolean))];
  $("keyword-history").replaceChildren(...keywords.slice(0, 80).map(value => {
    const option = node("option"); option.value = value; return option;
  }));
}
function expressionSuggestions() {
  const term = $("expression").value.toLowerCase();
  const expressions = [...new Set(previousInputs().map(entry => entry.expression).filter(Boolean))];
  const matches = expressions.filter(value => value.toLowerCase().includes(term)).slice(0, 6);
  $("expression-suggestions").replaceChildren(...matches.map(value => {
    const button = node("button", "", value); button.type = "button";
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => {
      $("expression").value = value; $("expression-suggestions").hidden = true; preview();
    });
    return button;
  }));
  $("expression-suggestions").hidden = !matches.length;
}
async function preview() {
  showError("form-error", "");
  try {
    const data = await post("/api/preview", values());
    $("compiled-query").textContent = data.query;
    $("query-preview").hidden = false;
    return data.query;
  } catch (error) {
    $("query-preview").hidden = true;
    showError("form-error", error.message);
    return null;
  }
}
function visibleHistory() {
  const filter = $("history-filter").value.toLowerCase();
  return state.history.filter(record => [record.filename, record.keywords, record.expression, record.query].join(" ").toLowerCase().includes(filter));
}
function updateHistorySelection(records = visibleHistory()) {
  const visibleSelected = records.filter(record => state.selectedHistory.has(record.filename)).length;
  $("select-history").checked = records.length > 0 && visibleSelected === records.length;
  $("select-history").indeterminate = visibleSelected > 0 && visibleSelected < records.length;
  $("select-history").disabled = state.deletingHistory || records.length === 0;
  const count = state.selectedHistory.size;
  $("delete-history").disabled = state.deletingHistory || count === 0;
  $("delete-history").textContent = state.deletingHistory ? "Deleting…" : count ? `Delete selected (${count})` : "Delete selected";
  $("clear-history-selection").hidden = count === 0;
  $("clear-history-selection").disabled = state.deletingHistory;
}
function renderHistory() {
  const records = visibleHistory();
  updateHistorySelection(records);
  if (!records.length) {
    $("history-list").replaceChildren(node("p", "muted", $("history-filter").value ? "No saved searches match." : "Your saved searches will appear here."));
    return;
  }
  $("history-list").replaceChildren(...records.map(record => {
    const row = node("div", "history-row");
    const checkbox = node("input", "history-checkbox"); checkbox.type = "checkbox";
    checkbox.checked = state.selectedHistory.has(record.filename);
    checkbox.disabled = state.deletingHistory;
    checkbox.setAttribute("aria-label", `Select ${record.filename}`);
    row.classList.toggle("selected", checkbox.checked);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedHistory.add(record.filename);
      else state.selectedHistory.delete(record.filename);
      row.classList.toggle("selected", checkbox.checked);
      updateHistorySelection();
    });
    const button = node("button", "history-item"); button.type = "button";
    if (state.view?.filename === record.filename) button.classList.add("active");
    button.title = record.query || record.filename;
    button.append(node("strong", "", record.keywords || record.expression || record.filename));
    const date = new Date(record.created_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const status = ["failed", "cancelled"].includes(record.status) ? ` · ${record.status}` : "";
    button.append(node("small", status ? "history-status" : "", `${record.count.toLocaleString("en-US")} papers · ${date}${status}`));
    if (record.mode === "days") button.append(node("small", "", `Date search · Past ${record.recent_days} days`));
    button.append(node("small", "history-filename", record.filename));
    button.addEventListener("click", () => openHistory(record));
    row.append(checkbox, button);
    return row;
  }));
}
async function refreshHistory() {
  try {
    state.history = (await api("/api/history")).records;
    const available = new Set(state.history.map(record => record.filename));
    state.selectedHistory = new Set([...state.selectedHistory].filter(filename => available.has(filename)));
    renderHistory(); populateSuggestions();
  } catch (error) { $("history-list").replaceChildren(node("p", "muted", `Could not load history: ${error.message}`)); }
}
async function deleteSelectedHistory() {
  if (state.deletingHistory || state.selectedHistory.size === 0) return;
  state.deletingHistory = true;
  showError("history-feedback", "");
  renderHistory();
  try {
    const data = await post("/api/history/delete", { filenames: [...state.selectedHistory] });
    const removed = new Set(data.deleted);
    const current = state.view?.filename || (state.view?.type === "job" ? state.job?.filename : null);
    if (current && removed.has(current)) {
      state.view = null; state.resultVersion++; state.page = 1;
      downloadLink(null);
      $("collection-label").textContent = "YOUR READING DESK";
      $("results-title").textContent = "Collection removed from history";
      $("results-caption").textContent = "Open another collection or start a new search.";
      $("collection-query").hidden = true; $("pagination").hidden = true;
      $("results").replaceChildren();
      if (!state.job || terminal.has(state.job.status)) $("progress-section").hidden = true;
    }
    if (state.job?.filename && removed.has(state.job.filename)) state.job.filename = null;
    for (const filename of removed) state.selectedHistory.delete(filename);
    await refreshHistory();
    const errors = data.errors || [];
    const message = `Permanently deleted ${removed.size} ${removed.size === 1 ? "search" : "searches"}.`;
    showError("history-feedback", errors.length ? `${message} Could not delete: ${errors.map(item => `${item.filename}: ${item.error}`).join("; ")}` : message);
  } catch (error) {
    showError("history-feedback", `Could not delete searches: ${error.message}`);
    await refreshHistory();
  } finally {
    state.deletingHistory = false;
    renderHistory();
  }
}
function downloadLink(filename) {
  state.downloadFile = filename;
  $("download-link").hidden = !filename;
  const sort = $("sort-authors").checked ? "author_count" : "original";
  if (filename) $("download-link").href = `/api/history/${encodeURIComponent(filename)}/download?sort=${sort}`;
}
async function openHistory(record) {
  state.view = { type: "history", filename: record.filename };
  state.page = 1;
  for (const field of ["keywords", "field", "expression"]) $(field).value = record[field];
  $("limit").value = record.limit ?? document.body.dataset.defaultLimit;
  $("recent-days").value = record.recent_days ?? document.body.dataset.defaultDays;
  $("search-mode").value = record.mode || "count"; updateSearchMode();
  $("include-abstract").checked = record.include_abstract;
  $("collection-label").textContent = "SAVED COLLECTION";
  $("results-title").textContent = record.keywords || "Saved search results";
  $("results-caption").textContent = record.filename;
  $("collection-query").textContent = record.query || "Imported JSON collection";
  $("collection-query").hidden = false;
  downloadLink(record.filename);
  renderHistory(); await loadPapers();
  if (record.query) { $("compiled-query").textContent = record.query; $("query-preview").hidden = false; }
  else $("query-preview").hidden = true;
  showError("form-error", "");
  if (!state.job || terminal.has(state.job.status)) {
    if (record.status === "imported") $("progress-section").hidden = true;
    else renderProgress({ ...record, fetched: record.count, target: record.mode === "days" ? record.total : Math.min(record.limit, record.total ?? record.limit) });
  }
}
function safeLink(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}
function paperCard(paper, index) {
  const card = node("article", "paper-card");
  card.append(node("span", "paper-number", String((state.page - 1) * state.pageSize + index + 1).padStart(2, "0")));
  const body = node("div", "paper-main"), title = node("h3");
  const link = safeLink(paper.url);
  if (link) {
    const anchor = node("a", "", paper.title); anchor.href = link; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; title.append(anchor);
  } else title.textContent = paper.title;
  body.append(title);
  const authors = node("p", "authors collapsed", paper.authors.join(" · "));
  body.append(authors);
  if (paper.authors.length > 10) {
    const toggle = node("button", "text-button author-toggle", `Show all ${paper.authors.length} authors`);
    toggle.addEventListener("click", () => {
      const collapsed = authors.classList.toggle("collapsed");
      toggle.textContent = collapsed ? `Show all ${paper.authors.length} authors` : "Show fewer authors";
    });
    body.append(toggle);
  }
  const meta = node("div", "paper-meta");
  meta.append(node("span", "author-badge", `${paper.authors.length} ${paper.authors.length === 1 ? "author" : "authors"}`));
  if (paper.published) {
    const date = new Date(paper.published);
    if (!Number.isNaN(date.getTime())) meta.append(node("span", "", date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" })));
  }
  if (link) {
    const anchor = node("a", "", "Read paper ↗"); anchor.href = link; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; meta.append(anchor);
  }
  body.append(meta);
  const subjects = Array.isArray(paper.subjects) ? paper.subjects : [];
  const subjectLine = node("p", "paper-subjects");
  subjectLine.append(node("strong", "", "Subjects: "), document.createTextNode(subjects.length ? subjects.join("; ") : "Not recorded in this saved search"));
  body.append(subjectLine);
  if ($("include-abstract").checked && paper.summary) body.append(node("p", "paper-summary", paper.summary));
  card.append(body);
  return card;
}
async function loadPapers() {
  if (!state.view) return;
  const version = ++state.resultVersion;
  const sort = $("sort-authors").checked ? "author_count" : "original";
  const base = state.view.type === "history" ? `/api/history/${encodeURIComponent(state.view.filename)}` : `/api/jobs/${state.view.id}`;
  try {
    const data = await api(`${base}/papers?page=${state.page}&sort=${sort}`);
    if (version !== state.resultVersion) return;
    state.page = data.page;
    state.pageSize = data.page_size;
    if (data.papers.length) $("results").replaceChildren(...data.papers.map(paperCard));
    else {
      const empty = node("div", "empty-state");
      empty.append(node("h3", "", state.job && !terminal.has(state.job.status) && state.view.type === "job" ? "Waiting for the first papers…" : "No papers in this collection."));
      empty.append(node("p", "", "Try a broader query or check the request activity above."));
      $("results").replaceChildren(empty);
    }
    $("pagination").hidden = data.count === 0;
    $("page-label").textContent = `Page ${data.page} of ${data.pages} · ${data.count.toLocaleString("en-US")} papers`;
    $("previous-page").disabled = data.page <= 1; $("next-page").disabled = data.page >= data.pages;
    if (state.view.type === "job") $("results-caption").textContent = `${data.count.toLocaleString("en-US")} papers retrieved${sort === "author_count" ? " · Most authors first" : " · Newest submissions first"}`;
  } catch (error) { showError("form-error", `Could not load papers: ${error.message}`); }
}
function renderProgress(job) {
  $("progress-section").hidden = false;
  const done = terminal.has(job.status);
  $("progress-state").textContent = { queued: "SEARCH QUEUED", running: "SEARCHING ARXIV", completed: "COLLECTION SAVED", failed: "SEARCH INTERRUPTED", cancelled: "SEARCH CANCELLED" }[job.status];
  $("progress-title").textContent = job.target == null ? `${job.fetched.toLocaleString("en-US")} papers retrieved · counting matches…` : `${job.fetched.toLocaleString("en-US")} of ${job.target.toLocaleString("en-US")} papers retrieved`;
  $("progress-bar").max = job.target || 1;
  if (job.target == null && !done) $("progress-bar").removeAttribute("value");
  else $("progress-bar").value = job.target === 0 && done ? 1 : job.fetched;
  const retrieval = job.mode === "days" ? `All matches · Past ${job.recent_days} days · First submissions (UTC)` : `Requested up to ${job.limit.toLocaleString("en-US")}`;
  $("progress-detail").textContent = job.total == null ? `Waiting for the first response from arXiv… · ${retrieval}` : `${job.total.toLocaleString("en-US")} matching papers · ${retrieval}`;
  $("progress-detail").title = job.date_start ? `${job.date_start} to ${job.date_end}` : "";
  $("elapsed").textContent = `${job.elapsed}s elapsed`;
  $("cancel-button").hidden = done;
  $("cancel-button").disabled = false;
  $("search-button").disabled = !done;
  $("search-button").textContent = done ? "Search ↗" : "Searching…";
  $("last-activity").textContent = job.events.at(-1)?.message || "";
  $("activity-log").replaceChildren(...job.events.map(event => node("div", "", `${event.time} UTC  ${event.message}`)));
  showError("job-error", job.error ? `${job.error} Any received papers are kept${job.filename ? " in search history" : " in this session"}.` : "");
  if (job.error) $("activity-details").open = true;
  if (state.view?.type === "job" && state.view.id === job.id) downloadLink(job.filename);
}
async function poll() {
  if (!state.job) return;
  try {
    const job = await api(`/api/jobs/${state.job.id}`);
    state.job = job; renderProgress(job);
    if (state.view?.type === "job" && state.view.id === job.id && state.lastFetched !== job.fetched) {
      state.lastFetched = job.fetched; await loadPapers();
    }
    if (terminal.has(job.status)) {
      writeLocal("paper-atlas-active-job", null);
      await refreshHistory(); return;
    }
  } catch (error) {
    showError("job-error", `Could not check search progress: ${error.message} Reconnecting in 3 seconds…`);
    $("search-button").disabled = false;
  }
  state.timer = setTimeout(poll, 3000);
}
$("search-form").addEventListener("submit", async event => {
  event.preventDefault(); $("expression-suggestions").hidden = true;
  if (!(await preview())) return;
  $("search-button").disabled = true;
  try {
    const options = values();
    const job = await post("/api/search", options);
    remember(options); clearTimeout(state.timer);
    state.job = job; state.view = { type: "job", id: job.id }; state.page = 1; state.lastFetched = -1;
    writeLocal("paper-atlas-active-job", job.id);
    $("collection-label").textContent = "LIVE COLLECTION";
    $("results-title").textContent = options.keywords || "Search results";
    $("collection-query").textContent = job.query; $("collection-query").hidden = false;
    downloadLink(null); renderProgress(job); renderHistory(); await poll();
  } catch (error) { showError("form-error", error.message); $("search-button").disabled = false; }
});
$("cancel-button").addEventListener("click", async () => {
  if (!state.job) return;
  try {
    const data = await post(`/api/jobs/${state.job.id}/cancel`, {});
    $("progress-detail").textContent = data.message;
    $("cancel-button").disabled = true;
  } catch (error) { showError("job-error", error.message); }
});
$("preview-button").addEventListener("click", preview);
document.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => {
  $("expression").value = button.dataset.example;
  $("expression-suggestions").hidden = true; preview();
}));
$("expression").addEventListener("focus", expressionSuggestions);
$("expression").addEventListener("input", expressionSuggestions);
$("expression").addEventListener("blur", () => setTimeout(() => { $("expression-suggestions").hidden = true; }, 150));
$("expression").addEventListener("keydown", event => { if (event.key === "Escape") $("expression-suggestions").hidden = true; });
for (const id of ["keywords", "expression", "field"]) $(id).addEventListener("input", () => { $("query-preview").hidden = true; });
$("search-mode").addEventListener("change", () => { updateSearchMode(); $("query-preview").hidden = true; });
$("recent-days").addEventListener("input", () => { $("query-preview").hidden = true; });
$("history-filter").addEventListener("input", renderHistory);
$("refresh-history").addEventListener("click", refreshHistory);
$("select-history").addEventListener("change", () => {
  for (const record of visibleHistory()) {
    if ($("select-history").checked) state.selectedHistory.add(record.filename);
    else state.selectedHistory.delete(record.filename);
  }
  renderHistory();
});
$("clear-history-selection").addEventListener("click", () => { state.selectedHistory.clear(); renderHistory(); });
$("delete-history").addEventListener("click", deleteSelectedHistory);
$("sort-authors").addEventListener("change", () => { state.page = 1; downloadLink(state.downloadFile); loadPapers(); });
$("include-abstract").addEventListener("change", () => {
  loadPapers();
  if ($("include-abstract").checked && state.view) {
    $("results-caption").textContent = "Abstracts appear when included in the saved search. Run a new search with this option enabled to fetch missing abstracts.";
  }
});
$("previous-page").addEventListener("click", () => { state.page--; loadPapers(); });
$("next-page").addEventListener("click", () => { state.page++; loadPapers(); });
$("new-search").addEventListener("click", () => {
  $("keywords").value = ""; $("expression").value = ""; $("field").value = "all";
  $("query-preview").hidden = true; showError("form-error", ""); $("keywords").focus();
});
$("load-starter").addEventListener("click", () => {
  const starter = state.history.find(record => record.filename === "cs_ro_recent_1000.json") || state.history[0];
  if (starter) openHistory(starter);
  else { $("expression").value = `cat:${document.body.dataset.defaultCategory}`; $("search-form").requestSubmit(); }
});
async function initialize() {
  await refreshHistory();
  const active = readLocal("paper-atlas-active-job", null);
  if (!active) return;
  try {
    const job = await api(`/api/jobs/${active}`);
    state.job = job; state.view = { type: "job", id: job.id };
    $("collection-query").textContent = job.query; $("collection-query").hidden = false;
    $("results-title").textContent = "Resumed search";
    $("collection-label").textContent = "LIVE COLLECTION";
    await poll();
  } catch { writeLocal("paper-atlas-active-job", null); }
}
updateSearchMode();
initialize();
