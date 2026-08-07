(() => {
  "use strict";

  const rows = [...document.querySelectorAll(".diff-row")];
  const body = document.querySelector(".diff-body");
  let lastRow = rows[0] || null;
  let view = document.body.dataset.defaultView || "context";

  /* ===== dual outline ===== */
  const outlineSelects = document.querySelectorAll(".outline-select");
  const headings = rows.filter((r) => r.classList.contains("heading-row"));

  headings.forEach((row) => {
    const cell = [...row.querySelectorAll(".cell-content")]
      .find((n) => n.textContent.trim() && !n.textContent.includes("<empty>"));
    if (!cell) return;
    const level = Number(row.dataset.headingLevel) || 2;
    const text = cell.textContent.trim();
    const indent = "\u00a0\u00a0".repeat(Math.max(0, level - 2));
    const dot = level > 2 ? "\u2022 " : "";
    const opt = document.createElement("option");
    opt.value = row.dataset.row;
    opt.textContent = indent + dot + text;
    outlineSelects.forEach((sel) => sel.append(opt.cloneNode(true)));
  });

  let outlinePaused = 0;
  let outlineTarget = null;

  function activeHeading() {
    const visible = headings.filter(
      (r) => !r.classList.contains("is-filtered") && r.getBoundingClientRect().top <= 80
    );
    return visible.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0] || headings[0];
  }

  function refreshOutline() {
    if (Date.now() < outlinePaused) return;
    if (outlineTarget) {
      const t = rows.find((r) => r.dataset.row === outlineTarget);
      if (t && Math.abs(t.getBoundingClientRect().top) < 10) {
        outlineSelects.forEach((s) => { s.value = outlineTarget; });
        outlineTarget = null;
        return;
      }
    }
    const cur = activeHeading();
    if (cur) outlineSelects.forEach((s) => { s.value = cur.dataset.row; });
  }

  const obs = new IntersectionObserver(refreshOutline, { rootMargin: "-18% 0px -70% 0px" });
  headings.forEach((r) => obs.observe(r));

  outlineSelects.forEach((sel) => {
    sel.addEventListener("change", () => {
      const t = rows.find((r) => r.dataset.row === sel.value);
      if (!t) return;
      outlineTarget = t.dataset.row;
      outlinePaused = Date.now() + 1200;
      const header = document.querySelector("header");
      const headerH = header ? header.getBoundingClientRect().height : 0;
      const top = t.getBoundingClientRect().top + window.scrollY - headerH - 8;
      window.scrollTo({ top, behavior: "smooth" });
      setTimeout(refreshOutline, 1200);
    });
  });

  /* ===== view switching ===== */
  function refreshVisibility(anchorRow, anchorTop) {
    for (const row of rows) {
      row.classList.remove("hidden-context");
      const unchanged = row.dataset.change === "unchanged";
      let visible = false;
      if (view === "all") visible = true;
      else if (view === "changes") visible = !unchanged;
      else if (view === "unchanged") visible = unchanged;
      else if (view === "context") visible = row.dataset.initialVisible === "true";
      row.classList.toggle("is-filtered", !visible);
    }
    if (anchorRow && anchorTop !== null) {
      window.scrollBy(0, anchorRow.getBoundingClientRect().top - anchorTop);
    }
    refreshOutline();
  }

  document.querySelectorAll("[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const anchor = activeHeading();
      const top = anchor ? anchor.getBoundingClientRect().top : null;
      view = btn.dataset.view;
      refreshVisibility(anchor, top);
    });
  });

  /* ===== collapse/expand ===== */
  function refreshRowAnnotations(row) {
    row.querySelectorAll(".annotation-toggle").forEach((toggle) => {
      const annIdStr = toggle.dataset.annId;
      const anchor = document.querySelector(`[data-annotation-id="${annIdStr}"]`);
      if (!anchor) return;
      const panel = row.querySelector(`.annotation-panel[data-ann-id="${annIdStr}"]`);
      if (row.classList.contains("is-collapsed")) {
        removeConnector(annIdStr);
      } else {
        drawConnector(annIdStr, toggle, panel, row, anchor);
      }
    });
  }

  function setCollapsed(row, collapsed) {
    if (row.classList.contains("heading-row")) return;
    row.classList.toggle("is-collapsed", collapsed);
    const toggle = row.querySelector(".row-toggle");
    if (toggle) {
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.textContent = collapsed ? "\u25b8" : "\u25be";
    }
    refreshRowAnnotations(row);
  }

  document.querySelector("#collapse-all").addEventListener("click", () =>
    rows.forEach((r) => setCollapsed(r, true))
  );
  document.querySelector("#expand-all").addEventListener("click", () =>
    rows.forEach((r) => setCollapsed(r, false))
  );

  /* ===== body click: row-toggle & note-button ===== */
  body.addEventListener("click", (event) => {
    const row = event.target.closest(".diff-row");
    if (row) lastRow = row;

    const toggle = event.target.closest(".row-toggle");
    if (toggle && row) {
      setCollapsed(row, !row.classList.contains("is-collapsed"));
      return;
    }

    const noteBtn = event.target.closest(".note-button");
    if (noteBtn && row) {
      addRowAnnotation(row);
      return;
    }
  });

  /* ===== annotation system ===== */
  let annId = 0;

  /* Get the line-no container for a given side ("old" or "new") */
  function getAnnotationsContainer(row, side) {
    const lineNo = row.querySelector(".line-no." + side);
    if (!lineNo) return null;
    let c = lineNo.querySelector(".row-annotations");
    if (!c) {
      c = document.createElement("div");
      c.className = "row-annotations";
      lineNo.appendChild(c);
    }
    return c;
  }

  /* Create floating panel (appended to the row, position:absolute) */
  function createPanel(value, annIdStr) {
    const panel = document.createElement("div");
    panel.className = "annotation-panel";
    panel.dataset.annId = annIdStr;
    const text = document.createElement("span");
    text.textContent = value;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-note";
    remove.setAttribute("aria-label", "Remove note");
    remove.textContent = "🗑";
    remove.addEventListener("click", (e) => {
      e.stopPropagation();
      panel.remove();
      removeConnector(annIdStr);
      document.querySelectorAll(`[data-ann-id="${annIdStr}"]`).forEach((el) => {
        if (el.classList.contains("annotation-toggle")) el.remove();
      });
      const anchor = document.querySelector(`[data-annotation-id="${annIdStr}"]`);
      if (anchor) anchor.replaceWith(...anchor.childNodes);
    });
    panel.append(text, remove);
    return panel;
  }

  function createToggle(annIdStr, annotationType) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = `annotation-toggle annotation-toggle--${annotationType}`;
    toggle.setAttribute("aria-label", annotationType === "row" ? "Expand row note" : "Expand text note");
    toggle.dataset.annId = annIdStr;
    toggle.dataset.annotationType = annotationType;
    toggle.textContent = annotationType === "row" ? "RN" : "BN";
    return toggle;
  }

  /* Position the panel relative to the row (absolute positioning) */
  function positionPanel(panel, toggle, row) {
    const rowRect = row.getBoundingClientRect();
    const tr = toggle.getBoundingClientRect();
    // Position to the right of the toggle, aligned with top of row
    // But absolute within the row's coordinate system
    const relLeft = tr.right - rowRect.left + 4;
    const relTop = (tr.top - rowRect.top) - 2;
    panel.style.position = "absolute";
    panel.style.left = relLeft + "px";
    panel.style.top = relTop + "px";
    panel.style.display = "flex";
    panel.style.maxWidth = Math.min(280, window.innerWidth * 0.38) + "px";
  }

  /* Route through the nearest text-line gap in row-local coordinates. */
  function drawConnector(annIdStr, toggle, panel, row, anchor) {
    removeConnector(annIdStr);
    if (row.classList.contains("is-collapsed")) return;

    const rowRect = row.getBoundingClientRect();
    const toggleRect = toggle.getBoundingClientRect();
    const anchorRect = anchor.getBoundingClientRect();
    const lineNoRect = toggle.closest(".line-no").getBoundingClientRect();
    const isAnchorLeftOfToggle = anchorRect.right <= toggleRect.left;
    const startX = (isAnchorLeftOfToggle ? toggleRect.left : toggleRect.right) - rowRect.left;
    const startY = toggleRect.top + toggleRect.height / 2 - rowRect.top;
    const endX = (isAnchorLeftOfToggle ? anchorRect.right : anchorRect.left) - rowRect.left;
    const endY = anchorRect.bottom - rowRect.top;
    const gutterX = (isAnchorLeftOfToggle ? lineNoRect.left : lineNoRect.right) - rowRect.left;
    const lineGapY = Math.min(rowRect.height - 2, anchorRect.bottom - rowRect.top + 2);
    const points = [
      [startX, startY],
      [gutterX, startY],
      [gutterX, lineGapY],
      [endX, lineGapY],
      [endX, endY]
    ];

    const namespace = "http" + "://www.w3.org/2000/svg";
    const svg = document.createElementNS(namespace, "svg");
    svg.classList.add("annotation-connector");
    svg.dataset.annId = annIdStr;
    svg.setAttribute("viewBox", `0 0 ${rowRect.width} ${rowRect.height}`);
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(namespace, "polyline");
    path.setAttribute("points", points.map((point) => point.join(",")).join(" "));
    svg.appendChild(path);
    row.appendChild(svg);
  }

  function removeConnector(annIdStr) {
    document.querySelectorAll(`.annotation-connector[data-ann-id="${annIdStr}"]`).forEach((el) => el.remove());
  }

  /* Determine which side the anchor is on: "old" or "new" */
  function anchorSide(anchorNode) {
    const cell = anchorNode.closest(".cell.old, .cell.new");
    if (cell) {
      return cell.classList.contains("old") ? "old" : "new";
    }
    return "old";
  }

  /* ===== addRowAnnotation ===== */
  function addRowAnnotation(row) {
    const value = window.prompt("Enter a note for this row:", "");
    if (!value) return;
    const id = ++annId;
    const annIdStr = "ann-" + id;
    const container = getAnnotationsContainer(row, "old");
    if (!container) return;
    const toggle = createToggle(annIdStr, "row");
    const panel = createPanel(value, annIdStr);
    setupToggleBehavior(toggle, panel, row, null);
    container.appendChild(toggle);
    row.appendChild(panel);
  }

  /* ===== addSelectionAnnotation ===== */
  function addSelectionAnnotation(row, anchorNode) {
    const value = window.prompt("Enter a note for the selected text:", "");
    if (!value) return;
    const id = ++annId;
    const annIdStr = "ann-" + id;
    anchorNode.dataset.annotationId = annIdStr;
    const side = anchorSide(anchorNode);
    const container = getAnnotationsContainer(row, side);
    if (!container) return;
    const toggle = createToggle(annIdStr, "selection");
    const panel = createPanel(value, annIdStr);
    setupToggleBehavior(toggle, panel, row, anchorNode);
    container.appendChild(toggle);
    row.appendChild(panel);
    drawConnector(annIdStr, toggle, panel, row, anchorNode);
  }

  function setupToggleBehavior(toggle, panel, row, anchor) {
    const annIdStr = toggle.dataset.annId;
    let hoverTimer = null;

    function show() {
      toggle.dataset.hovered = "true";
      positionPanel(panel, toggle, row);
    }
    function hide() {
      toggle.dataset.hovered = "false";
      panel.style.display = "none";
    }

    toggle.addEventListener("mouseenter", () => {
      if (toggle.dataset.expanded === "true") return;
      hoverTimer = setTimeout(show, 1000);
    });
    toggle.addEventListener("mouseleave", () => {
      if (hoverTimer) clearTimeout(hoverTimer);
      if (toggle.dataset.expanded === "true") return;
      hide();
    });
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const expanded = toggle.dataset.expanded === "true";
      toggle.dataset.expanded = String(!expanded);
      if (expanded) {
        hide();
      } else {
        show();
      }
    });
    panel.addEventListener("mouseenter", () => {
      if (hoverTimer) clearTimeout(hoverTimer);
    });
    panel.addEventListener("mouseleave", () => {
      if (toggle.dataset.expanded !== "true") {
        hide();
      }
    });
    window.addEventListener("resize", () => {
      if (anchor && !row.classList.contains("is-collapsed")) {
        if (toggle.dataset.expanded === "true" || toggle.dataset.hovered === "true") {
          positionPanel(panel, toggle, row);
        }
        drawConnector(annIdStr, toggle, panel, row, anchor);
      }
    });
  }

  /* ===== getSingleRowSelection: validate selection is within a single diff-row ===== */
  function getSingleRowSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    const ancestor = range.commonAncestorContainer.nodeType === Node.TEXT_NODE
      ? range.commonAncestorContainer.parentElement : range.commonAncestorContainer;
    if (!ancestor.closest?.(".diff-body")) return null;
    const startRow = ancestor.closest(".diff-row");
    if (!startRow) return null;
    const endContainer = range.endContainer.nodeType === Node.TEXT_NODE
      ? range.endContainer.parentElement : range.endContainer;
    const endRow = endContainer.closest?.(".diff-row");
    if (endRow && endRow !== startRow) return null;
    return { range, ancestor, row: startRow };
  }

  /* ===== toolbar buttons ===== */
  document.querySelector("#add-note").addEventListener("click", () => {
    const selInfo = getSingleRowSelection();
    if (!selInfo) return;

    const { range, row } = selInfo;
    const mark = document.createElement("mark");
    mark.className = "annotation-anchor";
    const contents = range.extractContents();
    mark.append(contents);
    range.insertNode(mark);
    addSelectionAnnotation(row, mark);
    window.getSelection().removeAllRanges();
  });

  document.querySelector("#highlight-selection").addEventListener("click", () => {
    const selInfo = getSingleRowSelection();
    if (!selInfo) return;

    const { range } = selInfo;
    const mark = document.createElement("mark");
    mark.className = "user-highlight";
    try {
      const contents = range.extractContents();
      mark.append(contents);
      range.insertNode(mark);
      window.getSelection().removeAllRanges();
    } catch (_) {
      alert("The selection spans too many structures. Select text within one block and try again.");
    }
  });

  document.querySelector("#clear-highlights").addEventListener("click", () => {
    document.querySelectorAll("mark.user-highlight").forEach((mark) => {
      mark.replaceWith(...mark.childNodes);
    });
  });

  document.querySelector("#clear-notes").addEventListener("click", () => {
    document.querySelectorAll(".annotation-toggle, .annotation-panel, .annotation-connector").forEach((el) => el.remove());
    document.querySelectorAll(".annotation-anchor").forEach((anchor) => {
      anchor.replaceWith(...anchor.childNodes);
    });
  });

  /* ===== save ===== */
  document.querySelector("#save-copy").addEventListener("click", () => {
    const clone = document.documentElement.cloneNode(true);
    clone.querySelectorAll(".annotation-connector").forEach((el) => el.remove());
    clone.querySelectorAll(".is-filtered").forEach((r) => r.classList.remove("is-filtered"));
    clone.querySelectorAll(".is-collapsed").forEach((r) => r.classList.remove("is-collapsed"));
    clone.querySelectorAll(".outline-select").forEach((s) => { s.value = ""; });
    const blob = new Blob(["<!doctype html>\n", clone.outerHTML], {
      type: "text/html;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = document.body.dataset.downloadName || "texdiff-annotated.html";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  /* ===== init ===== */
  refreshVisibility();
})();