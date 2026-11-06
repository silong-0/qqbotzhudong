(async function () {
  "use strict";
  const api = window.AstrBotPluginPage;
  if (api && typeof api.ready === "function") await api.ready();

  const state = { config: {}, sessions: [], dirty: false };

  const el = {
    sessionFilter: document.getElementById("sessionFilter"),
    sessionList: document.getElementById("sessionList"),
    selectAllBtn: document.getElementById("selectAllBtn"),
    selectNoneBtn: document.getElementById("selectNoneBtn"),
    newUmo: document.getElementById("newUmo"),
    openPickerBtn: document.getElementById("openPickerBtn"),
    pickerDialog: document.getElementById("sessionPicker"),
    pickerSearch: document.getElementById("pickerSearch"),
    pickerList: document.getElementById("pickerList"),
    pickerClose: document.getElementById("pickerClose"),
    addBtn: document.getElementById("addBtn"),
    saveBtn: document.getElementById("saveBtn"),
    saveState: document.getElementById("saveState"),
    enabledInput: document.getElementById("enabledInput"),
    minInput: document.getElementById("minInput"),
    maxInput: document.getElementById("maxInput"),
    startInput: document.getElementById("startInput"),
    endInput: document.getElementById("endInput"),
    contextInput: document.getElementById("contextInput"),
    contextLimitInput: document.getElementById("contextLimitInput"),
    memoryInput: document.getElementById("memoryInput"),
    memoryLimitInput: document.getElementById("memoryLimitInput"),
    maxCharsInput: document.getElementById("maxCharsInput"),
    promptInput: document.getElementById("promptInput"),
    selectedCount: document.getElementById("selectedCount"),
    selectedList: document.getElementById("selectedList"),
    testSelectedBtn: document.getElementById("testSelectedBtn"),
    status: document.getElementById("status"),
  };

  function setStatus(message, isError) {
    el.status.textContent = message || "";
    el.status.classList.toggle("error", Boolean(isError));
  }

  function markDirty() {
    state.dirty = true;
    el.saveState.textContent = "有未保存修改";
    el.saveState.classList.add("dirty");
  }

  function markClean() {
    state.dirty = false;
    el.saveState.textContent = "已保存";
    el.saveState.classList.remove("dirty");
  }

  function selectedSessions() {
    return state.sessions.filter((item) => item.selected);
  }

  function renderSessions() {
    const term = el.sessionFilter.value.trim().toLowerCase();
    const list = state.sessions.filter((item) => {
      if (!term) return true;
      return (
        String(item.display_name || "").toLowerCase().includes(term) ||
        item.umo.toLowerCase().includes(term) ||
        String(item.persona_id || "").toLowerCase().includes(term)
      );
    });
    el.sessionList.replaceChildren();
    if (!list.length) {
      const empty = document.createElement("div");
      empty.className = "meta";
      empty.style.padding = "18px";
      empty.textContent = "没有找到会话";
      el.sessionList.append(empty);
      return;
    }
    for (const session of list) {
      const row = document.createElement("label");
      row.className = "session-item" + (session.selected ? " selected" : "");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = Boolean(session.selected);
      check.addEventListener("change", () => {
        session.selected = check.checked;
        markDirty();
        renderAll();
      });
      const main = document.createElement("div");
      main.className = "session-main";
      const name = document.createElement("div");
      name.className = "session-name";
      name.textContent = session.display_name || session.umo;
      const sub = document.createElement("div");
      sub.className = "session-sub";
      const persona = session.persona_id ? ` · 人格 ${session.persona_id}` : "";
      const next = session.next_run ? ` · 下次 ${session.next_run}` : "";
      sub.textContent = `${session.kind}${persona}${next}`;
      main.append(name, sub);
      row.append(check, main);
      el.sessionList.append(row);
    }
  }

  function renderSelected() {
    const selected = selectedSessions();
    el.selectedCount.textContent = `已选 ${selected.length} 个会话`;
    el.testSelectedBtn.disabled = selected.length !== 1;
    el.selectedList.replaceChildren();
    if (!selected.length) {
      const empty = document.createElement("div");
      empty.className = "meta";
      empty.textContent = "还没有选择白名单会话，请在左侧勾选。";
      el.selectedList.append(empty);
      return;
    }
    for (const session of selected) {
      const row = document.createElement("div");
      row.className = "selected-item";
      const main = document.createElement("div");
      main.className = "selected-main";
      const name = document.createElement("div");
      name.className = "selected-name";
      name.textContent = session.display_name || session.umo;
      const meta = document.createElement("div");
      meta.className = "selected-meta";
      meta.textContent = `${session.umo}${session.persona_id ? ` · 人格 ${session.persona_id}` : ""}`;
      main.append(name, meta);
      const actions = document.createElement("div");
      actions.className = "selected-actions";
      const test = document.createElement("button");
      test.type = "button";
      test.className = "btn ghost small";
      test.textContent = "测试发送";
      test.addEventListener("click", () => testSession(session, test));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn ghost small";
      remove.textContent = "移除";
      remove.addEventListener("click", () => {
        session.selected = false;
        markDirty();
        renderAll();
      });
      actions.append(test, remove);
      row.append(main, actions);
      el.selectedList.append(row);
    }
  }

  function renderAll() {
    renderSessions();
    renderSelected();
  }

  function fillForm() {
    const c = state.config || {};
    el.enabledInput.checked = Boolean(c.enabled);
    el.minInput.value = c.min_minutes ?? 90;
    el.maxInput.value = c.max_minutes ?? 240;
    el.startInput.value = c.active_start || "09:00";
    el.endInput.value = c.active_end || "23:30";
    el.contextInput.checked = c.read_context !== false;
    el.contextLimitInput.value = c.context_limit ?? 8;
    el.memoryInput.checked = c.read_memory !== false;
    el.memoryLimitInput.value = c.memory_limit ?? 3;
    el.maxCharsInput.value = c.max_chars ?? 220;
    el.promptInput.value = c.prompt || "";
  }

  function currentConfig() {
    return {
      ...state.config,
      enabled: el.enabledInput.checked,
      min_minutes: Number(el.minInput.value) || 90,
      max_minutes: Number(el.maxInput.value) || 240,
      active_start: el.startInput.value || "09:00",
      active_end: el.endInput.value || "23:30",
      read_context: el.contextInput.checked,
      context_limit: Number(el.contextLimitInput.value) || 8,
      read_memory: el.memoryInput.checked,
      memory_limit: Number(el.memoryLimitInput.value) || 0,
      max_chars: Number(el.maxCharsInput.value) || 220,
      prompt: el.promptInput.value,
      whitelist: selectedSessions().map((item) => item.umo),
    };
  }

  function renderPicker() {
    const term = el.pickerSearch.value.trim().toLowerCase();
    const list = state.sessions.filter((item) => {
      if (!term) return true;
      return (
        String(item.display_name || "").toLowerCase().includes(term) ||
        item.umo.toLowerCase().includes(term) ||
        String(item.persona_id || "").toLowerCase().includes(term)
      );
    });
    el.pickerList.replaceChildren();
    if (!list.length) {
      const empty = document.createElement("div");
      empty.className = "picker-empty";
      empty.textContent = "没有找到会话。先在目标群里发一条消息，或手动输入 UMO。";
      el.pickerList.append(empty);
      return;
    }
    for (const session of list) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "picker-item" + (session.selected ? " selected" : "");
      const name = document.createElement("div");
      name.className = "picker-name";
      name.textContent = session.display_name || session.umo;
      const meta = document.createElement("div");
      meta.className = "picker-meta";
      const persona = session.persona_id ? ` · 人格 ${session.persona_id}` : "";
      meta.textContent = `${session.kind}${persona} · ${session.umo}`;
      row.append(name, meta);
      row.addEventListener("click", () => {
        session.selected = true;
        el.newUmo.value = session.umo;
        el.pickerDialog.close();
        markDirty();
        renderAll();
        setStatus(`已加入白名单：${session.display_name || session.umo}，记得保存。`);
      });
      el.pickerList.append(row);
    }
  }

  function openPicker() {
    renderPicker();
    if (typeof el.pickerDialog.showModal === "function") el.pickerDialog.showModal();
  }

  async function loadOverview() {
    setStatus("正在加载...");
    try {
      const data = await api.apiGet("overview");
      state.config = data?.config || {};
      state.sessions = Array.isArray(data?.sessions) ? data.sessions : [];
      fillForm();
      renderAll();
      markClean();
      setStatus("");
    } catch (error) {
      setStatus(`加载失败：${error.message || error}`, true);
    }
  }

  async function save() {
    el.saveBtn.disabled = true;
    setStatus("正在保存...");
    try {
      const data = await api.apiPost("save", { config: currentConfig() });
      state.config = data?.config || currentConfig();
      state.sessions = Array.isArray(data?.sessions) ? data.sessions : [];
      fillForm();
      renderAll();
      markClean();
      setStatus("已保存，新的间隔和时间段会立即生效。");
    } catch (error) {
      setStatus(`保存失败：${error.message || error}`, true);
    } finally {
      el.saveBtn.disabled = false;
    }
  }

  async function testSession(session, button) {
    if (button) button.disabled = true;
    setStatus(`正在为「${session.display_name || session.umo}」生成并发送...`);
    try {
      const data = await api.apiPost("test", { umo: session.umo });
      if (data?.ok) {
        setStatus(`已发送：${data.text || ""}`);
      } else {
        setStatus(`未发送：${data?.error || "模型没有生成内容"}`, true);
      }
    } catch (error) {
      setStatus(`测试失败：${error.message || error}`, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function addManual() {
    const umo = el.newUmo.value.trim();
    if (!umo) return;
    if (!umo.includes(":GroupMessage:") && !umo.includes(":FriendMessage:")) {
      setStatus("UMO 需要包含 :GroupMessage: 或 :FriendMessage:", true);
      return;
    }
    let session = state.sessions.find((item) => item.umo === umo);
    if (!session) {
      session = { umo, display_name: umo, kind: "手动", persona_id: "", selected: true, next_run: "" };
      state.sessions.push(session);
    }
    session.selected = true;
    el.newUmo.value = "";
    markDirty();
    renderAll();
    setStatus("已加入白名单，记得点保存。");
  }

  el.sessionFilter.addEventListener("input", renderSessions);
  el.openPickerBtn.addEventListener("click", openPicker);
  el.pickerSearch.addEventListener("input", renderPicker);
  el.pickerClose.addEventListener("click", () => el.pickerDialog.close());
  el.selectAllBtn.addEventListener("click", () => {
    state.sessions.forEach((item) => (item.selected = true));
    markDirty();
    renderAll();
  });
  el.selectNoneBtn.addEventListener("click", () => {
    state.sessions.forEach((item) => (item.selected = false));
    markDirty();
    renderAll();
  });
  el.addBtn.addEventListener("click", addManual);
  el.newUmo.addEventListener("keydown", (event) => {
    if (event.key === "Enter") addManual();
  });
  el.saveBtn.addEventListener("click", save);
  el.testSelectedBtn.addEventListener("click", () => {
    const selected = selectedSessions();
    if (selected.length === 1) testSession(selected[0], el.testSelectedBtn);
  });
  [
    el.enabledInput,
    el.minInput,
    el.maxInput,
    el.startInput,
    el.endInput,
    el.contextInput,
    el.contextLimitInput,
    el.memoryInput,
    el.memoryLimitInput,
    el.maxCharsInput,
    el.promptInput,
  ].forEach((node) => {
    node.addEventListener("input", markDirty);
    node.addEventListener("change", markDirty);
  });

  loadOverview();
})();
