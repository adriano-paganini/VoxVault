const loadingView = document.querySelector("#loading-view");
const setupView = document.querySelector("#setup-view");
const warningView = document.querySelector("#warning-view");
const createdKeysView = document.querySelector("#created-keys-view");
const publicKeyView = document.querySelector("#public-key-view");
const publicKeySummary = document.querySelector("#public-key-summary");
const publicKeyCard = document.querySelector("#public-key-card");
const errorView = document.querySelector("#error-view");
const errorOutput = document.querySelector("#error-output");
const uploadOutput = document.querySelector("#upload-output");
const refreshUploadsButton = document.querySelector("#refresh-uploads-button");

let createdKeys = null;

function showOnly(...views) {
  [
    loadingView,
    setupView,
    warningView,
    createdKeysView,
    publicKeyView,
    errorView,
  ].forEach((view) => view.classList.add("hidden"));

  views.forEach((view) => view.classList.remove("hidden"));
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw body;
  }

  return body;
}

function showError(error) {
  errorOutput.textContent = JSON.stringify(error, null, 2);
  showOnly(errorView);
}

function clearElement(id) {
  const element = document.querySelector(`#${id}`);
  element.innerHTML = "";
}

function clearInput(id) {
  const element = document.querySelector(`#${id}`);
  element.textContent = "";
}

function showQrFetchError(target, status) {
  target.replaceChildren();
  const message = document.createElement("p");
  message.className = "qr-error";
  message.textContent = `QR code failed to load (${status}).`;
  target.appendChild(message);
}

function permissionCommands(privateKeyPath) {
  return [
    `sudo chown root:root ${privateKeyPath}`,
    `sudo chmod 600 ${privateKeyPath}`,
  ].join("\n");
}

async function renderQr(targetId, text) {
  const target = document.querySelector(`#${targetId}`);
  const response = await fetch("/api/qrcode", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text}),
  });

  if (!response.ok) {
    throw await response.json();
  }

  target.innerHTML = await response.text();
}

async function renderPublicKeyQr(targetId, fallbackDeepLink) {
  if (!fallbackDeepLink) {
    const target = document.querySelector(`#${targetId}`);
    showQrFetchError(target, "image error");
    return;
  }

  await renderQr(targetId, fallbackDeepLink);
}

function publicKeyDeepLink(data) {
  return data.publicKeyDeepLink || `voxvault://setup?key=${encodeURIComponent(data.publicKey)}`;
}

async function showPublicKey() {
  const data = await requestJson("/api/keys/public");
  document.querySelector("#public-key-output").textContent = data.publicKey;
  await renderPublicKeyQr("public-key-qr", publicKeyDeepLink(data));
  publicKeySummary.classList.add("hidden");
  publicKeyCard.classList.remove("hidden");
  showOnly(publicKeyView);
}

function showPublicKeySummary() {
  clearInput("public-key-output");
  clearElement("public-key-qr");
  publicKeyCard.classList.add("hidden");
  publicKeySummary.classList.remove("hidden");
  showOnly(publicKeyView);
}

async function revealCreatedKeys() {
  document.querySelector("#private-key-output").textContent = createdKeys.privateKey;
  document.querySelector("#created-public-key-output").textContent = createdKeys.publicKey;

  await renderQr("private-key-qr", createdKeys.privateKey);
  await renderPublicKeyQr("created-public-key-qr", publicKeyDeepLink(createdKeys));

  showOnly(createdKeysView);
}

function hideCreatedKeys() {
  clearInput("private-key-output");
  clearInput("created-public-key-output");
  clearElement("private-key-qr");
  clearElement("created-public-key-qr");
  showOnly(warningView);
}

function hidePublicKey() {
  showPublicKeySummary();
}

async function refreshUploads() {
  const data = await requestJson("/api/uploads");

  if (!data.uploads.length) {
    uploadOutput.textContent = "No uploads received.";
    return;
  }

  uploadOutput.textContent = JSON.stringify(data.uploads, null, 2);
}

document.querySelector("#create-keys-button").addEventListener("click", async () => {
  try {
    createdKeys = await requestJson("/api/keys/create", {method: "POST"});

    if (!createdKeys.created) {
      await showPublicKey();
      return;
    }

    const privateKeyHostPath = createdKeys.privateKeyHostPath || createdKeys.privateKeyPath;
    document.querySelector("#private-key-path-warning").textContent = privateKeyHostPath;
    document.querySelector("#permission-commands").textContent = permissionCommands(privateKeyHostPath);
    showOnly(warningView);
  } catch (error) {
    showError(error);
  }
});

document.querySelector("#reveal-created-keys-button").addEventListener("click", async () => {
  try {
    await revealCreatedKeys();
  } catch (error) {
    showError(error);
  }
});

document.querySelector("#hide-created-keys-button").addEventListener("click", hideCreatedKeys);

document.querySelector("#display-public-key-button").addEventListener("click", async () => {
  try {
    await showPublicKey();
  } catch (error) {
    showError(error);
  }
});

document.querySelector("#hide-public-key-button").addEventListener("click", hidePublicKey);
refreshUploadsButton.addEventListener("click", refreshUploads);

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.querySelector(`#${button.dataset.copyTarget}`);
    const originalText = button.textContent;
    const copyText = target.textContent;

    await navigator.clipboard.writeText(copyText);
    button.textContent = "Copied";
    button.classList.add("copied");
    setTimeout(() => {
      button.textContent = originalText;
      button.classList.remove("copied");
    }, 1200);
  });
});

async function initialize() {
  try {
    await refreshUploads();

    const status = await requestJson("/api/keys/status");

    if (status.keysExist) {
      showPublicKeySummary();
      return;
    }

    showOnly(setupView);
  } catch (error) {
    showError(error);
  }
}

initialize();
setInterval(refreshUploads, 2000);
