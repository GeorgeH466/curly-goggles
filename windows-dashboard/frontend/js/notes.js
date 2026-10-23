/* ==========================================================================
   notes.js — markdown notes with autosave + the overview scratchpad
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, el, fmt, icon } = App;

  const Notes = (App.Notes = {});
  const SCRATCHPAD = "Scratchpad.md";

  let list = [];
  let openNotes = [];          // notes opened as tabs (newest last)
  let current = null;          // note name
  let dirty = false;
  let savingPromise = null;
  let saveTimer = null;
  let previewing = false;
  let scratchTimer = null;
  let initialised = false;

  /* ------------------------------------------------------------------ */
  /* Markdown                                                           */
  /* ------------------------------------------------------------------ */
  App.renderMarkdown = function renderMarkdown(text) {
    const source = String(text || "");
    let html;
    try {
      html = window.marked ? window.marked.parse(source, { breaks: true, gfm: true }) : null;
    } catch {
      html = null;
    }
    if (html == null) {
      return `<pre>${App.escape(source)}</pre>`;
    }
    if (window.DOMPurify) {
      return window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
    }
    return App.escape(source);
  };

  /* ------------------------------------------------------------------ */
  /* List + loading                                                     */
  /* ------------------------------------------------------------------ */
  function renderList() {
    const container = $("#note-list");
    if (!container) return;
    container.replaceChildren();
    if (!list.length) {
      container.append(el("p", { class: "muted small", text: "No notes yet" }));
      return;
    }
    list.forEach((note) => {
      container.append(el("div", {
        class: `note-item${note.name === current ? " is-active" : ""}`,
        title: note.name,
        onclick: () => openNote(note.name),
      }, [
        el("strong", { text: note.title || note.name.replace(/\.md$/i, "") }),
        el("small", { text: `${fmt.ago(note.modified)} · ${fmt.bytes(note.size)}` }),
      ]));
    });
  }

  async function loadList() {
    try {
      const data = await App.api.get("/api/notes");
      list = data.notes || [];
      App.state.notes = list;
      renderList();
    } catch (error) {
      App.showError("Could not load notes", error);
    }
  }

  function renderTabs() {
    const strip = $("#note-tabs");
    if (!strip) return;
    strip.replaceChildren();
    openNotes.forEach((name) => {
      const label = name.replace(/\.md$/i, "");
      const tab = el("div", {
        class: `note-tab${name === current ? " is-active" : ""}`,
        role: "tab",
        title: name,
        onclick: () => openNote(name),
      }, [
        el("span", { text: label }),
        openNotes.length > 1 ? el("button", {
          class: "note-tab-close",
          title: "Close tab",
          onclick: (event) => {
            event.stopPropagation();
            closeTab(name);
          },
        }, [el("span", { html: icon("x") })]) : null,
      ]);
      strip.append(tab);
    });
  }

  async function closeTab(name) {
    const index = openNotes.indexOf(name);
    if (index === -1) return;
    if (name === current && dirty) await saveNow();
    openNotes.splice(index, 1);
    if (name === current) {
      const next = openNotes[Math.min(index, openNotes.length - 1)];
      current = null;
      if (next) await openNote(next);
      else {
        if ($("#note-content")) $("#note-content").value = "";
        if ($("#note-title")) $("#note-title").value = "";
        updateSaved("saved");
        updateCounts();
        renderTabs();
      }
    } else {
      renderTabs();
    }
  }

  async function openNote(name) {
    if (dirty) await saveNow();
    try {
      const data = await App.api.get(`/api/notes/${encodeURIComponent(name)}`);
      current = data.name;
      if (!openNotes.includes(current)) openNotes.push(current);
      renderTabs();
      const title = $("#note-title");
      const content = $("#note-content");
      if (title) title.value = data.name.replace(/\.md$/i, "");
      if (content) content.value = data.content;
      dirty = false;
      updateSaved("saved");
      updateCounts();
      renderList();
      renderTabs();
      if (previewing) renderPreview();
      App.state.currentNote = current;
    } catch (error) {
      App.showError("Could not open note", error);
    }
  }

  function updateSaved(state) {
    const badge = $("#note-saved");
    if (!badge) return;
    const states = {
      saved: { text: "saved", cls: "badge badge-muted" },
      dirty: { text: "unsaved", cls: "badge badge-warn" },
      saving: { text: "saving…", cls: "badge" },
      error: { text: "save failed", cls: "badge badge-danger" },
    };
    const entry = states[state] || states.saved;
    badge.textContent = entry.text;
    badge.className = entry.cls;
  }

  function updateCounts() {
    const content = $("#note-content");
    const count = $("#note-count");
    if (!content || !count) return;
    const text = content.value;
    const words = text.trim() ? text.trim().split(/\s+/).length : 0;
    count.textContent = `${words} words · ${text.length} chars`;
  }

  /* ------------------------------------------------------------------ */
  /* Saving                                                             */
  /* ------------------------------------------------------------------ */
  function scheduleSave() {
    dirty = true;
    updateSaved("dirty");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 900);
  }

  async function saveNow() {
    if (!current || !dirty) return;
    const content = $("#note-content")?.value ?? "";
    if (savingPromise) {
      await savingPromise;
      return saveNow();
    }
    updateSaved("saving");
    savingPromise = App.api.put(`/api/notes/${encodeURIComponent(current)}`, { content })
      .then(() => {
        dirty = false;
        updateSaved("saved");
        loadList();
      })
      .catch((error) => {
        updateSaved("error");
        console.warn("note save failed", error);
      })
      .finally(() => {
        savingPromise = null;
      });
    return savingPromise;
  }

  Notes.create = async function create() {
    const name = await App.prompt({
      title: "New note",
      message: "Give your note a name. Markdown is supported.",
      placeholder: "Ideas",
      confirmText: "Create",
    });
    if (!name) return;
    try {
      const data = await App.api.post("/api/notes", { name, content: `# ${name}\n\n` });
      await loadList();
      await openNote(data.name);
      App.toast.success("Note created", data.name);
    } catch (error) {
      App.showError("Could not create note", error);
    }
  };

  Notes.open = openNote;
  Notes.loadList = loadList;
  Notes.focus = function focus() {
    if (!current && list.length) {
      const preferred = list.find((note) => note.name !== SCRATCHPAD) || list[0];
      openNote(preferred.name);
    } else if (!list.length) {
      renderList();
    }
  };

  /* ------------------------------------------------------------------ */
  /* Scratchpad widget                                                  */
  /* ------------------------------------------------------------------ */
  function scratchpadCount() {
    const area = $("#scratchpad");
    const counter = $("#scratchpad-count");
    if (!area || !counter) return;
    const words = area.value.trim() ? area.value.trim().split(/\s+/).length : 0;
    counter.textContent = `${words} words`;
  }

  async function loadScratchpad() {
    const area = $("#scratchpad");
    if (!area) return;
    try {
      const data = await App.api.get(`/api/notes/${encodeURIComponent(SCRATCHPAD)}`);
      area.value = data.content;
    } catch {
      // No scratchpad yet — the first save creates it, so nothing is written
      // to disk for users who never use the widget.
      area.value = "";
    }
    scratchpadCount();
  }

  function saveScratchpad() {
    const area = $("#scratchpad");
    const status = $("#scratchpad-status");
    if (!area) return;
    if (status) status.textContent = "saving…";
    App.api.put(`/api/notes/${encodeURIComponent(SCRATCHPAD)}`, { content: area.value })
      .then(() => {
        if (status) status.textContent = "saved";
        loadList();
      })
      .catch(() => {
        if (status) status.textContent = "save failed";
      });
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                             */
  /* ------------------------------------------------------------------ */
  document.addEventListener("DOMContentLoaded", () => {
    const content = $("#note-content");
    const title = $("#note-title");

    content?.addEventListener("input", () => {
      scheduleSave();
      updateCounts();
      if (previewing) renderPreview();
    });
    content?.addEventListener("keydown", (event) => {
      if (event.key === "Tab") {
        event.preventDefault();
        const start = content.selectionStart;
        content.setRangeText("  ", start, content.selectionEnd, "end");
        scheduleSave();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveNow();
        App.toast.success("Saved", current || "note");
      }
    });

    title?.addEventListener("change", async () => {
      if (!current) return;
      const next = `${title.value.trim() || "Untitled"}.md`;
      if (next === current) return;
      try {
        const result = await App.api.post(`/api/notes/${encodeURIComponent(current)}/rename`, { new_name: next });
        openNotes = openNotes.map((name) => (name === current ? result.name : name));
        current = result.name;
        App.state.currentNote = current;
        renderTabs();
        await loadList();
        renderList();
        App.toast.success("Renamed", result.name);
      } catch (error) {
        App.showError("Could not rename note", error);
        title.value = current.replace(/\.md$/i, "");
      }
    });

    $("#note-new")?.addEventListener("click", Notes.create);
    $("#note-preview-toggle")?.addEventListener("click", () => {
      previewing = !previewing;
      $("#notes-split")?.classList.toggle("previewing", previewing);
      const preview = $("#note-preview");
      if (preview) preview.hidden = !previewing;
      $("#note-preview-toggle")?.classList.toggle("is-active", previewing);
      if (previewing) renderPreview();
    });
    $("#note-rename")?.addEventListener("click", () => {
      const input = $("#note-title");
      if (input) { input.focus(); input.select(); }
    });
    $("#note-delete")?.addEventListener("click", async () => {
      if (!current) return;
      const ok = await App.confirm({
        title: "Delete this note?",
        message: `"${current}" will be permanently removed from the notes folder.`,
        confirmText: "Delete",
        danger: true,
      });
      if (!ok) return;
      try {
        await App.api.del(`/api/notes/${encodeURIComponent(current)}`);
        openNotes = openNotes.filter((name) => name !== current);
        current = null;
        dirty = false;
        renderTabs();
        if ($("#note-content")) $("#note-content").value = "";
        if ($("#note-title")) $("#note-title").value = "";
        await loadList();
        renderList();
        Notes.focus();
        App.toast.success("Note deleted");
      } catch (error) {
        App.showError("Could not delete note", error);
      }
    });

    const area = $("#scratchpad");
    area?.addEventListener("input", () => {
      scratchpadCount();
      const status = $("#scratchpad-status");
      if (status) status.textContent = "unsaved";
      clearTimeout(scratchTimer);
      scratchTimer = setTimeout(saveScratchpad, 800);
    });

    loadList().then(() => {
      if (App.state.view === "notes") Notes.focus();
    });
    loadScratchpad();
    initialised = true;

    // Keep the note list in sync when files change elsewhere.
    setInterval(() => {
      if (!document.hidden && !dirty) loadList();
    }, 30000);

    window.addEventListener("beforeunload", (event) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  });

  function renderPreview() {
    const preview = $("#note-preview");
    const content = $("#note-content");
    if (!preview || !content) return;
    preview.innerHTML = App.renderMarkdown(content.value);
  }

  App.on("config-saved", () => loadList());
})();
