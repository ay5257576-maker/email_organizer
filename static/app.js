/* AI Email Organizer – Frontend with Gmail OAuth */

const API = "/api";

const token = localStorage.getItem("token");
if (!token) {
  window.location.href = "/login";
}

let currentUser = null;
try {
  currentUser = JSON.parse(localStorage.getItem("user") || "null");
} catch {
  currentUser = null;
}

let currentFilter = "all";
let selectedId = null;
let emailsCache = [];

// ---------- DOM ----------
const emailListEl = document.getElementById("email-list");
const emptyStateEl = document.getElementById("empty-state");
const appEl = document.querySelector(".app");
const viewTitle = document.getElementById("view-title");
const totalCountEl = document.getElementById("total-count");
const statsBar = document.getElementById("stats-bar");
const modalOverlay = document.getElementById("modal-overlay");
const emailForm = document.getElementById("email-form");
const toastEl = document.getElementById("toast");

// ---------- Helpers ----------
function formatDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso.slice(0, 10);
  }
}

function showToast(msg, type = "success") {
  toastEl.textContent = msg;
  toastEl.className = `toast show ${type}`;
  setTimeout(() => toastEl.classList.remove("show"), 3500);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${localStorage.getItem("token")}`,
    ...options.headers,
  };
  const res = await fetch(`${API}${path}`, { ...options, headers });

  if (res.status === 401) {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    window.location.href = "/login";
    throw new Error("Session expired");
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ---------- User UI ----------
function renderUser() {
  if (!currentUser) return;
  document.getElementById("user-name").textContent = currentUser.full_name || "User";
  document.getElementById("user-email").textContent = currentUser.email || "";
  const initial = (currentUser.full_name || currentUser.email || "U")[0].toUpperCase();
  document.getElementById("user-avatar").textContent = initial;
  updateInboxStatusUI();
}

function updateInboxStatusUI() {
  const box = document.getElementById("inbox-status");
  const text = document.getElementById("inbox-status-text");
  const connectBtn = document.getElementById("connect-btn");
  const syncBtn = document.getElementById("sync-btn");

  if (currentUser && (currentUser.gmail_connected || currentUser.inbox_connected)) {
    box.classList.add("connected");
    text.innerHTML = `Gmail Connected<br><strong style="color:var(--text)">Ready</strong>`;
    connectBtn.style.display = "none";
    syncBtn.style.display = "block";
  } else {
    box.classList.remove("connected");
    text.textContent = "Gmail not connected";
    connectBtn.style.display = "block";
    syncBtn.style.display = "none";
  }
}

// ---------- Load emails ----------
async function loadEmails() {
  let url = "/emails";
  if (currentFilter === "High") {
    url += "?priority=High";
  } else if (currentFilter !== "all") {
    url += `?category=${encodeURIComponent(currentFilter)}`;
  }

  const data = await api(url);
  emailsCache = data.emails;
  renderList();
  loadStats();
}

function renderList() {
  emailListEl.innerHTML = "";
  if (emailsCache.length === 0) {
    emptyStateEl.style.display = "block";
    return;
  }
  emptyStateEl.style.display = "none";

  emailsCache.forEach((email) => {
    const card = document.createElement("div");
    card.className = "email-card" + (email.id === selectedId ? " active" : "");
    card.dataset.id = email.id;
    card.innerHTML = `
      <div class="email-card-main">
        <div class="email-top">
          <span class="email-sender">${escapeHtml(email.sender)}</span>
          <span class="email-date">${formatDate(email.received_at || email.created_at)}</span>
        </div>
        <div class="email-subject">${escapeHtml(email.subject)}</div>
        <div class="email-summary">${escapeHtml(email.summary || "")}</div>
      </div>
      <div class="email-tags">
        <span class="tag tag-category">${escapeHtml(email.category)}</span>
        <span class="tag tag-priority-${email.priority}">${email.priority}</span>
      </div>
    `;
    card.addEventListener("click", () => openDetail(email.id));
    emailListEl.appendChild(card);
  });
}

async function loadStats() {
  const stats = await api("/stats");
  totalCountEl.textContent = stats.total;

  if (stats.gmail_connected !== undefined) {
    currentUser.gmail_connected = stats.gmail_connected;
    currentUser.inbox_connected = stats.inbox_connected;
    currentUser.last_sync = stats.last_sync;
    localStorage.setItem("user", JSON.stringify(currentUser));
    updateInboxStatusUI();
  }

  let html = `<div class="stat-chip"><strong>${stats.total}</strong> total</div>`;
  for (const [cat, count] of Object.entries(stats.by_category || {})) {
    html += `<div class="stat-chip">${cat}: <strong>${count}</strong></div>`;
  }
  if (stats.last_sync) {
    html += `<div class="stat-chip">Last sync: ${formatDate(stats.last_sync)}</div>`;
  }
  statsBar.innerHTML = html;
}

// ---------- Detail ----------
async function openDetail(id) {
  selectedId = id;
  const email = emailsCache.find((e) => e.id === id) || (await api(`/emails/${id}`));

  document.getElementById("detail-subject").textContent = email.subject;
  document.getElementById("detail-meta").innerHTML = `
    <span>From: <strong>${escapeHtml(email.sender)}</strong></span>
    <span>${formatDate(email.received_at || email.created_at)}</span>
    <span class="tag tag-category">${escapeHtml(email.category)}</span>
    <span class="tag tag-priority-${email.priority}">${email.priority}</span>
  `;
  document.getElementById("detail-summary").textContent = email.summary || "No summary available.";
  document.getElementById("detail-body").textContent = email.body;

  const actionsEl = document.getElementById("detail-actions");
  actionsEl.innerHTML = "";
  (email.action_items || []).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    actionsEl.appendChild(li);
  });

  document.getElementById("edit-category").value = email.category;
  document.getElementById("edit-priority").value = email.priority;

  appEl.classList.add("detail-open");
  renderList();
}

function closeDetail() {
  selectedId = null;
  appEl.classList.remove("detail-open");
  renderList();
}

// ---------- Gmail Connect ----------
async function connectGmail() {
  try {
    showToast("Redirecting to Google...");
    const res = await api("/auth/gmail");   // this is under /api because of how we call it
    // Wait - the route is /auth/gmail (not under /api)
    // So we call it differently
  } catch (e) {
    // fallback
  }

  // Correct call (route is outside /api)
  const res = await fetch("/auth/gmail", {
    headers: {
      Authorization: `Bearer ${localStorage.getItem("token")}`,
    },
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    showToast(err.detail || "Failed to start Gmail login", "error");
    return;
  }

  const data = await res.json();
  if (data.authorization_url) {
    // Redirect browser to Google
    window.location.href = data.authorization_url;
  } else {
    showToast("No authorization URL received", "error");
  }
}

async function syncGmail() {
  const btn = document.getElementById("sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing...";
  try {
    const res = await api("/inbox/gmail-sync", { method: "POST" });
    showToast(`Synced! ${res.added} new emails added.`);
    await loadEmails();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "🔄 Sync Inbox";
  }
}

// ---------- Events ----------
document.getElementById("connect-btn").addEventListener("click", connectGmail);
document.getElementById("empty-connect-btn")?.addEventListener("click", connectGmail);
document.getElementById("sync-btn").addEventListener("click", syncGmail);

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    viewTitle.textContent =
      currentFilter === "all" ? "All Emails" :
      currentFilter === "High" ? "High Priority" : currentFilter;
    selectedId = null;
    appEl.classList.remove("detail-open");
    loadEmails();
  });
});

document.getElementById("close-detail").addEventListener("click", closeDetail);

document.getElementById("add-email-btn").addEventListener("click", () => {
  modalOverlay.classList.add("open");
  document.getElementById("sender").focus();
});

document.getElementById("close-modal").addEventListener("click", closeModal);
document.getElementById("cancel-btn").addEventListener("click", closeModal);

function closeModal() {
  modalOverlay.classList.remove("open");
  emailForm.reset();
}

emailForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    sender: document.getElementById("sender").value.trim(),
    subject: document.getElementById("subject").value.trim(),
    body: document.getElementById("body").value.trim(),
  };
  try {
    const email = await api("/emails", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showToast("Email analyzed & added!");
    closeModal();
    await loadEmails();
    openDetail(email.id);
  } catch (err) {
    showToast(err.message || "Failed to add email", "error");
  }
});

document.getElementById("save-edit-btn").addEventListener("click", async () => {
  if (!selectedId) return;
  const category = document.getElementById("edit-category").value;
  const priority = document.getElementById("edit-priority").value;
  try {
    await api(`/emails/${selectedId}`, {
      method: "PATCH",
      body: JSON.stringify({ category, priority }),
    });
    showToast("Updated");
    await loadEmails();
    openDetail(selectedId);
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("delete-email-btn").addEventListener("click", async () => {
  if (!selectedId) return;
  if (!confirm("Delete this email?")) return;
  try {
    await api(`/emails/${selectedId}`, { method: "DELETE" });
    showToast("Deleted");
    closeDetail();
    await loadEmails();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("reorganize-btn").addEventListener("click", async () => {
  try {
    const res = await api("/reorganize", { method: "POST" });
    showToast(`Re-organized ${res.count} emails`);
    await loadEmails();
    if (selectedId) openDetail(selectedId);
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  localStorage.removeItem("token");
  localStorage.removeItem("user");
  window.location.href = "/login";
});

modalOverlay.addEventListener("click", (e) => {
  if (e.target === modalOverlay) closeModal();
});

// Handle redirect back from Google
const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get("gmail_connected") === "1") {
  showToast("Gmail connected successfully! Emails are being organized.");
  // clean URL
  window.history.replaceState({}, document.title, "/");
  // refresh user info
  setTimeout(() => loadEmails(), 1000);
}
if (urlParams.get("gmail_error")) {
  showToast("Gmail connection failed: " + urlParams.get("gmail_error"), "error");
  window.history.replaceState({}, document.title, "/");
}

// ---------- Init ----------
renderUser();
loadEmails().catch((err) => {
  console.error(err);
  showToast("Failed to load emails", "error");
});
