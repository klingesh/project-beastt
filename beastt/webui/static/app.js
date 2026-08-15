/* JARVIS chat interface. Talks to the local Python server over /api. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    sidebar: $("sidebar"), chatList: $("chatList"), newChat: $("newChat"),
    messages: $("messages"), empty: $("empty"), composer: $("composer"),
    input: $("input"), send: $("send"), title: $("chatTitle"),
    del: $("deleteChat"), openSidebar: $("openSidebar"), closeSidebar: $("closeSidebar"),
    brandName: $("brandName"), greetName: $("greetName"), modelInfo: $("modelInfo"),
    attachBtn: $("attachBtn"), fileInput: $("fileInput"), attachments: $("attachments"),
    repoBtn: $("repoBtn"), repoLabel: $("repoLabel"), repoModal: $("repoModal"),
    repoList: $("repoList"), repoFilter: $("repoFilter"),
    repoCancel: $("repoCancel"), repoUnlink: $("repoUnlink"),
    chatFilter: $("chatFilter"), banner: $("banner"), toasts: $("toasts"),
    brainDot: $("brainDot"), confirmModal: $("confirmModal"),
    confirmTitle: $("confirmTitle"), confirmBody: $("confirmBody"),
    confirmOk: $("confirmOk"), confirmCancel: $("confirmCancel"),
    modelBtn: $("modelBtn"), modelLabel: $("modelLabel"), modelModal: $("modelModal"),
    modelList: $("modelList"), modelFilter: $("modelFilter"),
    modelCancel: $("modelCancel"), modelRefresh: $("modelRefresh"),
    modelDefault: $("modelDefault"), providerHelp: $("providerHelp"),
    modelError: $("modelError"),
  };

  let chatId = null;
  let busy = false;
  let name = "JARVIS";
  let repo = "";
  let attachments = [];
  let repos = [];
  let chatList = [];        // the sidebar listing, newest first
  let listFilter = "";
  let dismissConfirm = null;   // set while the confirm dialog is open
  let model = "";              // this chat's override; "" means use the default
  let resolved = "";           // the model actually answering
  let modelText = "Model";     // what the topbar pill shows
  let modelRows = [];          // catalogue from /api/models
  let providerRows = [];
  let defaultModel = "";

  // ---------- helpers ----------
  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    /* A 4xx body carries a human-readable {error} that the caller wants to
       show -- an unsupported file type, a model with no API key. Those are
       data, not exceptions. Throwing on them (as this used to) meant every
       carefully worded server message was replaced by a generic catch-all.
       Only server faults are genuinely unexpected. */
    if (response.status >= 500) {
      throw new Error(`${response.status}`);
    }
    return response.json();
  };

  const escapeHtml = (text) =>
    text.replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  /* Light formatting: fenced code, inline code, bold, and links. Deliberately
     minimal -- replies are plain text, and escaping happens first. */
  const format = (text) => {
    let out = escapeHtml(text);
    out = out.replace(/```(\w*)\n?([\s\S]*?)```/g,
      (_m, _lang, code) => `<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    /* Generated artwork, shown inline. The source is restricted to /api/art/
       plus a plain filename -- nothing else becomes an <img>. A reply is model
       output, so an unrestricted src here would let it point the browser
       anywhere or smuggle in an onerror handler. Must run before the link rule,
       which would otherwise eat the URL out of the middle. */
    out = out.replace(/!\[([^\]\n]*)\]\((\/api\/art\/[A-Za-z0-9._-]+)\)/g,
      (_m, alt, src) => `<img class="art" src="${src}" alt="${alt}" loading="lazy">`);
    out = out.replace(/(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener">$1</a>');
    return out;
  };

  const scrollDown = () => {
    el.messages.scrollTop = el.messages.scrollHeight;
  };

  // ---------- feedback ----------
  const toast = (text) => {
    const node = document.createElement("div");
    node.className = "toast";
    node.textContent = text;
    el.toasts.appendChild(node);
    setTimeout(() => {
      node.classList.add("out");
      setTimeout(() => node.remove(), 220);
    }, 2600);
  };

  const showBanner = (text, fix) => {
    el.banner.innerHTML = "";
    const label = document.createElement("span");
    label.textContent = fix ? `${text} — ${fix}` : text;
    const close = document.createElement("button");
    close.className = "banner-x";
    close.innerHTML = "&times;";
    close.title = "Dismiss";
    close.onclick = () => el.banner.classList.add("hidden");
    el.banner.append(label, close);
    el.banner.classList.remove("hidden");
  };

  /* This page is always current -- the server re-reads its Javascript, HTML and
     CSS from disk on every request. Its Python is not: that was loaded when the
     process started. So an update reaches the front end at once and the back end
     never, and the mismatch surfaces as errors describing code that is no longer
     on disk anywhere. One real example: a screenshot pasted with brand-new code
     and refused by the old uploader, which had never heard of images.

     Said once per page. Someone who dismissed it has been told. */
  let staleNoticed = false;
  const noteStaleCode = (code) => {
    if (!code || !code.stale || staleNoticed) return;
    staleNoticed = true;
    showBanner(code.detail, code.fix);
  };

  /* Asked on a timer, not only at load: the way anyone finds out about an update
     is by running update.py, and this window is usually already open when they
     do. Slow on purpose -- the server answers this from cached stats until a
     file actually moves, and nothing here is urgent to the second. */
  const watchForUpdates = () => setInterval(async () => {
    try {
      noteStaleCode((await api("/api/status")).code);
    } catch (_) { /* mid-restart, most likely -- which is the fix */ }
  }, 20000);

  /* Our own confirmation dialog rather than window.confirm, which can't be
     styled and, in some browsers, offers to suppress itself permanently --
     a bad outcome for an irreversible delete. Resolves true/false. */
  const confirmDialog = ({ title, body, confirmLabel = "Delete" }) =>
    new Promise((resolve) => {
      el.confirmTitle.textContent = title;
      el.confirmBody.textContent = body;
      el.confirmOk.textContent = confirmLabel;
      el.confirmModal.classList.remove("hidden");
      el.confirmOk.focus();

      const settle = (answer) => {
        el.confirmModal.classList.add("hidden");
        dismissConfirm = null;
        resolve(answer);
      };
      dismissConfirm = () => settle(false);
      el.confirmOk.onclick = () => settle(true);
      el.confirmCancel.onclick = () => settle(false);
      el.confirmModal.onclick = (event) => {
        if (event.target === el.confirmModal) settle(false);
      };
    });

  const thread = () => {
    let box = el.messages.querySelector(".thread");
    if (!box) {
      box = document.createElement("div");
      box.className = "thread";
      el.messages.appendChild(box);
    }
    return box;
  };

  const addMessage = (role, text) => {
    if (el.empty) el.empty.style.display = "none";
    const wrap = document.createElement("div");
    wrap.className = `msg ${role === "user" ? "user" : "bot"}`;
    wrap.innerHTML = `
      <div class="avatar">${role === "user" ? "You" : "J"}</div>
      <div class="bubble">
        <div class="who">${role === "user" ? "You" : escapeHtml(name)}</div>
        <div class="body">${format(text)}</div>
      </div>`;
    thread().appendChild(wrap);
    scrollDown();
    return wrap;
  };

  const addTyping = () => {
    const wrap = document.createElement("div");
    wrap.className = "msg bot";
    wrap.innerHTML = `
      <div class="avatar">J</div>
      <div class="bubble">
        <div class="who">${escapeHtml(name)}</div>
        <div class="body"><span class="typing"><i></i><i></i><i></i></span></div>
      </div>`;
    thread().appendChild(wrap);
    scrollDown();
    return wrap;
  };

  /* An assistant bubble that can be written into as text arrives. It carries a
     status line above the body for progress ("Searching the web for ..."),
     which is removed the moment real text starts. */
  const addStreamingMessage = () => {
    if (el.empty) el.empty.style.display = "none";
    const wrap = document.createElement("div");
    wrap.className = "msg bot streaming";
    wrap.innerHTML = `
      <div class="avatar">J</div>
      <div class="bubble">
        <div class="who">${escapeHtml(name)}</div>
        <div class="status">
          <span class="typing"><i></i><i></i><i></i></span>
          <span class="status-text"></span>
        </div>
        <div class="body"></div>
      </div>`;
    thread().appendChild(wrap);
    scrollDown();
    return {
      node: wrap,
      status: wrap.querySelector(".status"),
      statusText: wrap.querySelector(".status-text"),
      body: wrap.querySelector(".body"),
    };
  };

  /* Read a server-sent-event response, handing each decoded payload to
     `onEvent`. Frames are separated by a blank line; the tail of the buffer is
     kept because a chunk can split a frame down the middle. */
  const readEvents = async (response, onEvent) => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        try {
          onEvent(JSON.parse(line.slice(5).trim()));
        } catch (_) { /* one malformed frame shouldn't kill the stream */ }
      }
    }
  };

  // ---------- attachments ----------
  const renderAttachments = () => {
    el.attachments.innerHTML = "";
    attachments.forEach((file) => {
      const chipEl = document.createElement("span");
      chipEl.className = "attach-chip" + (file.kind === "image" ? " is-image" : "");
      // A thumbnail for a picture, because a filename is a poor way to confirm
      // you pasted the right screenshot. The src is built here from the chat id
      // and the server's own stored name, never from anything the file claimed
      // to be called.
      const thumb = file.kind === "image" && file.file && chatId
        ? `<img class="ac-thumb" alt="" src="/api/attachment/${encodeURIComponent(chatId)}/${encodeURIComponent(file.file)}">`
        : "";
      chipEl.innerHTML = thumb +
        `<span class="ac-text">` +
        `<span class="ac-name">${escapeHtml(file.name)}</span>` +
        `<span class="ac-note">${escapeHtml(file.note || "")}</span>` +
        `</span>` +
        `<button class="ac-x" title="Remove">&times;</button>`;
      chipEl.querySelector(".ac-x").onclick = async () => {
        if (!chatId) return;
        const data = await api(`/api/chats/${chatId}/detach`, {
          method: "POST", body: JSON.stringify({ name: file.name }),
        });
        attachments = data.attachments || [];
        renderAttachments();
      };
      el.attachments.appendChild(chipEl);
    });
  };

  const readAsBase64 = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });

  // A pasted screenshot arrives as a File with no useful name -- browsers call
  // it "image.png" every time, so three of them would look identical in the
  // list and each would replace the last on the server, which de-duplicates by
  // name. Stamping the time makes them distinct and tells you which is which.
  const namePasted = (file, index) => {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const ext = (file.type.split("/")[1] || "png").replace("jpeg", "jpg");
    const suffix = index ? `-${index + 1}` : "";
    return `pasted-${stamp}${suffix}.${ext}`;
  };

  const uploadFiles = async (files, { rename = false } = {}) => {
    files.forEach((file, i) => {
      if (rename || !file.name || file.name === "image.png") {
        // File.name is read-only, so carry the chosen name alongside it.
        file._asName = namePasted(file, i);
      }
    });
    for (const file of files) {
      const shownName = file._asName || file.name;
      const pending = document.createElement("span");
      pending.className = "attach-chip pending";
      pending.textContent = `Reading ${shownName}…`;
      el.attachments.appendChild(pending);
      try {
        const data = await api("/api/upload", {
          method: "POST",
          body: JSON.stringify({
            chat_id: chatId, name: shownName, data: await readAsBase64(file),
          }),
        });
        pending.remove();
        if (data.error) {
          addMessage("assistant", `I couldn't attach ${shownName}: ${data.error}`);
          continue;
        }
        chatId = data.chat_id;
        attachments = data.attachments || [];
        renderAttachments();
        const cut = data.truncated ? " (only the first part will be used)" : "";
        addMessage("assistant",
          `Attached **${data.attachment.name}** — ${data.attachment.note}${cut}. Ask me anything about it.`);
        refreshList();
      } catch (err) {
        pending.remove();
        addMessage("assistant", `Something went wrong attaching ${shownName}.`);
      }
    }
  };

  // ---------- repository link ----------
  const setRepoLabel = () => {
    el.repoLabel.textContent = repo || "Link repo";
    el.repoBtn.classList.toggle("linked", Boolean(repo));
  };

  const renderRepos = (filter = "") => {
    const term = filter.trim().toLowerCase();
    const shown = repos.filter((r) => !term || r.name.toLowerCase().includes(term));
    el.repoList.innerHTML = "";
    if (!shown.length) {
      el.repoList.innerHTML = `<div class="muted" style="padding:10px">No repositories found.</div>`;
      return;
    }
    shown.forEach((r) => {
      const row = document.createElement("div");
      row.className = "repo-item" + (r.name === repo ? " active" : "");
      row.innerHTML = `${escapeHtml(r.name)}${r.private ? ' <span class="muted">private</span>' : ""}`;
      row.onclick = () => linkRepo(r.name);
      el.repoList.appendChild(row);
    });
  };

  const linkRepo = async (chosen) => {
    if (!chatId) {
      // A repo needs a chat to belong to, so start one.
      const created = await api("/api/chats", { method: "POST" });
      chatId = created.id;
    }
    await api(`/api/chats/${chatId}/repo`, {
      method: "POST", body: JSON.stringify({ repo: chosen }),
    });
    repo = chosen;
    setRepoLabel();
    el.repoModal.classList.add("hidden");
    addMessage("assistant", chosen
      ? `Linked to **${chosen}**. I'll read from and push to that repo in this chat.`
      : "Unlinked. I'll use the default repo from your settings.");
    refreshList();
  };

  const openRepoPicker = async () => {
    el.repoModal.classList.remove("hidden");
    el.repoList.innerHTML = `<div class="muted" style="padding:10px">Loading…</div>`;
    const data = await api("/api/repos");
    if (data.error && !(data.repos || []).length) {
      el.repoList.innerHTML = `<div class="muted" style="padding:10px">${escapeHtml(data.error)}</div>`;
      return;
    }
    repos = data.repos || [];
    el.repoFilter.value = "";
    renderRepos();
    el.repoFilter.focus();
  };

  // ---------- model picker ----------
  /* Ids look like "provider:model", and Ollama's own names contain colons
     ("ollama:llama3.1:8b"), so only the first one separates the two parts. */
  const shortModel = (id) => {
    const cut = String(id || "").indexOf(":");
    return cut === -1 ? String(id || "") : id.slice(cut + 1);
  };

  const isCloud = (id) => {
    const row = modelRows.find((m) => m.id === id);
    if (row) return row.kind === "cloud";
    // Before the catalogue loads, the prefix is a good enough guess.
    return Boolean(id) && !String(id).startsWith("ollama:");
  };

  const setModelLabel = () => {
    el.modelLabel.textContent = modelText || "Model";
    // Highlighted only when this chat overrides the global default.
    el.modelBtn.classList.toggle("pinned", Boolean(model));
    const cloud = isCloud(resolved || model || defaultModel);
    el.modelBtn.classList.toggle("cloud", cloud);
    el.modelBtn.title = cloud
      ? `${modelText} — a cloud model, so your messages go to that provider`
      : `${modelText} — runs locally, nothing leaves this machine`;
  };

  const applyModel = (override, label) => {
    model = override || "";
    if (label) modelText = label;
    else modelText = shortModel(model || defaultModel) || "Model";
    setModelLabel();
  };

  const renderModels = () => {
    const term = el.modelFilter.value.trim().toLowerCase();
    const shown = term
      ? modelRows.filter((m) => m.model.toLowerCase().includes(term)
                             || m.provider_label.toLowerCase().includes(term))
      : modelRows;

    el.modelList.innerHTML = "";
    if (!shown.length) {
      const note = document.createElement("div");
      note.className = "muted";
      note.style.padding = "10px";
      note.textContent = modelRows.length
        ? "No models match that."
        : "No models available yet — see below.";
      el.modelList.appendChild(note);
      return;
    }

    const active = model || defaultModel;
    let heading = null;
    shown.forEach((row) => {
      if (row.provider_label !== heading) {
        heading = row.provider_label;
        const head = document.createElement("div");
        head.className = "model-group";
        const name = document.createElement("span");
        name.textContent = heading;
        const tag = document.createElement("span");
        tag.className = `tag ${row.kind}`;
        tag.textContent = row.kind;
        head.append(name, tag);
        el.modelList.appendChild(head);
      }

      const item = document.createElement("div");
      item.className = "model-item" + (row.id === active ? " active" : "");
      const name = document.createElement("span");
      name.className = "mi-name";
      name.textContent = row.model;
      item.appendChild(name);
      if (row.id === defaultModel) {
        const note = document.createElement("span");
        note.className = "mi-note muted";
        note.textContent = "default";
        item.appendChild(note);
      }
      item.onclick = () => chooseModel(row.id);
      el.modelList.appendChild(item);
    });
  };

  /* Providers that aren't ready are listed with the exact fix, because "no
     models available" on its own gives you nowhere to go. */
  const renderProviderHelp = () => {
    el.providerHelp.innerHTML = "";
    providerRows.filter((p) => p.status !== "ready").forEach((p) => {
      const row = document.createElement("div");
      row.className = "ph-row";
      const bits = [`<strong>${escapeHtml(p.label)}</strong> — ${escapeHtml(p.status)}`];
      if (!p.configured && p.signup) {
        bits.push(`<a href="${escapeHtml(p.signup)}" target="_blank" rel="noopener">get a key</a>`);
      }
      if (!p.configured && p.env_var) {
        bits.push(`then set <code>${escapeHtml(p.env_var)}</code> in .env`);
      }
      row.innerHTML = bits.join(" · ");
      el.providerHelp.appendChild(row);
    });
  };

  /* Shown inside the picker rather than as a toast: a refusal explains what to
     fix, and a message that disappears after two seconds is no help at all. */
  const showModelError = (text) => {
    el.modelError.textContent = text || "";
    el.modelError.classList.toggle("hidden", !text);
  };

  const chooseModel = async (id) => {
    showModelError("");
    if (!chatId) {
      // A model choice belongs to a conversation, so start one.
      const created = await api("/api/chats", { method: "POST" });
      chatId = created.id;
    }
    const data = await api(`/api/chats/${chatId}/model`, {
      method: "POST", body: JSON.stringify({ model: id }),
    });
    if (data.error) {
      showModelError(data.error);   // e.g. missing key, or model not pulled
      return;
    }
    resolved = data.model || defaultModel;
    applyModel(data.model, data.label);
    renderModels();
    el.modelModal.classList.add("hidden");
    toast(id ? `Now using ${modelText}` : `Back to the default — ${modelText}`);
    refreshList();
  };

  const loadModels = async (refresh = false) => {
    const note = document.createElement("div");
    note.className = "muted";
    note.style.padding = "10px";
    note.textContent = refresh ? "Refreshing…" : "Loading…";
    el.modelList.innerHTML = "";
    el.modelList.appendChild(note);

    const data = await api(`/api/models${refresh ? "?refresh=1" : ""}`);
    modelRows = data.models || [];
    providerRows = data.providers || [];
    defaultModel = data.default || defaultModel;
    renderModels();
    renderProviderHelp();
    setModelLabel();
  };

  const openModelPicker = async () => {
    el.modelModal.classList.remove("hidden");
    el.modelFilter.value = "";
    showModelError("");
    await loadModels();
    el.modelFilter.focus();
  };

  // ---------- chat list ----------
  /* Rename in place. A modal for a two-word edit loses your bearings, and the
     old prompt() couldn't even show you which chat you were renaming. */
  const inlineEdit = ({ anchor, value, className, onCommit }) => {
    if (!anchor.parentNode || anchor.parentNode.querySelector(`.${className}`)) return;
    const input = document.createElement("input");
    input.className = className;
    input.value = value;
    input.spellcheck = false;
    anchor.replaceWith(input);
    input.focus();
    input.select();

    let settled = false;
    const finish = async (commit) => {
      if (settled) return;          // blur fires again as we swap the node back
      settled = true;
      const next = input.value.trim();
      input.replaceWith(anchor);
      if (commit && next && next !== value) await onCommit(next);
    };

    input.onkeydown = (event) => {
      event.stopPropagation();      // Escape belongs to the edit, not a modal
      if (event.key === "Enter") { event.preventDefault(); finish(true); }
      if (event.key === "Escape") { event.preventDefault(); finish(false); }
    };
    input.onblur = () => finish(true);
    ["click", "dblclick"].forEach((kind) =>
      input.addEventListener(kind, (event) => event.stopPropagation()));
  };

  const renameChat = (chat, anchor, className) =>
    inlineEdit({
      anchor,
      value: chat.title,
      className,
      onCommit: async (title) => {
        await api(`/api/chats/${chat.id}/rename`, {
          method: "POST", body: JSON.stringify({ title }),
        });
        if (chat.id === chatId) el.title.textContent = title;
        toast(`Renamed to “${title}”`);
        refreshList();
      },
    });

  const deleteChat = async (chat) => {
    const count = chat.count || 0;
    const what = count
      ? `“${chat.title}” and its ${count} message${count === 1 ? "" : "s"}`
      : `“${chat.title}”`;
    const ok = await confirmDialog({
      title: "Delete this chat?",
      body: `${what} will be removed from this machine, along with any files ` +
            `attached to it. Anything you asked me to remember permanently ` +
            `stays in long-term memory.`,
    });
    if (!ok) return;

    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
    toast("Chat deleted");
    // If the thread that just went was the open one, fall back to a blank one.
    if (chat.id === chatId) startNew();
    else refreshList();
  };

  /* Date headings, the way a chat app does it. `updated` is epoch seconds. */
  const groupFor = (updated) => {
    const now = new Date();
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const day = 86400000;
    const at = (updated || 0) * 1000;
    if (at >= midnight) return "Today";
    if (at >= midnight - day) return "Yesterday";
    if (at >= midnight - 7 * day) return "Previous 7 days";
    if (at >= midnight - 30 * day) return "Previous 30 days";
    return "Older";
  };

  const chatRow = (chat) => {
    const row = document.createElement("div");
    row.className = "chat-item" + (chat.id === chatId ? " active" : "");
    row.title = `${chat.count} message${chat.count === 1 ? "" : "s"}`;

    const label = document.createElement("span");
    label.className = "ci-title";
    label.textContent = chat.title;

    const edit = document.createElement("button");
    edit.className = "ci-btn";
    edit.textContent = "✎";
    edit.title = "Rename";
    edit.onclick = (event) => {
      event.stopPropagation();
      renameChat(chat, label, "ci-input");
    };

    const remove = document.createElement("button");
    remove.className = "ci-btn danger";
    remove.textContent = "🗑";
    remove.title = "Delete";
    remove.onclick = (event) => {
      event.stopPropagation();
      deleteChat(chat);
    };

    const actions = document.createElement("span");
    actions.className = "ci-actions";
    actions.append(edit, remove);

    row.append(label, actions);
    row.onclick = () => openChat(chat.id);
    row.ondblclick = (event) => {
      event.preventDefault();
      renameChat(chat, label, "ci-input");
    };
    return row;
  };

  const renderList = () => {
    const term = listFilter.trim().toLowerCase();
    const shown = term
      ? chatList.filter((chat) => chat.title.toLowerCase().includes(term))
      : chatList;

    el.chatList.innerHTML = "";
    // Searching one chat is pointless, so the box only appears once it helps.
    el.chatFilter.classList.toggle("hidden", chatList.length < 2);

    if (!shown.length) {
      const note = document.createElement("div");
      note.className = "list-note muted";
      note.textContent = chatList.length ? "No chats match that." : "No chats yet";
      el.chatList.appendChild(note);
      return;
    }

    // The listing arrives newest first, so the headings fall in order.
    let heading = null;
    shown.forEach((chat) => {
      const group = groupFor(chat.updated);
      if (group !== heading) {
        heading = group;
        const head = document.createElement("div");
        head.className = "list-group";
        head.textContent = group;
        el.chatList.appendChild(head);
      }
      el.chatList.appendChild(chatRow(chat));
    });
  };

  const refreshList = async () => {
    try {
      const data = await api("/api/chats");
      chatList = data.chats || [];
      renderList();
    } catch (_) { /* the list is cosmetic; ignore failures */ }
  };

  // ---------- opening / creating ----------
  const clearThread = () => {
    el.messages.innerHTML = "";
    if (el.empty) {
      el.messages.appendChild(el.empty);
      el.empty.style.display = "";
    }
  };

  const openChat = async (id) => {
    const chat = await api(`/api/chats/${id}`);
    if (chat.error) return;
    chatId = chat.id;
    el.title.textContent = chat.title || "Chat";
    repo = chat.repo || "";
    attachments = chat.attachments || [];
    resolved = "";
    applyModel(chat.model || "");
    setRepoLabel();
    renderAttachments();
    clearThread();
    (chat.messages || []).forEach((m) => addMessage(m.role, m.content));
    if ((chat.messages || []).length && el.empty) el.empty.style.display = "none";
    refreshList();
    if (window.innerWidth <= 720) el.sidebar.classList.add("hidden");
  };

  const startNew = async () => {
    chatId = null;
    repo = "";
    attachments = [];
    resolved = "";
    applyModel("");
    el.title.textContent = "New chat";
    setRepoLabel();
    renderAttachments();
    clearThread();
    refreshList();
    el.input.focus();
  };

  // ---------- sending ----------
  /* Both endpoints report the same facts about the turn, so apply them once. */
  const applyTurnMeta = (data) => {
    if (data.chat_id) chatId = data.chat_id;
    if (data.title) el.title.textContent = data.title;
    if (data.repo !== undefined) { repo = data.repo; setRepoLabel(); }
    if (data.model) {
      // Reflect what actually answered, without implying a chat override.
      resolved = data.model;
      if (data.model_label) modelText = data.model_label;
      setModelLabel();
    }
    if (data.attachments) { attachments = data.attachments; renderAttachments(); }
  };

  /* The original blocking path, kept as a fallback for when streaming can't
     start -- an older browser, or a proxy that buffers the response. */
  const sendBlocking = async (text) => {
    const typing = addTyping();
    try {
      const data = await api("/api/message", {
        method: "POST",
        body: JSON.stringify({ chat_id: chatId, message: text }),
      });
      typing.remove();
      if (data.error) {
        addMessage("assistant", `Something went wrong: ${data.error}`);
        return;
      }
      applyTurnMeta(data);
      addMessage("assistant", data.reply);
      refreshList();
    } catch (err) {
      typing.remove();
      addMessage("assistant",
        "I couldn't reach the local server. Is the window running `python main.py --ui` still open?");
    }
  };

  const send = async (text) => {
    if (busy || !text.trim()) return;
    busy = true;
    el.send.disabled = true;
    addMessage("user", text);

    const view = addStreamingMessage();
    let full = "";
    let started = false;      // has any real text arrived?

    // Re-render the whole reply each time rather than appending: a code fence
    // or bold marker can span two chunks, and only the full text formats right.
    const paint = () => {
      view.body.innerHTML = format(full);
      scrollDown();
    };

    const beginText = () => {
      if (started) return;
      started = true;
      view.status.remove();
    };

    try {
      const response = await fetch("/api/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, message: text }),
      });
      if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);

      await readEvents(response, (event) => {
        if (event.type === "meta") {
          applyTurnMeta(event);
        } else if (event.type === "status") {
          view.statusText.textContent = event.text || "";
          scrollDown();
        } else if (event.type === "chunk") {
          beginText();
          full += event.text || "";
          paint();
        } else if (event.type === "done") {
          beginText();
          full = event.reply || full;
          paint();
        }
      });

      if (!started) throw new Error("empty stream");
      view.node.classList.remove("streaming");
      refreshList();
    } catch (err) {
      if (started) {
        // Something broke mid-answer. Keep what arrived -- retrying would
        // duplicate the reply, and a truncated answer is still useful.
        view.node.classList.remove("streaming");
        refreshList();
      } else {
        view.node.remove();
        await sendBlocking(text);
      }
    } finally {
      busy = false;
      el.send.disabled = false;
      el.input.focus();
    }
  };

  // ---------- events ----------
  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = el.input.value;
    el.input.value = "";
    el.input.style.height = "auto";
    send(text);
  });

  el.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      el.composer.requestSubmit();
    }
  });

  // Grow the box with the text, up to the CSS max-height.
  el.input.addEventListener("input", () => {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 180)}px`;
  });

  el.newChat.onclick = startNew;
  el.openSidebar.onclick = () => el.sidebar.classList.remove("hidden");
  el.closeSidebar.onclick = () => el.sidebar.classList.add("hidden");

  el.del.onclick = () => {
    if (!chatId) return startNew();
    const meta = chatList.find((chat) => chat.id === chatId)
      || { id: chatId, title: el.title.textContent, count: 0 };
    deleteChat(meta);
  };

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.onclick = () => send(chip.textContent);
  });

  // Rename by clicking the title in the header.
  el.title.onclick = () => {
    if (!chatId) return;
    const meta = chatList.find((chat) => chat.id === chatId)
      || { id: chatId, title: el.title.textContent };
    renameChat(meta, el.title, "title-input");
  };

  el.chatFilter.oninput = () => {
    listFilter = el.chatFilter.value;
    renderList();
  };

  // Attachments: button, file picker, and drag-and-drop.
  el.attachBtn.onclick = () => el.fileInput.click();
  el.fileInput.onchange = () => {
    if (el.fileInput.files.length) uploadFiles([...el.fileInput.files]);
    el.fileInput.value = "";
  };
  ["dragenter", "dragover"].forEach((event) =>
    document.addEventListener(event, (e) => {
      e.preventDefault();
      document.body.classList.add("dropping");
    }));
  ["dragleave", "drop"].forEach((event) =>
    document.addEventListener(event, (e) => {
      e.preventDefault();
      if (event === "dragleave" && e.relatedTarget) return;
      document.body.classList.remove("dropping");
    }));
  // Paste. A screenshot on the clipboard is the commonest thing anyone wants to
  // attach and was the one route that did not work -- the button and drag-and-drop
  // both existed, but Ctrl+V into the box did nothing at all.
  //
  // Only intercept when there are actually files on the clipboard: pasting text
  // must keep working normally, and clipboardData carries both, so checking the
  // wrong one would swallow every ordinary paste.
  document.addEventListener("paste", (e) => {
    const items = e.clipboardData ? [...(e.clipboardData.files || [])] : [];
    if (!items.length) return;
    e.preventDefault();
    uploadFiles(items, { rename: true });
  });

  document.addEventListener("drop", (e) => {
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) uploadFiles(files);
  });

  // Model picker.
  el.modelBtn.onclick = openModelPicker;
  el.modelCancel.onclick = () => el.modelModal.classList.add("hidden");
  el.modelRefresh.onclick = () => loadModels(true);
  el.modelDefault.onclick = () => chooseModel("");
  el.modelFilter.oninput = renderModels;
  el.modelModal.onclick = (event) => {
    if (event.target === el.modelModal) el.modelModal.classList.add("hidden");
  };

  // Repository picker.
  el.repoBtn.onclick = openRepoPicker;
  el.repoCancel.onclick = () => el.repoModal.classList.add("hidden");
  el.repoUnlink.onclick = () => linkRepo("");
  el.repoFilter.oninput = () => renderRepos(el.repoFilter.value);
  el.repoModal.onclick = (event) => {
    if (event.target === el.repoModal) el.repoModal.classList.add("hidden");
  };
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    // The confirmation sits on top of everything, so it gets first refusal.
    if (dismissConfirm) return dismissConfirm();
    el.repoModal.classList.add("hidden");
    el.modelModal.classList.add("hidden");
  });

  // ---------- startup ----------
  (async () => {
    try {
      const status = await api("/api/status");
      name = status.name || "JARVIS";
      el.brandName.textContent = name;
      document.title = name;
      el.greetName.textContent = status.user && status.user !== "friend"
        ? `, ${status.user}` : "";

      // Say plainly when there's no real model behind the replies.
      const brain = status.brain || {};
      defaultModel = status.default_model || "";
      applyModel("", brain.label);
      el.modelInfo.textContent = brain.detail || `${status.model} · local`;
      el.modelInfo.classList.toggle("warn", brain.ready === false);
      el.brainDot.classList.toggle("offline", brain.ready === false);
      if (brain.ready === false) showBanner(brain.detail, brain.fix);

      // Last, so it outranks the model warning: a server running code that is
      // no longer on disk explains symptoms nothing else will.
      noteStaleCode(status.code);
    } catch (_) { /* defaults are fine */ }
    // Outside the try: a first status that failed is no reason to stop asking.
    watchForUpdates();
    await refreshList();
    el.input.focus();
  })();
})();
