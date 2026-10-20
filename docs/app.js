/* Between Visits ops dashboard. Reads the masked JSON snapshot in data/ (written by `python -m infra run`).
   All data is inserted with textContent; nothing from the JSON is interpreted as HTML. */
(function () {
  "use strict";

  const FILES = ["run", "summary", "contacts", "call_attempts", "scripts", "transcripts", "interviews",
    "suppressions", "gate_decisions", "errors", "dlq", "alerts", "runs", "audit_log", "brain_inbox"];
  const STATUS_ICON = { good: "✓", warning: "!", serious: "▲", critical: "✕", neutral: "·" };

  // ---------- tiny DOM helpers ----------
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    (children || []).forEach((c) => node.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function status(kind, label) {
    return el("span", { class: "status " + kind }, [el("span", { text: STATUS_ICON[kind] || "" }), label]);
  }
  function table(headers, rows, opts) {
    opts = opts || {};
    if (!rows.length) return el("div", { class: "empty", text: opts.empty || "Nothing yet." });
    const thead = el("thead", null, [el("tr", null, headers.map((h, i) =>
      el("th", { class: (opts.num || []).includes(i) ? "num" : "" }, [h])))]);
    const tbody = el("tbody", null, rows.map((r) => el("tr", null, r.map((cell, i) => {
      const td = el("td", { class: ((opts.num || []).includes(i) ? "num " : "") + ((opts.mono || []).includes(i) ? "mono " : "") + ((opts.nowrap || []).includes(i) ? "nowrap" : "") });
      if (cell instanceof Node) td.appendChild(cell);
      else td.textContent = cell == null ? "" : String(cell);
      return td;
    }))));
    return el("table", null, [thead, tbody]);
  }
  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  }
  function pct(x) { return x == null ? "n/a" : Math.round(x * 100) + "%"; }
  function nice(s) { return String(s || "").replace(/_/g, " ").toLowerCase(); }

  // ---------- tooltip ----------
  const tip = document.getElementById("tooltip");
  function showTip(evt, label, value, extra) {
    clear(tip);
    tip.appendChild(el("div", null, [el("span", { class: "k" }), el("b", { text: String(value) }), " ", el("span", { class: "muted", text: label })]));
    if (extra) tip.appendChild(el("div", { class: "muted small", text: extra }));
    tip.style.display = "block";
    moveTip(evt);
  }
  function moveTip(evt) {
    const x = evt.clientX != null ? evt.clientX : (evt.target.getBoundingClientRect().right);
    const y = evt.clientY != null ? evt.clientY : (evt.target.getBoundingClientRect().top);
    tip.style.left = Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = Math.max(8, y - tip.offsetHeight - 10) + "px";
  }
  function hideTip() { tip.style.display = "none"; }

  function bars(container, items, opts) {
    opts = opts || {};
    clear(container);
    const max = Math.max(1, ...items.map((i) => i.count));
    items.forEach((item, idx) => {
      container.appendChild(el("div", { class: "lbl", text: item.label, title: item.label }));
      const bar = el("div", { class: "bar", tabindex: "0", role: "img", "aria-label": item.label + ": " + item.count });
      bar.style.width = Math.max(0.4, (item.count / max) * 100) + "%";
      const over = (e) => showTip(e, item.label, item.count, item.extra);
      bar.addEventListener("pointerenter", over);
      bar.addEventListener("pointermove", moveTip);
      bar.addEventListener("pointerleave", hideTip);
      bar.addEventListener("focus", over);
      bar.addEventListener("blur", hideTip);
      container.appendChild(el("div", { class: "track" }, [bar, el("span", { class: "val", text: String(item.count) })]));
    });
    if (!items.length) container.appendChild(el("div", { class: "empty", text: "No data yet." }));
  }

  // ---------- theme ----------
  const themeBtn = document.getElementById("theme");
  const THEMES = ["system", "light", "dark"];
  function applyTheme(t) {
    if (t === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    themeBtn.textContent = "Theme: " + t;
    try { localStorage.setItem("bv-theme", t); } catch (e) { /* private mode */ }
  }
  let theme = "system";
  try { theme = localStorage.getItem("bv-theme") || "system"; } catch (e) { /* ignore */ }
  applyTheme(theme);
  themeBtn.addEventListener("click", () => { theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]; applyTheme(theme); });

  // ---------- load ----------
  async function loadAll() {
    const stamp = Date.now();
    const out = {};
    await Promise.all(FILES.map(async (name) => {
      try {
        const res = await fetch("data/" + name + ".json?t=" + stamp, { cache: "no-store" });
        out[name] = res.ok ? await res.json() : null;
      } catch (e) { out[name] = null; }
    }));
    return out;
  }

  function render(d) {
    const run = d.run || {}, s = d.summary || {};
    const contacts = d.contacts || [];
    const byId = Object.fromEntries(contacts.map((c) => [c.id, c]));
    const name = (cid) => { const c = byId[cid]; return c ? (c.full_name || "(no name)") + (c.external_id ? " · " + c.external_id : "") : cid; };
    const transcriptsById = Object.fromEntries((d.transcripts || []).map((t) => [t.id, t]));

    // header chips
    const chips = clear(document.getElementById("run-chips"));
    chips.appendChild(el("span", { class: "chip mode" }, [el("b", { text: (run.mode || "dry_run").toUpperCase().replace("_", " ") })]));
    chips.appendChild(el("span", { class: "chip" }, ["Last run ", el("b", { text: run.finished_at ? fmtTime(run.finished_at) : "never" })]));
    if (run.simulated_time) chips.appendChild(el("span", { class: "chip warning" }, [el("span", { class: "dot" }), "simulated time ", el("b", { text: run.now_used || "" })]));
    chips.appendChild(el("span", { class: "chip" }, ["Trigger ", el("b", { text: run.trigger || "–" })]));
    if (run.git_sha) chips.appendChild(el("span", { class: "chip" }, ["Commit ", el("a", { href: "https://github.com/UmaSanskriti/between-visits-workflow/commit/" + run.git_sha, text: run.git_sha, target: "_blank", rel: "noopener" })]));
    chips.appendChild(el("span", { class: "chip " + (run.kill_switch ? "critical" : "good") }, [el("span", { class: "dot" }), "Kill switch ", el("b", { text: run.kill_switch ? "ON" : "off" })]));
    if (run.paused) chips.appendChild(el("span", { class: "chip warning" }, [el("span", { class: "dot" }), "Calling paused"]));
    if (run.brain_down) chips.appendChild(el("span", { class: "chip serious" }, [el("span", { class: "dot" }), "Brain simulated down"]));
    if (run.playbook) chips.appendChild(el("span", { class: "chip" }, ["Playbook ", el("a", { href: "https://github.com/UmaSanskriti/between-visits-workflow/blob/main/" + run.playbook, text: run.playbook.replace(/\.md$/, "") + " v1", target: "_blank", rel: "noopener" })]));
    if (run.callback_number_set === false) chips.appendChild(el("span", { class: "chip critical" }, [el("span", { class: "dot" }), "Callback number not set"]));

    // KPIs
    const kp = clear(document.getElementById("kpis"));
    const c = s.contacts || {}, calls = s.calls || {}, tr = s.transcripts || {}, comp = s.compliance || {};
    const tile = (label, value, noteKind, note) => kp.appendChild(el("div", { class: "tile" }, [
      el("div", { class: "label", text: label }), el("div", { class: "value", text: value }),
      el("div", { class: "note " + (noteKind || "") }, noteKind && noteKind !== "plain" ? [el("span", { text: STATUS_ICON[noteKind] }), note] : [note || ""]),
    ]));
    tile("Contacts sourced", c.total ?? 0, "plain", (c.verified ?? 0) + " verified");
    tile("In queue", (s.queue || []).length, "plain", "ready or in outreach");
    tile("Calls placed", calls.attempts ?? 0, "plain", "connect rate " + pct(calls.connect_rate));
    tile("Interviews booked", s.interviews ?? 0, (s.interviews_pending_approval ?? 0) ? "warning" : "plain", (s.interviews_pending_approval ?? 0) ? (s.interviews_pending_approval) + " awaiting approval" : (calls.booked ?? 0) + " BOOKED outcomes");
    tile("Transcripts delivered", tr.delivery_rate == null ? "–" : pct(tr.delivery_rate), tr.held_pii_review ? "warning" : "plain", (tr.delivered ?? 0) + "/" + ((tr.total ?? 0) - (tr.held_pii_review ?? 0)) + (tr.held_pii_review ? ", " + tr.held_pii_review + " held for review" : ""));
    tile("Gate escapes", comp.gate_escapes ?? 0, (comp.gate_escapes ?? 0) === 0 ? "good" : "critical", (comp.gate_escapes ?? 0) === 0 ? "none, as required" : "calls placed past a failed gate");
    tile("Dead-letter queue", s.dlq ?? 0, (s.dlq ?? 0) === 0 ? "good" : "serious", (s.dlq ?? 0) === 0 ? "empty" : "needs redrive");
    tile("Errors / alerts", (s.errors ?? 0) + " / " + (s.alerts ?? 0), (s.alerts ?? 0) ? "warning" : "plain", (comp.suppressions ?? 0) + " suppressions");

    // funnel + outcomes
    bars(document.getElementById("funnel"), (s.funnel || []).map((f) => ({ label: f.stage, count: f.count })));
    const outcomes = Object.entries(calls.by_outcome || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: nice(k), count: v }));
    bars(document.getElementById("outcomes"), outcomes);
    document.getElementById("outcomes-count").textContent = (calls.attempts ?? 0) + " attempts";

    // gate decisions, last run
    const lastRun = run.run_id;
    const decisions = (d.gate_decisions || []).filter((g) => g.run_id === lastRun);
    const order = { blocked: 0, deferred: 1, allowed: 2 };
    const kind = (g) => g.allowed ? "allowed" : (g.deferred ? "deferred" : "blocked");
    decisions.sort((a, b) => order[kind(a)] - order[kind(b)] || name(a.contact_id).localeCompare(name(b.contact_id)));
    const lr = comp.last_run || {};
    document.getElementById("gate-count").textContent = (lr.allowed ?? 0) + " allowed · " + (lr.blocked ?? 0) + " blocked · " + (lr.deferred ?? 0) + " deferred";
    clear(document.getElementById("gate")).appendChild(table(["Contact", "Decision", "Reasons"], decisions.map((g) => {
      const k = kind(g);
      return [name(g.contact_id), status(k === "allowed" ? "good" : (k === "deferred" ? "warning" : "critical"), k),
        el("div", { class: "reasons" }, (g.reasons || []).map((r) => el("span", { class: "reason", text: r })))];
    }), { empty: "No gate decisions recorded for the last run.", nowrap: [1] }));

    // queue
    const queue = s.queue || [];
    document.getElementById("queue-count").textContent = queue.length + " contacts";
    clear(document.getElementById("queue")).appendChild(table(["Contact", "Segment", "Attempts", "Last outcome", "Next attempt"],
      queue.map((q) => [q.name + (q.external_id ? " · " + q.external_id : ""), nice(q.segment), q.attempts, nice(q.last_outcome) || "–", q.next_attempt_at ? fmtTime(q.next_attempt_at) : "when window opens"]),
      { num: [2], empty: "Queue is empty.", nowrap: [4] }));

    // interviews
    const interviews = d.interviews || [];
    document.getElementById("interviews-count").textContent = interviews.length + " booked";
    clear(document.getElementById("interviews")).appendChild(table(["Contact", "Scheduled for", "Status", "Confirmation", "Handoff"],
      interviews.map((i) => [name(i.contact_id), fmtTime(i.scheduled_for), i.status === "approved" ? status("good", "approved by " + i.approved_by) : status("warning", "draft, pending approval"), nice(i.confirmation_email), i.calendar_event_ref ? el("a", { href: "https://github.com/UmaSanskriti/between-visits-workflow/blob/main/" + i.calendar_event_ref.replace(/^file:/, ""), text: ".ics", target: "_blank", rel: "noopener" }) : "–"]), { nowrap: [1] }));

    // attempts
    const attempts = d.call_attempts || [];
    document.getElementById("attempts-count").textContent = attempts.length + " attempts";
    const dStatus = (t) => !t ? status("neutral", "none") : t.delivery_status === "delivered" ? status("good", "delivered") : t.delivery_status === "held_pii_review" ? status("warning", "held (PII)") : t.delivery_status === "failed" ? status("critical", "failed") : status("neutral", t.delivery_status);
    clear(document.getElementById("attempts")).appendChild(table(["Started", "Contact", "#", "Outcome", "Failure category", "Sec", "Script", "Segment", "Transcript", "Fallbacks", "Note"],
      attempts.map((a) => [fmtTime(a.started_at), name(a.contact_id), a.attempt_number, nice(a.outcome) || "(pending)", a.failure_category || "", a.duration_sec, a.script_version, nice(a.segment), dStatus(transcriptsById[a.transcript_id]), (a.fallbacks_applied || []).join(", "), a.outcome_reason || (a.duplicate_events_ignored ? a.duplicate_events_ignored + " duplicate webhook ignored" : "")]),
      { num: [2, 5], mono: [6], nowrap: [0, 3, 4, 8] }));

    // segments
    const seg = calls.by_segment || {}, ver = calls.by_script_version || {};
    const segRows = Object.entries(seg).map(([k, v]) => [nice(k), v.attempts, v.booked, v.declined, pct(v.conversion), Object.entries(v.by_outcome || {}).map(([o, n]) => nice(o) + " " + n).join(", ")]);
    const verRows = Object.entries(ver).map(([k, v]) => ["script " + k, v.attempts, v.booked, v.declined, pct(v.conversion), Object.entries(v.by_outcome || {}).map(([o, n]) => nice(o) + " " + n).join(", ")]);
    clear(document.getElementById("segments")).appendChild(table(["Group", "Attempts", "Booked", "Declined", "Conversion", "By outcome"], segRows.concat(verRows), { num: [1, 2, 3, 4] }));

    // transcripts
    const transcripts = d.transcripts || [];
    document.getElementById("transcripts-count").textContent = (tr.delivered ?? 0) + " delivered · " + (tr.held_pii_review ?? 0) + " held · " + (tr.failed ?? 0) + " failed · Brain inbox " + (tr.brain_inbox ?? 0);
    clear(document.getElementById("transcripts")).appendChild(table(["Contact", "Outcome", "Delivery", "Tries", "Utterances"],
      transcripts.map((t) => {  // eslint-disable-line
        const a = attempts.find((x) => x.id === t.call_attempt_id) || {};
        const det = el("details", null, [el("summary", { text: t.utterances.length + " lines" + (t.pii_flagged ? " · flagged: " + t.pii_reason : "") }),
          el("ul", { class: "utter" }, t.utterances.map((u) => el("li", null, [el("b", { text: u.speaker }), u.text])))]);
        return [name(t.contact_id), nice(a.outcome), dStatus(t), t.delivery_attempts, t.utterances.length ? det : el("span", { class: "muted", text: "empty (no answer)" })];
      }), { num: [3] }));

    // errors
    const errors = (d.errors || []).slice().reverse();
    document.getElementById("errors-count").textContent = errors.length + " entries";
    clear(document.getElementById("errors")).appendChild(table(["Time", "Type", "Contact", "Field", "Detail"],
      errors.map((e) => [fmtTime(e.timestamp), nice(e.error_type), e.contact_id ? name(e.contact_id) : e.call_id, e.field, e.detail]), { nowrap: [0] }));

    // alerts + dlq
    const alerts = (d.alerts || []).slice().reverse().map((a) => [fmtTime(a.timestamp), status(a.type === "kill_switch_on" || a.type === "gate_escape" ? "critical" : (a.type.includes("failed") ? "serious" : "warning"), nice(a.type)), a.detail]);
    const dlq = (d.dlq || []).map((x) => [fmtTime(x.timestamp), status("serious", "dead letter"), x.transcript_id + ": " + x.last_error + " (" + x.attempts + " attempts)"]);
    document.getElementById("alerts-count").textContent = alerts.length + " alerts · " + dlq.length + " in DLQ";
    clear(document.getElementById("alerts")).appendChild(table(["Time", "Type", "Detail"], dlq.concat(alerts), { nowrap: [0, 1] }));

    // runs
    const runs = (d.runs || []).slice().reverse();
    document.getElementById("runs-count").textContent = runs.length + " runs";
    clear(document.getElementById("runs")).appendChild(table(["Run", "Trigger", "Placed", "Blocked", "Deferred", "Delivered", "Escapes"],
      runs.map((r) => { const st = r.stages || {}, cl = st.calls || {}, dl = st.delivery || {}; return [fmtTime(r.started_at) + (r.simulated_time ? " (sim)" : ""), r.trigger, cl.placed ?? 0, cl.blocked ?? 0, cl.deferred ?? 0, dl.delivered ?? 0, r.gate_escapes ?? 0]; }),
      { num: [2, 3, 4, 5, 6], nowrap: [0] }));

    // audit
    const audit = (d.audit_log || []).slice().reverse().slice(0, 100);
    document.getElementById("audit-count").textContent = (d.audit_log || []).length + " shown of latest export";
    clear(document.getElementById("audit")).appendChild(table(["Time", "Entity", "Action", "Actor", "Id"],
      audit.map((e) => [fmtTime(e.timestamp), e.entity, nice(e.action), e.actor, e.entity_id]), { mono: [4], nowrap: [0] }));

    // contact history search
    const input = document.getElementById("search");
    const hist = document.getElementById("history");
    const scripts = d.scripts || [];
    function renderHistory(q) {
      q = q.trim().toLowerCase();
      clear(hist);
      if (!q) { hist.textContent = "Type to search."; return; }
      const hit = contacts.find((x) => x.id === q || x.external_id === q) || contacts.find((x) => (x.full_name || "").toLowerCase().includes(q) || (x.practice_name || "").toLowerCase().includes(q));
      if (!hit) { hist.textContent = "No contact matches."; return; }
      const kv = el("dl", { class: "kv" });
      [["Name", hit.full_name || "(missing)"], ["Practice", hit.practice_name], ["Specialty", hit.specialty || "(missing)"], ["Segment", nice(hit.segment)], ["Status", hit.status], ["Verification", hit.verification_status + (hit.verification_reason ? " (" + hit.verification_reason + ")" : "")], ["Attempts", hit.attempts], ["Next attempt", hit.next_attempt_at ? fmtTime(hit.next_attempt_at) : "–"], ["Time zone", hit.time_zone], ["Source", hit.lead_source], ["Ids", hit.id + (hit.external_id ? " · " + hit.external_id : "")], ["Test scenario", hit.test_scenario || "–"]]
        .forEach(([k, v]) => { kv.appendChild(el("dt", { text: k })); kv.appendChild(el("dd", { text: String(v ?? "") })); });
      hist.appendChild(kv);
      hist.appendChild(el("h3", { class: "small", text: "Channels" }));
      hist.appendChild(table(["Type", "Value (masked)", "Line type", "Verified", "Clearance", "Source"], (hit.channels || []).map((ch) => [ch.type, ch.value, ch.line_type, ch.verified ? "yes" : "no" + (ch.verification_reason ? " (" + ch.verification_reason + ")" : ""), nice(ch.call_clearance), ch.source])));
      hist.appendChild(el("h3", { class: "small", text: "Gate decisions" }));
      hist.appendChild(table(["Run", "Decision", "Reasons"], (d.gate_decisions || []).filter((g) => g.contact_id === hit.id).reverse().map((g) => [g.run_id, kind(g), (g.reasons || []).join("; ")])));
      hist.appendChild(el("h3", { class: "small", text: "Call attempts and scripts" }));
      const mine = attempts.filter((a) => a.contact_id === hit.id);
      hist.appendChild(table(["#", "Started", "Outcome", "Script", "First message"], mine.map((a) => {
        const sc = scripts.find((x) => x.call_id === a.id);
        return [a.attempt_number, fmtTime(a.started_at), nice(a.outcome), a.script_version, sc ? el("details", null, [el("summary", { text: "show" }), el("pre", { class: "block", text: sc.first_message + "\n\n--- system prompt ---\n" + sc.system_prompt })]) : "–"];
      }), { num: [0] }));
      hist.appendChild(el("h3", { class: "small", text: "Audit trail" }));
      hist.appendChild(table(["Time", "Entity", "Action", "Change"], (d.audit_log || []).filter((e) => e.entity_id === hit.id || e.contact_id === hit.id).reverse().map((e) => [fmtTime(e.timestamp), e.entity + " " + e.entity_id, nice(e.action), JSON.stringify(e.after || {})])));
    }
    input.addEventListener("input", () => renderHistory(input.value));
    const params = new URLSearchParams(location.search);
    if (params.get("contact")) { input.value = params.get("contact"); renderHistory(input.value); }

    document.getElementById("footer").textContent = "Snapshot generated " + (s.generated_at ? fmtTime(s.generated_at) : "–") + " · run " + (run.run_id || "–") + " · Between Visits infra v0 (dry run).";
  }

  loadAll().then((d) => {
    if (!d.summary) {
      document.getElementById("error").textContent = "No pipeline data yet. Run `python -m infra run` locally or trigger the Dry-run pipeline workflow; the snapshot lands in docs/data/.";
      return;
    }
    render(d);
  }).catch((e) => { document.getElementById("error").textContent = "Failed to render: " + e.message; });
})();
