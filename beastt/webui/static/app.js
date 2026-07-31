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
  };

  let chatId = null;
  let busy = false;
  let name = "JARVIS";
  let repo = "";
  let attachments = [];
  let repos = [];

  // ---------- helpers ----------
  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok && response.status !== 404) {
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
    out = out.replace(/(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener">$1</a>');
    return out;
  };

  const scrollDown = () => {
    el.messages.scrollTop = el.messages.scrollHeight;
  };

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

  // ---------- attachments ----------
  const renderAttachments = () => {
    el.attachments.innerHTML = "";
    attachments.forEach((file) => {
      const chipEl = document.createElement("span");
      chipEl.className = "attach-chip";
      chipEl.innerHTML =
        `<span class="ac-name">${escapeHtml(file.name)}</span>` +
        `<span class="ac-note">${escapeHtml(file.note || "")}</span>` +
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

  const uploadFiles = async (files) => {
    for (const file of files) {
      const pending = document.createElement("span");
      pending.className = "attach-chip pending";
      pending.textContent = `Reading ${file.name}…`;
      el.attachments.appendChild(pending);
      try {
        const data = await api("/api/upload", {
          method: "POST",
          body: JSON.stringify({
            chat_id: chatId, name: file.name, data: await readAsBase64(file),
          }),
        });
        pending.remove();
        if (data.error) {
          addMessage("assistant", `I couldn't attach ${file.name}: ${data.error}`);
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
        addMessage("assistant", `Something went wrong attaching ${file.name}.`);
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

  // ---------- chat list ----------
  const renameChat = async (id, current) => {
    const title = prompt("Rename this chat:", current || "");
    if (title === null) return;
    await api(`/api/chats/${id}/rename`, {
      method: "POST", body: JSON.stringify({ title }),
    });
    if (id === chatId) el.title.textContent = title.trim() || "Untitled";
    refreshList();
  };

  const renderList = (items) => {
    el.chatList.innerHTML = "";
    items.forEach((chat) => {
      const row = document.createElement("div");
      row.className = "chat-item" + (chat.id === chatId ? " active" : "");
      row.title = `${chat.count} message(s) — double-click to rename`;
      const label = document.createElement("span");
      label.className = "ci-title";
      label.textContent = chat.title;
      const edit = document.createElement("button");
      edit.className = "ci-edit";
      edit.textContent = "✎";
      edit.title = "Rename";
      edit.onclick = (event) => {
        event.stopPropagation();
        renameChat(chat.id, chat.title);
      };
      row.append(label, edit);
      row.onclick = () => openChat(chat.id);
      row.ondblclick = (event) => {
        event.preventDefault();
        renameChat(chat.id, chat.title);
      };
      el.chatList.appendChild(row);
    });
    if (!items.length) {
      const note = document.createElement("div");
      note.className = "chat-item";
      note.textContent = "No chats yet";
      el.chatList.appendChild(note);
    }
  };

  const refreshList = async () => {
    try {
      const data = await api("/api/chats");
      renderList(data.chats || []);
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
    el.title.textContent = "New chat";
    setRepoLabel();
    renderAttachments();
    clearThread();
    refreshList();
    el.input.focus();
  };

  // ---------- sending ----------
  const send = async (text) => {
    if (busy || !text.trim()) return;
    busy = true;
    el.send.disabled = true;
    addMessage("user", text);
    const typing = addTyping();

    try {
      const data = await api("/api/message", {
        method: "POST",
        body: JSON.stringify({ chat_id: chatId, message: text }),
      });
      typing.remove();
      if (data.error) {
        addMessage("assistant", `Something went wrong: ${data.error}`);
      } else {
        chatId = data.chat_id;
        el.title.textContent = data.title || "Chat";
        if (data.repo !== undefined) { repo = data.repo; setRepoLabel(); }
        if (data.attachments) { attachments = data.attachments; renderAttachments(); }
        addMessage("assistant", data.reply);
        refreshList();
      }
    } catch (err) {
      typing.remove();
      addMessage("assistant",
        "I couldn't reach the local server. Is the window running `python main.py --ui` still open?");
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

  el.del.onclick = async () => {
    if (!chatId) return startNew();
    if (!confirm("Delete this chat?")) return;
    await api(`/api/chats/${chatId}`, { method: "DELETE" });
    startNew();
  };

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.onclick = () => send(chip.textContent);
  });

  // Rename by clicking the title.
  el.title.onclick = () => {
    if (chatId) renameChat(chatId, el.title.textContent);
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
  document.addEventListener("drop", (e) => {
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) uploadFiles(files);
  });

  // Repository picker.
  el.repoBtn.onclick = openRepoPicker;
  el.repoCancel.onclick = () => el.repoModal.classList.add("hidden");
  el.repoUnlink.onclick = () => linkRepo("");
  el.repoFilter.oninput = () => renderRepos(el.repoFilter.value);
  el.repoModal.onclick = (event) => {
    if (event.target === el.repoModal) el.repoModal.classList.add("hidden");
  };
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") el.repoModal.classList.add("hidden");
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
      el.modelInfo.textContent = `${status.model} · local`;
    } catch (_) { /* defaults are fine */ }
    await refreshList();
    el.input.focus();
  })();
})();
