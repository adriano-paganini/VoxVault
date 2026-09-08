(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const api = "/api/explorer";
  const pageSize = 25;
  const state = {
    people: [], person: null, chunk: null, known: [], knownChunkId: null,
    searchOffset: 0, knownOffset: 0, candidateOffset: 0,
    searchQuery: "", searchAssignment: "all", searchMode: "semantic",
    view: "conversations", conversation: null, conversationId: null, conversationsOffset: 0,
    context: {}, matchIndex: 0, speakerChunk: null, speakerPeople: [],
    source: "chunk", scope: "other", busy: false,
  };
  const epochs = { search: 0, people: 0, chunk: 0, person: 0, known: 0, candidates: 0, conversations: 0, conversation: 0, speaker: 0 };
  let pendingDeletion = null;
  if (new URLSearchParams(location.search).get("app") === "1") document.body.classList.add("in-app");

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function icon(name) {
    const node = element("img");
    node.src = `/static/icons/${name}.svg`;
    node.alt = "";
    return node;
  }

  function button(text, iconName, callback, className = "secondary compact") {
    const node = element("button", className);
    node.type = "button";
    if (iconName) node.append(icon(iconName));
    if (text) node.append(document.createTextNode(text));
    node.addEventListener("click", callback);
    return node;
  }

  function showError(error, inInspector = $("#inspector").open) {
    const target = $($("#speaker-picker").open ? "#speaker-error" : inInspector ? "#inspector-error" : "#page-error");
    target.textContent = error.message || "The request failed. Please try again.";
    target.classList.remove("hidden");
  }

  function clearError(inInspector) {
    $(inInspector ? "#inspector-error" : "#page-error").classList.add("hidden");
    $("#speaker-error").classList.add("hidden");
  }

  async function request(path, options = {}) {
    const response = await fetch(`${api}${path}`, {
      cache: "no-store", signal: AbortSignal.timeout(120000),
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}). Please try again.`);
    }
    return body;
  }

  function personName(person) { return person?.name || `Person #${person?.id}`; }
  function ownerName(chunk) { return chunk.personName || `Person #${chunk.personId}`; }
  function count(value, noun) { return `${(value || 0).toLocaleString()} ${noun}${value === 1 ? "" : "s"}`; }
  function finite(value) { return typeof value === "number" && Number.isFinite(value); }

  function timestamp(value) {
    if (value === null || value === undefined || value === "") return "Date unknown";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function timecode(value) {
    if (!finite(value)) return "Unknown";
    const seconds = Math.max(0, value) / 1000;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;
  }

  function score(value, label = "Voice cosine similarity") {
    if (!finite(value)) return element("span", "muted", "No comparable embedding");
    const node = element("span", "similarity");
    node.title = `${label}: ${value.toFixed(4)}`;
    node.setAttribute("aria-label", `${label}: ${value.toFixed(3)}`);
    const meter = element("meter");
    meter.min = -1;
    meter.max = 1;
    meter.value = Math.max(-1, Math.min(1, value));
    meter.setAttribute("aria-hidden", "true");
    node.append(meter, document.createTextNode(value.toFixed(3)));
    return node;
  }

  function association(chunk, candidate = false) {
    if (chunk.personId != null) {
      const confirmed = chunk.personId === state.person?.id && $("#inspector").open;
      return element("span", `association ${confirmed ? "confirmed" : "assigned"}`, `${confirmed ? "Confirmed" : "Assigned"}: ${ownerName(chunk)}`);
    }
    return element("span", `association ${candidate ? "candidate" : ""}`, candidate ? "Candidate / unassigned" : "Unassigned");
  }

  function transcript(chunk) {
    const paragraph = element("p", "transcript");
    const text = chunk.text || "";
    if (!text) return element("p", "transcript muted", "No transcript available.");
    let cursor = 0;
    let matched = false;
    // Locate aligned words in the original transcript so punctuation and spacing survive.
    for (const word of chunk.words || []) {
      const spoken = String(word.word || "").trim();
      if (!spoken) continue;
      const position = text.indexOf(spoken, cursor);
      if (position < 0) continue;
      if (position > cursor) paragraph.append(document.createTextNode(text.slice(cursor, position)));
      const confidence = finite(word.confidence) ? word.confidence : null;
      const level = confidence === null ? "unknown" : confidence >= 0.9 ? "high" : confidence >= 0.6 ? "medium" : "low";
      const wordNode = element("mark", `word confidence-${level}`, text.slice(position, position + spoken.length));
      const description = confidence === null ? "Confidence unknown" : `${(confidence * 100).toFixed(1)}% transcription confidence`;
      wordNode.title = `${spoken}: ${description}${finite(word.startMs) ? `; ${timecode(word.startMs)}` : ""}${word.speakerLabel ? `; speaker ${word.speakerLabel}` : ""}`;
      wordNode.setAttribute("aria-label", `${spoken}, ${description}`);
      paragraph.append(wordNode);
      cursor = position + spoken.length;
      matched = true;
    }
    if (cursor < text.length) {
      const remainder = element("span", matched ? "" : "word confidence-unknown", text.slice(cursor));
      if (!matched) remainder.title = "Word confidence unavailable";
      paragraph.append(remainder);
    }
    return paragraph;
  }

  function metadata(chunk) {
    const details = element("details", "chunk-details");
    details.append(element("summary", "", "Metadata"));
    const list = element("dl");
    const values = [
      ["Recording", chunk.recordingId ?? "Unknown"], ["Recorded", timestamp(chunk.recordingTimestamp)],
      ["Chunk index", chunk.chunkIndex ?? "Unknown"], ["Language", chunk.language || "Unknown"],
      ["Time range", `${timecode(chunk.startMs)} - ${timecode(chunk.endMs)}`],
      ["Speech", count(chunk.wordCount, "word")], ["Voice embedding", chunk.hasVoiceEmbedding ? "Available" : "Unavailable"],
    ];
    for (const [name, value] of values) {
      const row = element("div");
      row.append(element("dt", "", name), element("dd", "", value));
      list.append(row);
    }
    details.append(list);
    return details;
  }

  function chunkContent(chunk, { candidate = false, inspect = true, search = false, framed = true } = {}) {
    const card = element("article", framed ? "chunk-card" : "chunk-content");
    card.dataset.chunkId = chunk.id;
    const top = element("div", "chunk-top");
    const identity = element("div", "chunk-identity");
    identity.append(element("span", "chunk-id", `Chunk #${chunk.id}`), element("span", "", timestamp(chunk.recordingTimestamp)), association(chunk, candidate));
    top.append(identity);
    if (finite(chunk.similarity)) top.append(score(chunk.similarity, search ? "Text cosine similarity" : "Voice cosine similarity"));
    card.append(top, transcript(chunk));
    const bottom = element("div", "chunk-bottom");
    bottom.append(metadata(chunk));
    const actions = element("div", "inline-actions");
    if (inspect) {
      const inspectButton = button("Inspect voice", "audio-lines", () => inspectChunk(chunk.id, { retainPerson: $("#inspector").open }));
      inspectButton.disabled = !chunk.hasVoiceEmbedding;
      if (!chunk.hasVoiceEmbedding) inspectButton.title = "No voice embedding is available for this chunk";
      actions.append(inspectButton);
      actions.append(button("Full conversation", "arrow-right", () => {
        $("#inspector").close();
        navigate("conversation", { id: chunk.recordingId, chunk: chunk.id, from: state.view });
      }));
    }
    if (candidate && state.person) {
      if (chunk.personId === state.person.id) {
        const unassign = button("Unassign", "unlink", () => assignChunk(chunk.id, null));
        unassign.dataset.mutation = "";
        unassign.disabled = state.busy;
        actions.append(unassign);
      } else {
        const assignment = button(chunk.personId == null ? "Assign" : "Reassign", "check", () => assignChunk(chunk.id, state.person.id), "compact");
        assignment.dataset.mutation = "";
        assignment.disabled = state.busy;
        assignment.title = chunk.personId == null ? `Assign to ${personName(state.person)}` : `Move from ${ownerName(chunk)} to ${personName(state.person)}`;
        actions.append(assignment);
      }
    }
    const remove = button("", "trash-2", () => confirmDeletion(chunk), "icon-button secondary danger-icon");
    remove.title = `Delete chunk #${chunk.id}`;
    remove.setAttribute("aria-label", remove.title);
    remove.dataset.mutation = "";
    remove.disabled = state.busy;
    actions.append(remove);
    bottom.append(actions);
    card.append(bottom);
    return card;
  }

  function renderPagination(selector, page, callback) {
    const container = $(selector);
    container.replaceChildren();
    if (!page.total) return;
    const previous = button("", "arrow-right", () => callback(Math.max(0, page.offset - page.limit)), "icon-button secondary previous");
    previous.title = "Previous page";
    previous.setAttribute("aria-label", "Previous page");
    previous.disabled = page.offset === 0;
    const next = button("", "arrow-right", () => callback(page.offset + page.limit), "icon-button secondary");
    next.title = "Next page";
    next.setAttribute("aria-label", "Next page");
    next.disabled = page.offset + page.limit >= page.total;
    container.append(element("span", "", `${page.offset + 1}-${Math.min(page.offset + page.items.length, page.total)} of ${page.total.toLocaleString()}`), previous, next);
  }

  async function loadSearch(offset = 0) {
    const version = ++epochs.search;
    state.searchOffset = offset;
    clearError(false);
    $("#search-results").setAttribute("aria-busy", "true");
    $("#search-summary").textContent = "Searching conversations...";
    $("#search-submit").disabled = true;
    try {
      const params = new URLSearchParams({ q: state.searchQuery, mode: state.searchMode, assignment: state.searchAssignment, offset, limit: pageSize });
      const page = await request(`/conversations?${params}`);
      if (version !== epochs.search) return;
      if (page.total && offset >= page.total) return await loadSearch(Math.floor((page.total - 1) / pageSize) * pageSize);
      $("#search-summary").textContent = `${count(page.total, "conversation")}${state.searchQuery ? " found" : " in your archive"}`;
      $("#search-results").replaceChildren(...page.items.map(item => conversationRow(item, true)));
      if (!page.items.length) $("#search-results").append(element("p", "empty-state", "No matching conversations."));
      renderPagination("#search-pagination", page, offset => navigate("search", { offset }));
    } catch (error) {
      if (version !== epochs.search) return;
      $("#search-summary").textContent = "Conversation search unavailable";
      $("#search-results").replaceChildren();
      $("#search-pagination").replaceChildren();
      showError(error, false);
    } finally {
      if (version === epochs.search) {
        $("#search-results").setAttribute("aria-busy", "false");
        $("#search-submit").disabled = false;
      }
    }
  }

  function conversationRow(item, searched = false) {
    const row = button("", null, () => navigate("conversation", {
      id: item.id, from: searched ? "search" : "conversations",
    }), "conversation-row");
    row.dataset.recordingId = item.id;
    const content = element("span", "conversation-description");
    content.append(element("strong", "", `Conversation - ${timestamp(item.timestamp)}`));
    content.append(element("span", "muted", `${count(item.chunkCount, "segment")} / ${count(item.wordCount, "word")} / ${timecode(item.durationMs)}${item.language ? ` / ${item.language}` : ""}`));
    content.append(element("span", "conversation-preview", item.preview || "No transcript available."));
    if (searched && state.searchQuery) {
      content.append(element("span", "match-count", state.searchMode === "conversation" ? "Conversation theme match" : count(item.matchedChunkIds.length, "matching segment")));
      if (finite(item.similarity)) content.append(score(item.similarity, "Text cosine similarity"));
    }
    row.append(content, icon("arrow-right"));
    return row;
  }

  async function loadConversations(offset = 0) {
    const version = ++epochs.conversations;
    state.conversationsOffset = offset;
    $("#conversations-results").setAttribute("aria-busy", "true");
    $("#conversations-summary").textContent = "Loading conversations...";
    clearError(false);
    try {
      const page = await request(`/conversations?limit=${pageSize}&offset=${offset}`);
      if (version !== epochs.conversations) return;
      if (page.total && offset >= page.total) return await loadConversations(Math.floor((page.total - 1) / pageSize) * pageSize);
      $("#conversations-summary").textContent = count(page.total, "conversation");
      $("#conversations-results").replaceChildren(...page.items.map(item => conversationRow(item)));
      if (!page.items.length) $("#conversations-results").append(element("p", "empty-state", "No conversations yet."));
      renderPagination("#conversations-pagination", page, offset => navigate("conversations", { offset }));
    } catch (error) {
      if (version !== epochs.conversations) return;
      $("#conversations-summary").textContent = "Conversations unavailable";
      $("#conversations-results").replaceChildren();
      $("#conversations-pagination").replaceChildren();
      showError(error, false);
    } finally {
      if (version === epochs.conversations) $("#conversations-results").setAttribute("aria-busy", "false");
    }
  }

  function dialogueChunk(chunk) {
    const node = element("article", `dialogue-segment${chunk.matched ? " matched" : ""}`);
    node.id = `segment-${chunk.id}`;
    node.dataset.chunkId = chunk.id;
    node.tabIndex = -1;
    const top = element("div", "dialogue-speaker");
    const speaker = button(chunk.personId == null ? "Assign speaker" : ownerName(chunk), "users", () => openSpeakerPicker(chunk), "secondary compact");
    speaker.dataset.mutation = "";
    speaker.disabled = state.busy;
    top.append(speaker, element("span", "muted", `${timecode(chunk.startMs)} - ${timecode(chunk.endMs)}`));
    if (chunk.matched) top.append(element("span", "match-count", "Search match"));
    const body = element("p", "transcript", chunk.text || "No transcript available.");
    if (chunk.matched && state.context.mode === "text" && state.context.q) {
      body.replaceChildren();
      const source = chunk.text || "";
      const query = state.context.q.toLocaleLowerCase();
      let start = 0;
      let index;
      while ((index = source.toLocaleLowerCase().indexOf(query, start)) !== -1) {
        body.append(document.createTextNode(source.slice(start, index)), element("mark", "text-match", source.slice(index, index + query.length)));
        start = index + query.length;
      }
      body.append(document.createTextNode(source.slice(start)));
    }
    const actions = element("div", "inline-actions");
    const inspect = button("Inspect voice", "audio-lines", () => inspectChunk(chunk.id));
    inspect.dataset.mutation = "";
    inspect.dataset.unavailable = String(!chunk.hasVoiceEmbedding);
    inspect.disabled = !chunk.hasVoiceEmbedding || state.busy;
    const remove = button("", "trash-2", () => confirmDeletion(chunk), "icon-button secondary danger-icon");
    remove.title = `Delete chunk #${chunk.id}`;
    remove.setAttribute("aria-label", remove.title);
    remove.dataset.mutation = "";
    remove.disabled = state.busy;
    actions.append(inspect, remove);
    node.append(top, body, actions);
    return node;
  }

  async function loadConversation(id, focusId = null, scroll = true) {
    const version = ++epochs.conversation;
    state.conversationId = id;
    $("#conversation-chunks").setAttribute("aria-busy", "true");
    if (scroll) {
      state.conversation = null;
      $("#conversation-title").textContent = "Loading conversation...";
      $("#conversation-metadata").textContent = "";
      $("#conversation-chunks").replaceChildren();
      $("#match-navigation").classList.add("hidden");
    }
    try {
      const conversation = await request(`/conversations/${id}?${new URLSearchParams(state.context)}`);
      if (version !== epochs.conversation || state.view !== "conversation") return;
      state.conversation = conversation;
      $("#conversation-title").textContent = `Conversation - ${timestamp(conversation.timestamp)}`;
      $("#conversation-metadata").textContent = `${count(conversation.chunkCount, "segment")} / ${count(conversation.wordCount, "word")} / ${timecode(conversation.durationMs)} / ${count(conversation.personCount, "identified speaker")}`;
      $("#conversation-chunks").replaceChildren(...conversation.chunks.map(dialogueChunk));
      if (!conversation.chunks.length) $("#conversation-chunks").append(element("p", "empty-state", "This conversation has no remaining transcript segments."));
      state.matchIndex = Math.max(0, Math.min(state.matchIndex, conversation.matchedChunkIds.length - 1));
      renderMatches();
      if (scroll) {
        const targetId = focusId || conversation.matchedChunkIds[0];
        const target = targetId && $(`#segment-${targetId}`);
        if (target) {
          target.focus({ preventScroll: true });
          target.scrollIntoView({ block: "start" });
        } else {
          $("#conversation-title").focus({ preventScroll: true });
          window.scrollTo(0, 0);
        }
      }
    } catch (error) {
      if (version !== epochs.conversation) return;
      $("#conversation-title").textContent = "Conversation unavailable";
      showError(error, false);
    } finally {
      if (version === epochs.conversation) $("#conversation-chunks").setAttribute("aria-busy", "false");
    }
  }

  function renderMatches() {
    const ids = state.conversation?.matchedChunkIds || [];
    $("#match-navigation").classList.toggle("hidden", !state.context.q);
    $("#match-summary").textContent = ids.length ? `Match ${state.matchIndex + 1} of ${ids.length} for "${state.context.q}"` : `No matching segments for "${state.context.q}"${state.context.mode === "conversation" ? " / Conversation theme result" : ""}`;
    $("#previous-match").disabled = !ids.length || state.matchIndex === 0;
    $("#next-match").disabled = !ids.length || state.matchIndex === ids.length - 1;
  }

  function moveMatch(direction) {
    const ids = state.conversation?.matchedChunkIds || [];
    state.matchIndex = Math.max(0, Math.min(ids.length - 1, state.matchIndex + direction));
    const target = $(`#segment-${ids[state.matchIndex]}`);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "start" });
    renderMatches();
  }

  async function openSpeakerPicker(chunk) {
    if (state.busy) return;
    const version = ++epochs.speaker;
    state.speakerChunk = chunk;
    state.speakerPeople = [];
    clearError(false);
    $("#speaker-filter").value = "";
    $("#speaker-new-name").value = "";
    $("#speaker-title").textContent = `Speaker / ${timecode(chunk.startMs)}`;
    $("#speaker-sample").textContent = chunk.personId == null ? "Unassigned" : ownerName(chunk);
    $("#speaker-status").textContent = "Loading people...";
    $("#speaker-options").replaceChildren();
    $("#speaker-unassign").classList.toggle("hidden", chunk.personId == null);
    $("#speaker-picker").showModal();
    try {
      const result = await request(`/persons?chunk_id=${chunk.id}`);
      if (version !== epochs.speaker) return;
      state.speakerPeople = result.items;
      $("#speaker-status").textContent = chunk.hasVoiceEmbedding ? "Voice similarity / highest first" : "No voice comparison available";
      renderSpeakerOptions();
    } catch (error) {
      if (version === epochs.speaker) { $("#speaker-status").textContent = ""; showError(error); }
    }
  }

  function renderSpeakerOptions() {
    const filter = $("#speaker-filter").value.trim().toLocaleLowerCase();
    const people = state.speakerPeople.filter(person => personName(person).toLocaleLowerCase().includes(filter));
    $("#speaker-options").replaceChildren(...people.map(person => {
      const row = button("", null, async () => {
        const saved = await assignChunk(state.speakerChunk.id, person.id);
        if (saved) $("#speaker-picker").close();
      }, "comparison-person");
      row.dataset.personId = person.id;
      row.disabled = state.busy || person.id === state.speakerChunk.personId;
      row.append(element("strong", "", personName(person)), score(person.similarity));
      if (person.id === state.speakerChunk.personId) row.append(icon("check"));
      return row;
    }));
    if (!people.length) $("#speaker-options").append(element("p", "empty-state", "No matching people."));
  }

  function renderPeople() {
    $("#people-count").textContent = state.people.length;
    const filter = $("#person-filter").value.trim().toLocaleLowerCase();
    const people = [...state.people].filter(person => personName(person).toLocaleLowerCase().includes(filter)).sort((a, b) => personName(a).localeCompare(personName(b)));
    const rows = people.map(person => {
      const row = button("", null, () => inspectPerson(person.id), "person-row");
      const description = element("div");
      description.append(element("strong", "", personName(person)), element("span", "muted", `Person #${person.id} · ${count(person.chunkCount, "chunk")} · ${count(person.recordingCount, "recording")} · ${count(person.wordCount, "word")}`));
      row.append(description, element("span", "association", person.hasVoiceEmbedding ? "Voice profile" : "No voice samples"), icon("arrow-right"));
      return row;
    });
    $("#people-results").replaceChildren(...rows);
    if (!rows.length) $("#people-results").append(element("p", "empty-state", filter ? "No matching people." : "No people in this archive."));
    renderComparison();
  }

  function renderComparison() {
    const filter = $("#comparison-filter").value.trim().toLocaleLowerCase();
    $("#comparison-label").textContent = state.chunk ? "Chunk cosine score" : `${state.people.length} total`;
    const rows = state.people.filter(person => personName(person).toLocaleLowerCase().includes(filter)).map(person => {
      const row = button("", null, () => selectPerson(person.id), `comparison-person${state.person?.id === person.id ? " selected" : ""}`);
      row.setAttribute("aria-pressed", String(state.person?.id === person.id));
      row.dataset.personId = person.id;
      const description = element("span");
      description.append(element("strong", "", personName(person)), element("small", "", `#${person.id} · ${count(person.chunkCount, "chunk")}`));
      row.append(description);
      if (state.chunk) row.append(finite(person.similarity) ? score(person.similarity) : element("span", "muted", "n/a"));
      return row;
    });
    $("#comparison-people").replaceChildren(...rows);
    if (!rows.length) $("#comparison-people").append(element("p", "empty-state", filter ? "No matching people." : "No people yet."));
  }

  async function loadPeople() {
    const version = ++epochs.people;
    try {
      const result = await request(`/persons${state.chunk ? `?chunk_id=${state.chunk.id}` : ""}`);
      if (version !== epochs.people) return;
      state.people = result.items;
      if (state.person) state.person = state.people.find(person => person.id === state.person.id) || null;
      renderPeople();
      renderProfile();
    } catch (error) {
      if (version === epochs.people) showError(error);
    }
  }

  function renderSelectedChunk() {
    $("#selected-chunk-section").classList.toggle("hidden", !state.chunk);
    $("#selected-chunk").replaceChildren();
    if (state.chunk) {
      $("#selected-chunk-title").textContent = `Selected chunk #${state.chunk.id}`;
      $("#selected-chunk").append(chunkContent({ ...state.chunk, similarity: null }, { inspect: false, framed: false }));
    }
    $("#create-person-button").disabled = state.busy || !state.chunk;
    $("#new-person-name").disabled = !state.chunk;
    $("#create-person-unavailable").classList.toggle("hidden", !!state.chunk);
    updateSourceControls();
  }

  function renderProfile() {
    const person = state.person;
    $("#person-profile").classList.toggle("hidden", !person);
    $("#no-person").classList.toggle("hidden", !!person);
    if (!person) { updateSourceControls(); return; }
    if (document.activeElement !== $("#person-name")) $("#person-name").value = personName(person);
    $("#person-statistics").textContent = `Person #${person.id} · ${count(person.chunkCount, "confirmed chunk")} · ${count(person.recordingCount, "recording")} · ${count(person.wordCount, "word")}`;
    const chunk = state.chunk;
    $("#selected-assignment").classList.toggle("hidden", !chunk);
    if (chunk) {
      const confirmed = chunk.personId === person.id;
      $("#assignment-summary").textContent = confirmed ? `Chunk #${chunk.id} is confirmed for ${personName(person)}.` : chunk.personId == null ? `Chunk #${chunk.id} is unassigned.` : `Chunk #${chunk.id} is currently assigned to ${ownerName(chunk)}. Reassignment moves it to ${personName(person)}.`;
      $("#assign-selected span").textContent = confirmed ? "Confirmed association" : chunk.personId == null ? `Assign to ${personName(person)}` : `Reassign to ${personName(person)}`;
      $("#assign-selected").disabled = state.busy || confirmed;
      $("#unassign-selected").classList.toggle("hidden", chunk.personId == null);
      $("#unassign-selected").disabled = state.busy;
    }
    $("#save-person-name").disabled = state.busy;
    updateSourceControls();
  }

  function updateSourceControls() {
    const select = $("#similarity-source");
    select.options[0].disabled = !state.chunk?.hasVoiceEmbedding;
    select.options[1].disabled = !state.person?.hasVoiceEmbedding;
    if (state.source === "chunk" && !state.chunk?.hasVoiceEmbedding && state.person?.hasVoiceEmbedding) state.source = "person";
    if (state.source === "person" && !state.person?.hasVoiceEmbedding && state.chunk?.hasVoiceEmbedding) state.source = "chunk";
    select.value = state.source;
  }

  async function selectPerson(id) {
    const version = ++epochs.person;
    clearError(true);
    state.person = state.people.find(person => person.id === id) || null;
    state.known = [];
    state.knownChunkId = null;
    $("#known-chunk").replaceChildren();
    renderComparison();
    renderProfile();
    try {
      if (!state.person) {
        const person = await request(`/persons/${id}`);
        if (version !== epochs.person) return;
        state.person = person;
        renderProfile();
      }
      await loadKnown(0);
      if (version === epochs.person) await loadCandidates(0);
    } catch (error) {
      if (version === epochs.person) showError(error, true);
    }
  }

  function openInspector() {
    clearError(true);
    $("#inspector-status").textContent = "";
    $("#comparison-filter").value = "";
    if (!$("#inspector").open) $("#inspector").showModal();
  }

  async function inspectChunk(id, { retainPerson = false } = {}) {
    if (state.busy) return;
    const version = ++epochs.chunk;
    openInspector();
    $("#inspector-status").textContent = "Loading voice sample...";
    try {
      const chunk = await request(`/chunks/${id}`);
      if (version !== epochs.chunk) return;
      state.chunk = chunk;
      state.source = "chunk";
      if (!retainPerson) state.person = null;
      renderSelectedChunk();
      renderProfile();
      await loadPeople();
      if (version !== epochs.chunk) return;
      $("#inspector-status").textContent = "";
      if (!retainPerson && chunk.personId != null) await selectPerson(chunk.personId);
      else await loadCandidates(0);
    } catch (error) {
      if (version === epochs.chunk) {
        $("#inspector-status").textContent = "";
        showError(error, true);
      }
    }
  }

  async function inspectPerson(id) {
    if (state.busy) return;
    ++epochs.chunk;
    state.chunk = null;
    state.source = "person";
    openInspector();
    renderSelectedChunk();
    await loadPeople();
    await selectPerson(id);
  }

  async function loadKnown(offset = 0) {
    const version = ++epochs.known;
    const personId = state.person?.id;
    state.knownOffset = offset;
    $("#known-chunks").disabled = true;
    $("#known-chunks").replaceChildren(new Option("Loading confirmed chunks...", ""));
    $("#known-chunk").replaceChildren();
    $("#known-pagination").replaceChildren();
    if (personId == null) return;
    try {
      const page = await request(`/persons/${personId}/chunks?limit=${pageSize}&offset=${offset}`);
      if (version !== epochs.known || state.person?.id !== personId) return;
      if (page.total && offset >= page.total) return await loadKnown(Math.floor((page.total - 1) / pageSize) * pageSize);
      state.known = page.items;
      if (!state.known.some(chunk => chunk.id === state.knownChunkId)) state.knownChunkId = state.known[0]?.id ?? null;
      $("#known-chunks").replaceChildren(...page.items.map(chunk => new Option(`#${chunk.id} · ${timestamp(chunk.recordingTimestamp)} · ${chunk.text || "No transcript"}`, String(chunk.id))));
      if (!page.items.length) $("#known-chunks").append(new Option("No confirmed chunks", ""));
      else $("#known-chunks").value = String(state.knownChunkId);
      $("#known-chunks").disabled = !page.items.length;
      renderKnownChunk();
      renderPagination("#known-pagination", page, loadKnown);
    } catch (error) {
      if (version !== epochs.known || state.person?.id !== personId) return;
      $("#known-chunks").replaceChildren(new Option("Confirmed chunks unavailable", ""));
      showError(error, true);
    }
  }

  function renderKnownChunk() {
    const chunk = state.known.find(item => item.id === state.knownChunkId);
    $("#known-chunk").replaceChildren();
    if (chunk) $("#known-chunk").append(chunkContent(chunk, { framed: false }));
  }

  async function loadCandidates(offset = 0) {
    const version = ++epochs.candidates;
    state.candidateOffset = offset;
    updateSourceControls();
    const source = state.source === "chunk" ? state.chunk : state.person;
    $("#candidate-results").replaceChildren();
    $("#candidate-pagination").replaceChildren();
    if (!source?.hasVoiceEmbedding) {
      $("#candidate-summary").textContent = "No voice embedding selected.";
      return;
    }
    $("#candidate-summary").textContent = "Comparing voice embeddings...";
    $("#candidate-results").setAttribute("aria-busy", "true");
    try {
      const params = new URLSearchParams({ [state.source === "chunk" ? "chunk_id" : "person_id"]: source.id, scope: state.scope, limit: pageSize, offset });
      const page = await request(`/similar?${params}`);
      if (version !== epochs.candidates) return;
      if (page.total && offset >= page.total) return await loadCandidates(Math.floor((page.total - 1) / pageSize) * pageSize);
      $("#candidate-summary").textContent = `${count(page.total, "candidate")} · highest similarity first`;
      $("#candidate-results").replaceChildren(...page.items.map(chunk => chunkContent(chunk, { candidate: true })));
      if (!page.items.length) $("#candidate-results").append(element("p", "empty-state", "No comparable voice samples in this selection."));
      renderPagination("#candidate-pagination", page, loadCandidates);
    } catch (error) {
      if (version !== epochs.candidates) return;
      $("#candidate-summary").textContent = "Voice comparison unavailable";
      showError(error, true);
    } finally {
      if (version === epochs.candidates) $("#candidate-results").setAttribute("aria-busy", "false");
    }
  }

  async function refreshAfterMutation() {
    if (state.chunk) {
      ++epochs.chunk;
      state.chunk = await request(`/chunks/${state.chunk.id}`);
    }
    await loadPeople();
    renderSelectedChunk();
    renderProfile();
    if ($("#inspector").open) {
      await loadKnown(state.knownOffset);
      await loadCandidates(state.candidateOffset);
    }
    if (state.view === "search") await loadSearch(state.searchOffset);
    if (state.view === "conversations") await loadConversations(state.conversationsOffset);
    if (state.view === "conversation" && state.conversationId) await loadConversation(state.conversationId, null, false);
  }

  async function mutate(callback, message) {
    if (state.busy) return;
    state.busy = true;
    const inInspector = $("#inspector").open;
    const status = $($("#speaker-picker").open ? "#speaker-status" : inInspector ? "#inspector-status" : "#page-status");
    for (const key of Object.keys(epochs)) ++epochs[key];
    clearError(inInspector);
    status.textContent = "Saving changes...";
    document.querySelectorAll("button, input, select").forEach(node => {
      if (node.id !== "close-inspector") {
        node.dataset.preMutationDisabled = String(node.disabled);
        node.disabled = true;
      }
    });
    let saved = false;
    try {
      await callback();
      saved = true;
      await refreshAfterMutation();
      status.textContent = message;
    } catch (error) {
      status.textContent = saved ? "Changes saved. Some archive data could not be refreshed." : "";
      showError(error, inInspector);
    } finally {
      state.busy = false;
      document.querySelectorAll("[data-pre-mutation-disabled]").forEach(node => {
        node.disabled = node.dataset.preMutationDisabled === "true";
        delete node.dataset.preMutationDisabled;
      });
      renderSelectedChunk();
      renderProfile();
      renderComparison();
      document.querySelectorAll(".chunk-list [data-mutation], #known-chunk [data-mutation], .dialogue [data-mutation]").forEach(node => {
        node.disabled = node.dataset.unavailable === "true";
      });
      $("#search-submit").disabled = false;
    }
    return saved;
  }

  function confirmDeletion(chunk) {
    if (state.busy) return;
    pendingDeletion = chunk.id;
    $("#delete-title").textContent = `Delete chunk #${chunk.id}?`;
    $("#delete-description").textContent = "This permanently removes the transcript, word timings, and embeddings for this chunk. Any assigned person's voice profile will be recalculated from their remaining chunks.";
    $("#delete-confirmation").returnValue = "cancel";
    $("#delete-confirmation").showModal();
  }

  $("#delete-confirmation").addEventListener("close", () => {
    const id = pendingDeletion;
    pendingDeletion = null;
    if ($("#delete-confirmation").returnValue !== "delete" || id == null) return;
    mutate(async () => {
      await request(`/chunks/${id}`, { method: "DELETE" });
      if (state.chunk?.id === id) {
        state.chunk = null;
        state.source = "person";
      }
      if (state.knownChunkId === id) state.knownChunkId = null;
      document.querySelectorAll(`[data-chunk-id="${id}"]`).forEach(node => node.remove());
    }, `Chunk #${id} deleted.`);
  });

  function assignChunk(chunkId, personId) {
    const name = [...state.people, ...state.speakerPeople].find(person => person.id === personId);
    return mutate(() => request(`/chunks/${chunkId}/person`, { method: "PUT", body: JSON.stringify({ personId }) }), personId == null ? `Chunk #${chunkId} is now unassigned.` : `Chunk #${chunkId} assigned to ${personName(name)}. Voice profile updated.`);
  }

  function setTab(tab) {
    state.view = tab;
    for (const name of ["conversations", "search", "people"]) {
      $(`#${name}-view`).classList.toggle("hidden", name !== tab);
      $(`#${name}-tab`).classList.toggle("active", name === tab);
      $(`#${name}-tab`).setAttribute("aria-pressed", String(name === tab));
    }
    $("#conversation-view").classList.toggle("hidden", tab !== "conversation");
  }

  function navigate(view, extra = {}) {
    if (state.busy) return;
    history.replaceState({ ...history.state, scrollY: window.scrollY }, "");
    const route = new URLSearchParams({ view, q: state.searchQuery, mode: state.searchMode, assignment: state.searchAssignment, ...extra });
    history.pushState({ archiveNavigation: true }, "", `#${route}`);
    renderRoute();
  }

  async function renderRoute() {
    const route = new URLSearchParams(location.hash.slice(1));
    const view = ["search", "people", "conversation"].includes(route.get("view")) ? route.get("view") : "conversations";
    ++epochs.conversation;
    ++epochs.speaker;
    ++epochs.search;
    ++epochs.conversations;
    document.querySelectorAll("dialog[open]").forEach(dialog => { dialog.returnValue = "cancel"; dialog.close(); });
    clearError(false);
    $("#page-status").textContent = "";
    state.searchQuery = (route.get("q") || "").slice(0, 2000);
    state.searchMode = ["text", "conversation"].includes(route.get("mode")) ? route.get("mode") : "semantic";
    state.searchAssignment = ["assigned", "unassigned"].includes(route.get("assignment")) ? route.get("assignment") : "all";
    $("#search-query").value = state.searchQuery;
    $("#search-mode").value = state.searchMode;
    $("#search-assignment").value = state.searchAssignment;
    setTab(view);
    const offset = Math.max(0, Number(route.get("offset")) || 0);
    if (view === "conversation") {
      const id = Number(route.get("id"));
      if (!Number.isSafeInteger(id) || id <= 0) { showError(new Error("Invalid conversation link."), false); return; }
      state.context = route.get("from") === "search" ? { q: state.searchQuery, mode: state.searchMode, assignment: state.searchAssignment } : {};
      state.matchIndex = 0;
      $("#back-to-results span").textContent = route.get("from") === "search" ? "Search results" : "Conversations";
      await loadConversation(id, Number(route.get("chunk")) || null);
    } else {
      state.conversationId = null;
      if (view === "conversations") await loadConversations(offset);
      else if (view === "search") await loadSearch(offset);
      else await loadPeople();
      if (state.view === view) window.scrollTo(0, history.state?.scrollY || 0);
    }
  }

  window.voxvaultBack = () => {
    if (state.busy) return true;
    const dialogs = document.querySelectorAll("dialog[open]");
    if (dialogs.length) {
      const dialog = dialogs[dialogs.length - 1];
      dialog.returnValue = "cancel";
      dialog.close();
      return true;
    }
    if (state.view !== "conversation") return false;
    if (history.state?.archiveNavigation) history.back();
    else navigate(new URLSearchParams(location.hash.slice(1)).get("from") === "search" ? "search" : "conversations");
    return true;
  };
  window.addEventListener("popstate", renderRoute);
  $("#conversations-tab").addEventListener("click", () => navigate("conversations"));
  $("#search-tab").addEventListener("click", () => navigate("search"));
  $("#people-tab").addEventListener("click", () => navigate("people"));
  $("#refresh-conversations").addEventListener("click", () => loadConversations(state.conversationsOffset));
  $("#back-to-results").addEventListener("click", window.voxvaultBack);
  $("#previous-match").addEventListener("click", () => moveMatch(-1));
  $("#next-match").addEventListener("click", () => moveMatch(1));
  $("#close-speaker").addEventListener("click", () => $("#speaker-picker").close());
  $("#speaker-picker").addEventListener("close", () => { ++epochs.speaker; });
  $("#speaker-filter").addEventListener("input", renderSpeakerOptions);
  $("#speaker-unassign").addEventListener("click", async () => {
    if (await assignChunk(state.speakerChunk.id, null)) $("#speaker-picker").close();
  });
  $("#speaker-create-form").addEventListener("submit", async event => {
    event.preventDefault();
    const name = $("#speaker-new-name").value.trim();
    if (!name || !state.speakerChunk) return;
    const saved = await mutate(() => request("/persons", { method: "POST", body: JSON.stringify({ name, chunkId: state.speakerChunk.id }) }), `${name} created and assigned.`);
    if (saved) $("#speaker-picker").close();
  });
  $("#search-form").addEventListener("submit", event => {
    event.preventDefault();
    state.searchQuery = $("#search-query").value.trim();
    state.searchAssignment = $("#search-assignment").value;
    state.searchMode = $("#search-mode").value;
    navigate("search");
  });
  $("#search-assignment").addEventListener("change", () => $("#search-form").requestSubmit());
  $("#search-mode").addEventListener("change", () => $("#search-form").requestSubmit());
  $("#person-filter").addEventListener("input", renderPeople);
  $("#comparison-filter").addEventListener("input", renderComparison);
  $("#close-inspector").addEventListener("click", () => $("#inspector").close());
  $("#inspector").addEventListener("close", () => {
    if (!state.busy) {
      for (const key of ["chunk", "person", "known", "candidates"]) ++epochs[key];
    }
  });
  $("#clear-chunk").addEventListener("click", async () => {
    ++epochs.chunk;
    state.chunk = null;
    state.source = "person";
    renderSelectedChunk();
    renderProfile();
    await loadPeople();
    await loadCandidates(0);
  });
  $("#known-chunks").addEventListener("change", () => {
    state.knownChunkId = Number($("#known-chunks").value);
    renderKnownChunk();
  });
  $("#similarity-source").addEventListener("change", () => {
    state.source = $("#similarity-source").value;
    loadCandidates(0);
  });
  $("#candidate-scope").addEventListener("change", () => {
    state.scope = $("#candidate-scope").value;
    loadCandidates(0);
  });
  $("#assign-selected").addEventListener("click", () => {
    if (state.chunk && state.person) assignChunk(state.chunk.id, state.person.id);
  });
  $("#unassign-selected").addEventListener("click", () => {
    if (state.chunk) assignChunk(state.chunk.id, null);
  });
  $("#rename-person-form").addEventListener("submit", event => {
    event.preventDefault();
    const name = $("#person-name").value.trim();
    if (!name || !state.person) return;
    const id = state.person.id;
    mutate(async () => { state.person = await request(`/persons/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }); }, "Person name saved.");
  });
  $("#create-person-form").addEventListener("submit", event => {
    event.preventDefault();
    const name = $("#new-person-name").value.trim();
    if (!name || !state.chunk) return;
    const chunkId = state.chunk.id;
    mutate(async () => {
      const result = await request("/persons", { method: "POST", body: JSON.stringify({ name, chunkId }) });
      state.person = result.person;
      state.chunk = result.chunk;
      state.knownOffset = 0;
      state.candidateOffset = 0;
      $("#new-person-name").value = "";
    }, `${name} created and assigned to chunk #${chunkId}.`);
  });

  renderRoute();
  loadPeople();
})();
