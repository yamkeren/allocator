"use strict";

const POLL_MS = 3000;
const DEVICE_CLASSES = [
  "WIFI", "ETHERNET", "BLUETOOTH", "AUDIO", "VIDEO", "HID",
  "MASS_STORAGE", "PRINTER", "IMAGE", "SMARTCARD", "SERIAL", "GENERIC",
];

let selectedSessionId = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function shortId(id) {
  return String(id).slice(0, 8);
}

async function refresh() {
  let data;
  try {
    const res = await fetch("/api/v1/overview");
    if (!res.ok) throw new Error("HTTP " + res.status);
    data = await res.json();
  } catch (err) {
    document.getElementById("manager-status").textContent = "unreachable";
    document.getElementById("manager-status").className = "badge offline";
    return;
  }
  render(data);
}

function render(data) {
  const ms = document.getElementById("manager-status");
  ms.textContent = data.manager.status;
  ms.className = "badge " + (data.manager.status === "healthy" ? "healthy" : "offline");
  document.getElementById("manager-version").textContent = "v" + data.manager.version;
  document.getElementById("updated").textContent =
    "updated " + new Date().toLocaleTimeString();

  renderNodes(data.nodes);
  renderActive(data.active_sessions);
  renderPending(data.pending_sessions);

  // Drop a stale selection (session may have been released), else re-apply.
  const stillThere = data.active_sessions.some((s) => s.session_id === selectedSessionId);
  if (!stillThere) selectedSessionId = null;
  applyHighlight();
}

function renderNodes(nodes) {
  const root = document.getElementById("nodes");
  if (!nodes.length) {
    root.innerHTML = '<div class="empty">No nodes registered.</div>';
    return;
  }
  root.innerHTML = nodes.map((n) => {
    const statusBadge = `<span class="badge ${n.status === "ONLINE" ? "online" : "offline"}">${esc(n.status)}</span>`;
    const frozenBadge = n.frozen ? '<span class="badge frozen">frozen</span>' : "";
    const freezeBtn = n.frozen
      ? `<button data-action="unfreeze" data-node="${esc(n.name)}">Unfreeze</button>`
      : `<button data-action="freeze" data-node="${esc(n.name)}">Freeze</button>`;
    const devices = n.devices.length
      ? n.devices.map((d) => renderDevice(n.name, d)).join("")
      : '<li class="device"><span class="empty">no devices</span></li>';
    return `
      <article class="node" data-node-name="${esc(n.name)}">
        <div class="node-head">
          <span class="name">${esc(n.name)}</span>
          ${statusBadge}${frozenBadge}
          <span class="muted">${esc(n.ip_address)}</span>
          <span class="actions">
            ${freezeBtn}
            <button class="danger" data-action="delete-node" data-node="${esc(n.name)}" title="Mark node OFFLINE">Mark Offline</button>
          </span>
        </div>
        <ul class="devices">${devices}</ul>
      </article>`;
  }).join("");
}

function renderDevice(nodeName, d) {
  const cls = "s-" + d.status.toLowerCase();
  const owner = d.owner
    ? `<span class="owner">→ ${esc(d.owner.client_id)} <span class="muted">(${shortId(d.owner.session_id)})</span></span>`
    : "";
  return `
    <li class="device ${cls}" data-device-id="${esc(d.device_id)}"
        data-node="${esc(nodeName)}" data-logical="${esc(d.logical_name)}">
      <span class="logical">${esc(d.logical_name)}</span>
      <span class="muted">${esc(d.device_class)}</span>
      <span class="badge ${cls}">${esc(d.status)}</span>
      ${owner}
      <span class="actions">
        <button data-action="rename" data-node="${esc(nodeName)}" data-logical="${esc(d.logical_name)}">Rename</button>
        <button data-action="class" data-node="${esc(nodeName)}" data-logical="${esc(d.logical_name)}">Class</button>
      </span>
    </li>`;
}

function renderActive(sessions) {
  const root = document.getElementById("active-sessions");
  if (!sessions.length) {
    root.innerHTML = '<div class="empty">No active sessions.</div>';
    return;
  }
  root.innerHTML = sessions.map((s) => {
    const ids = s.devices.map((d) => d.device_id).join(",");
    const chips = s.devices.map((d) => `<span class="chip">${esc(d.logical_name)}</span>`).join("");
    return `
      <article class="session" data-session-id="${esc(s.session_id)}" data-device-ids="${esc(ids)}">
        <div class="line1">
          <span class="client">${esc(s.client_id)}</span>
          <span class="arrow">→</span>
          <span>${esc(s.node_name ?? "?")}</span>
          <span class="muted">${shortId(s.session_id)}</span>
          <span class="actions">
            <button class="danger" data-action="force-release" data-session-id="${esc(s.session_id)}">Force Release</button>
          </span>
        </div>
        <div class="chips">${chips}</div>
      </article>`;
  }).join("");
}

function renderPending(pending) {
  const root = document.getElementById("pending-sessions");
  if (!pending.length) {
    root.innerHTML = '<div class="empty">Queue is empty.</div>';
    return;
  }
  root.innerHTML = pending.map((p) => `
    <div class="pending">
      <span class="client">${esc(p.client_id)}</span>
      <span class="muted">waiting on</span>
      <span class="chips">${p.requested_devices.map((d) => `<span class="chip">${esc(d)}</span>`).join("")}</span>
    </div>`).join("");
}

function applyHighlight() {
  document.querySelectorAll(".device.highlight").forEach((el) => el.classList.remove("highlight"));
  document.querySelectorAll(".session.selected").forEach((el) => el.classList.remove("selected"));
  if (!selectedSessionId) return;
  const sessionEl = document.querySelector(`.session[data-session-id="${CSS.escape(selectedSessionId)}"]`);
  if (!sessionEl) return;
  sessionEl.classList.add("selected");
  const ids = (sessionEl.getAttribute("data-device-ids") || "").split(",").filter(Boolean);
  ids.forEach((id) => {
    const dev = document.querySelector(`.device[data-device-id="${CSS.escape(id)}"]`);
    if (dev) dev.classList.add("highlight");
  });
}

// ---- actions ----------------------------------------------------------------

async function call(method, path, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  return fetch(path, opts);
}

async function doRename(node, logical) {
  const newName = prompt(`Rename "${logical}" on ${node} to:`, logical);
  if (!newName || newName === logical) return;
  let res = await call("POST", `/api/v1/devices/${encodeURIComponent(node)}/${encodeURIComponent(logical)}/rename`, { name: newName, force: false });
  if (res.status === 409) {
    if (confirm(`"${newName}" is taken on ${node}. Reassign it (force)?`)) {
      res = await call("POST", `/api/v1/devices/${encodeURIComponent(node)}/${encodeURIComponent(logical)}/rename`, { name: newName, force: true });
    } else {
      return;
    }
  }
  if (!res.ok) alert("Rename failed: HTTP " + res.status);
  await refresh();
}

async function doClass(node, logical) {
  const cls = prompt(`Device class for "${logical}" (${DEVICE_CLASSES.join(", ")}):`);
  if (!cls) return;
  const up = cls.trim().toUpperCase();
  if (!DEVICE_CLASSES.includes(up)) { alert("Unknown class: " + cls); return; }
  const res = await call("PATCH", `/api/v1/devices/${encodeURIComponent(node)}/${encodeURIComponent(logical)}`, { device_class: up });
  if (!res.ok) alert("Update failed: HTTP " + res.status);
  await refresh();
}

async function handleAction(action, el) {
  const node = el.getAttribute("data-node");
  const logical = el.getAttribute("data-logical");
  const sid = el.getAttribute("data-session-id");
  switch (action) {
    case "freeze":
      await call("POST", `/api/v1/nodes/${encodeURIComponent(node)}/freeze`); await refresh(); break;
    case "unfreeze":
      await call("POST", `/api/v1/nodes/${encodeURIComponent(node)}/unfreeze`); await refresh(); break;
    case "delete-node":
      if (confirm(`Mark node "${node}" OFFLINE?`)) { await call("DELETE", `/api/v1/nodes/${encodeURIComponent(node)}`); await refresh(); } break;
    case "rename":
      await doRename(node, logical); break;
    case "class":
      await doClass(node, logical); break;
    case "force-release":
      if (confirm(`Force-release session ${shortId(sid)}? Devices free even if the agent is offline.`)) {
        await call("POST", `/api/v1/sessions/${encodeURIComponent(sid)}/force-release`); await refresh();
      }
      break;
  }
}

document.addEventListener("click", (ev) => {
  const actionEl = ev.target.closest("[data-action]");
  if (actionEl) {
    ev.stopPropagation();
    handleAction(actionEl.getAttribute("data-action"), actionEl);
    return;
  }
  const sessionEl = ev.target.closest(".session");
  if (sessionEl) {
    const id = sessionEl.getAttribute("data-session-id");
    selectedSessionId = selectedSessionId === id ? null : id;
    applyHighlight();
  }
});

refresh();
setInterval(refresh, POLL_MS);
