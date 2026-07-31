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
  };

  let chatId = null;
  let busy = false;
  let name = "JARVIS";

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

  // ---------- chat list ----------
  const renderList = (items) => {
    el.chatList.innerHTML = "";
    items.forEach((chat) => {
      const row = document.createElement("div");
      row.className = "chat-item" + (chat.id === chatId ? " active" : "");
      row.textContent = chat.title;
      row.title = `${chat.count} message(s)`;
      row.onclick = () => openChat(chat.id);
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
    clearThread();
    (chat.messages || []).forEach((m) => addMessage(m.role, m.content));
    if ((chat.messages || []).length && el.empty) el.empty.style.display = "none";
    refreshList();
    if (window.innerWidth <= 720) el.sidebar.classList.add("hidden");
  };

  const startNew = async () => {
    chatId = null;
    el.title.textContent = "New chat";
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
