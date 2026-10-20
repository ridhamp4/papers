let state = { settings: {}, papers: [] };
const $ = (selector) => document.querySelector(selector);
const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);

async function api(path, options) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Request failed");
  return result;
}

function status(selector, message, error = false) {
  const element = $(selector);
  element.textContent = message;
  element.classList.toggle("error", error);
}

async function refresh() {
  state = await api("/api/state");
  $("#count").textContent = `${state.papers.length} paper${state.papers.length === 1 ? "" : "s"}`;
  $("#paper-list").innerHTML = state.papers.length
    ? state.papers.map((paper) => `<div class="paper">
        <div class="paper-title">${safe(paper.title)}</div>
        <p>${safe(paper.authors)} · ${safe(paper.year)}${paper.venue ? " · " + safe(paper.venue) : ""}</p>
        ${Array.isArray(paper.institutes) && paper.institutes.some(Boolean) ? `<p>${safe([...new Set(paper.institutes.filter(Boolean))].join(" · "))}</p>` : ""}
        <div class="row">
          <button type="button" data-edit="${safe(paper.slug)}">Edit details</button>
          <a target="_blank" href="/site/papers/${encodeURIComponent(paper.slug)}/">View page ↗</a>
          ${paper.pdf ? '<span class="badge">PDF ready</span>' : '<span class="badge">No PDF</span>'}
          <button class="delete" type="button" data-delete="${safe(paper.slug)}">Delete</button>
        </div>
      </div>`).join("")
    : '<p class="empty">Your library is empty.</p>';

  for (const [key, value] of Object.entries(state.settings)) {
    const input = $(`#settings-form [name="${key}"]`);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value || "";
  }
  $("#github-url").value = state.settings.github_repo || "";
}

$("#paper-list").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-edit], button[data-delete]");
  if (!button) return;

  if (button.dataset.delete) {
    const slug = button.dataset.delete;
    const paper = state.papers.find((item) => item.slug === slug);
    if (!paper) return;
    if (!confirm(`Delete “${paper.title}” from the public site? Its local PDF and TeX ZIP will be kept in .paperhost-trash for recovery.`)) return;
    button.disabled = true;
    try {
      const result = await api(`/api/papers/${encodeURIComponent(slug)}`, { method: "DELETE" });
      await refresh();
      status("#library-status", result.message);
    } catch (error) {
      status("#library-status", error.message, true);
      button.disabled = false;
    }
    return;
  }

  const slug = button.dataset.edit;
  const paper = state.papers.find((item) => item.slug === slug);
  if (!paper) return;
  for (const key of ["slug", "title", "authors", "institutes", "abstract", "year", "venue", "date", "arxiv", "openreview", "proceedings"]) {
    $(`#edit-form [name="${key}"]`).value = key === "institutes" ? (paper.institutes || []).join("\n") : (paper[key] || "");
  }
  $("#file-form").dataset.slug = slug;
  $("#file-form").reset();
  status("#edit-status", "");
  status("#file-status", "");
  $("#editor").showModal();
});

$("#close-dialog").onclick = () => $("#editor").close();
$("#cancel-dialog").onclick = () => $("#editor").close();

$("#add-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  status("#add-status", "Adding paper…");
  try {
    const result = await api("/api/papers", { method: "POST", body: new FormData(event.target) });
    event.target.reset();
    await refresh();
    status("#add-status", result.message);
  } catch (error) {
    status("#add-status", error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#settings-form").onsubmit = async (event) => {
  event.preventDefault();
  const fields = Object.fromEntries(new FormData(event.target));
  try {
    const result = await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields) });
    status("#settings-status", result.message);
    await refresh();
  } catch (error) {
    status("#settings-status", error.message, true);
  }
};

$("#github-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  status("#github-status", "Connecting repository…");
  try {
    const result = await api("/api/github", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: $("#github-url").value }) });
    await refresh();
    status("#github-status", result.message);
  } catch (error) {
    status("#github-status", error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#edit-form").onsubmit = async (event) => {
  event.preventDefault();
  const fields = Object.fromEntries(new FormData(event.target));
  const slug = fields.slug;
  delete fields.slug;
  try {
    const result = await api(`/api/papers/${encodeURIComponent(slug)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields) });
    $("#editor").close();
    await refresh();
    status("#library-status", result.message);
  } catch (error) {
    status("#edit-status", error.message, true);
  }
};

$("#file-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  status("#file-status", "Updating files…");
  try {
    const result = await api(`/api/papers/${encodeURIComponent(event.target.dataset.slug)}/files`, { method: "POST", body: new FormData(event.target) });
    event.target.reset();
    await refresh();
    status("#file-status", result.message);
  } catch (error) {
    status("#file-status", error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#publish").onclick = async (event) => {
  const button = event.target;
  button.disabled = true;
  button.textContent = "Publishing…";
  try {
    const result = await api("/api/publish", { method: "POST" });
    status("#github-status", result.message);
  } catch (error) {
    status("#github-status", error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Publish to GitHub";
  }
};

refresh().catch((error) => status("#add-status", error.message, true));
