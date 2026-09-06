const $ = (selector) => document.querySelector(selector);
const stages = [
  "receiving",
  "validating",
  "transcribing",
  "aligning",
  "text_embedding",
  "voice_embedding",
  "saving",
];
let currentView = "";
let status = null;
let createdKeys = null;
let keyWarning = false;
let deviceKeyLoaded = false;
let busy = false;
let pollTimer = null;
let refreshPromise = null;

function setText(selector, value) {
  const element = $(selector);
  if (element.textContent !== value) element.textContent = value;
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
    ...options,
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "The request could not be completed. Please try again.",
    );
  }
  return body;
}

function showView(id, step) {
  document
    .querySelectorAll(".view")
    .forEach((view) => view.classList.toggle("hidden", view.id !== id));
  const steps = ["keys", "device", "reading", "upload", "profile"];
  document.querySelectorAll(".steps li").forEach((item) => {
    const active = item.dataset.step === step;
    item.classList.toggle("active", active);
    item.classList.toggle(
      "done",
      steps.indexOf(item.dataset.step) < steps.indexOf(step) ||
        status?.stage === "complete",
    );
    if (active) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  if (currentView !== id) {
    currentView = id;
    const heading = $(`#${id} h2`);
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
}

function showError(error) {
  $("#error-output").textContent =
    error.message || "Could not reach the server. Please try again.";
  $("#error-view").classList.remove("hidden");
}

async function loadDeviceKey() {
  if (deviceKeyLoaded) return;
  const data = await requestJson("/api/keys/public");
  $("#public-key-output").textContent = data.publicKey;
  const qr = $("#public-key-qr");
  qr.onerror = () => $("#qr-error").classList.remove("hidden");
  qr.onload = () => $("#qr-error").classList.add("hidden");
  qr.src = `/api/keys/qrcode?v=${Date.now()}`;
  deviceKeyLoaded = true;
}

function renderConnection() {
  let address;
  try {
    address = new URL(status.publicUrl || window.location.origin);
    if (!["http:", "https:"].includes(address.protocol))
      throw new Error("Invalid protocol");
  } catch {
    address = new URL(window.location.origin);
  }
  const local = ["localhost", "127.0.0.1", "[::1]", "0.0.0.0"].includes(
    address.hostname,
  );
  $("#backend-url").textContent = local
    ? `${address.protocol}//<server-address>`
    : `${address.protocol}//${address.hostname}`;
  $("#backend-port").textContent =
    address.port || (address.protocol === "https:" ? "443" : "80");
  $("#localhost-notice").classList.toggle("hidden", !local);
}

function renderProgress() {
  const failed = status.stage === "failed";
  const lastStage = failed ? status.history.at(-2)?.stage : status.stage;
  const activeIndex = stages.indexOf(lastStage);
  $("#processing-title").textContent = failed
    ? "Your voice profile needs another try"
    : "Creating your voice profile";
  setText(
    "#processing-message",
    failed
      ? "Processing has stopped."
      : status.history.at(-1)?.message || "Processing your recording...",
  );
  const receiving = status.stage === "receiving";
  $("#upload-progress-wrap").classList.toggle("hidden", !receiving);
  $("#upload-progress").max = status.totalChunks || 1;
  $("#upload-progress").value = status.receivedChunks;
  $("#upload-count").textContent =
    `${status.receivedChunks} of ${status.totalChunks} chunks received`;
  $("#processing-error").classList.toggle("hidden", !failed);
  $("#processing-error-message").textContent = status.error || "";
  $("#model-notice").classList.toggle("hidden", failed || receiving);
  document.querySelectorAll("#processing-steps li").forEach((item, index) => {
    item.classList.toggle("done", index < activeIndex);
    item.classList.toggle("active", !failed && index === activeIndex);
    item.classList.toggle("failed", failed && index === activeIndex);
  });
}

async function render() {
  if (!status.keysExist) {
    deviceKeyLoaded = false;
    showView("setup-view", "keys");
  } else if (keyWarning) {
    showView("warning-view", "keys");
  } else if (status.stage === "device") {
    await loadDeviceKey();
    showView("device-view", "device");
  } else if (status.stage === "reading") {
    setText("#reading-text", status.readingText);
    showView("reading-view", "reading");
  } else if (status.stage === "awaiting_upload") {
    renderConnection();
    showView("upload-view", "upload");
  } else if (status.stage === "complete") {
    if (!$("#complete-qr").getAttribute("src"))
      $("#complete-qr").src = "/api/keys/qrcode";
    showView("complete-view", "profile");
  } else {
    renderProgress();
    showView("processing-view", "profile");
  }
}

async function refresh() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      status = await requestJson("/api/setup/status");
      await render();
      setText("#connection-status", "Connected");
      $("#connection-status").classList.remove("offline");
      if ($("#error-view").dataset.connectionError === "true") {
        $("#error-view").classList.add("hidden");
        delete $("#error-view").dataset.connectionError;
      }
    } catch (error) {
      setText("#connection-status", "Connection lost. Reconnecting...");
      $("#connection-status").classList.add("offline");
      if (!status || currentView === "") {
        showError(error);
        $("#error-view").dataset.connectionError = "true";
      }
    }
  })();
  try {
    await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

async function poll() {
  if (!busy) await refresh();
  pollTimer = setTimeout(poll, 1000);
}

function action(selector, callback) {
  $(selector).addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    document
      .querySelectorAll(
        ".actions button, #create-keys-button, #reveal-created-keys-button",
      )
      .forEach((button) => (button.disabled = true));
    $("#error-view").classList.add("hidden");
    try {
      if (refreshPromise) await refreshPromise;
      await callback();
    } catch (error) {
      showError(error);
    } finally {
      busy = false;
      document
        .querySelectorAll("button")
        .forEach((button) => (button.disabled = false));
    }
  });
}

async function advance(step) {
  const next = await requestJson("/api/setup/step", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step }),
  });
  status = { ...status, ...next };
  if (step === "reading") {
    createdKeys = null;
    $("#private-key-output").textContent = "";
    $("#created-private-key").classList.add("hidden");
  }
  await render();
}

action("#create-keys-button", async () => {
  createdKeys = await requestJson("/api/keys/create", { method: "POST" });
  status.keysExist = true;
  keyWarning = createdKeys.created;
  if (keyWarning) {
    $("#private-key-path-warning").textContent =
      createdKeys.privateKeyHostPath || createdKeys.privateKeyPath;
    $("#private-key-output").textContent = createdKeys.privateKey;
    $("#created-private-key").classList.remove("hidden");
  }
  await render();
});
action("#reveal-created-keys-button", async () => {
  keyWarning = false;
  await render();
});
action("#next-reading-button", () => advance("reading"));
action("#finished-reading-button", () => advance("awaiting_upload"));
action("#read-again-button", () => advance("reading"));
action("#retry-reading-button", () => advance("reading"));
action("#retry-upload-button", () => advance("awaiting_upload"));
action("#retry-connection-button", refresh);

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = $(`#${button.dataset.copyTarget}`);
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(target.textContent);
      } else {
        const field = document.createElement("textarea");
        field.value = target.textContent;
        field.className = "clipboard-field";
        document.body.appendChild(field);
        field.select();
        let copied;
        try {
          copied = document.execCommand("copy");
        } finally {
          field.remove();
        }
        if (!copied) throw new Error("Clipboard unavailable");
      }
      $("#copy-status").textContent = "Key copied.";
    } catch {
      $("#copy-status").textContent =
        "Could not copy. Select the key and copy it manually.";
    }
  });
});
window.addEventListener("pagehide", () => clearTimeout(pollTimer));
window.addEventListener("pageshow", (event) => {
  if (event.persisted) poll();
});
poll();
