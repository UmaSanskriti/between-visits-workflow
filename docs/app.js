/* Between Visits · Clinic Reach-Out Agent · operator view.
   Reads the masked JSON snapshot in data/ (written by `python -m infra run`) and asks the public GitHub API
   whether a run is in progress right now. Everything from JSON goes in with textContent. */
(function () {
  "use strict";

  const REPO = "https://github.com/UmaSanskriti/between-visits-workflow";
  const API_RUNS = "https://api.github.com/repos/UmaSanskriti/between-visits-workflow/actions/workflows/pipeline.yml/runs?per_page=1";
  const FILES = ["run", "summary", "contacts", "call_attempts", "email_attempts", "enrichments", "scripts", "transcripts", "interviews", "gate_decisions", "errors", "dlq", "alerts", "runs", "audit_log"];
  const ICON = { good: "✓", warning: "!", serious: "▲", critical: "✕", neutral: "·" };
  const TZ = "America/New_York";
  const LOCAL = "http://127.0.0.1:8787";
  const SRC = "data/";
  // An https page may call the loopback service only when the fetch opts in (Chrome private network access).
  function localFetch(url, opts) {
    opts = Object.assign({ cache: "no-store", targetAddressSpace: "loopback" }, opts || {});
    if (!opts.signal) { const ctl = new AbortController(); setTimeout(() => ctl.abort(), 4000); opts.signal = ctl.signal; }
    return fetch(url, opts);
  }
  const LOCAL_CONSOLE = LOCAL + "/";
  const GH_ACTIONS = REPO + "/actions";
  let ring_ctl = null; // set by render(); lets the local run poller drive the loop
  let CHANNEL = "call"; // "email" | "call", set by render() from the snapshot

  // ---------- helpers ----------
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v; else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v); else node.setAttribute(k, v);
    }
    (children || []).forEach((c) => { if (c != null) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }
  const $ = (id) => document.getElementById(id);
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function status(kind, label) { return el("span", { class: "status " + kind }, [el("span", { text: ICON[kind] || "" }), label]); }
  function pill(kind, label) { return el("span", { class: "pill " + kind, text: label }); }
  function table(headers, rows, opts) {
    opts = opts || {};
    if (!rows.length) return el("div", { class: "empty", text: opts.empty || "Nothing." });
    const thead = el("thead", null, [el("tr", null, headers.map((h, i) => el("th", { class: (opts.num || []).includes(i) ? "num" : "" }, [h])))]);
    const tbody = el("tbody", null, rows.map((r) => el("tr", null, r.map((cell, i) => {
      const td = el("td", { class: ((opts.num || []).includes(i) ? "num " : "") + ((opts.mono || []).includes(i) ? "mono " : "") + ((opts.nowrap || []).includes(i) ? "nowrap" : "") });
      if (cell instanceof Node) td.appendChild(cell); else td.textContent = cell == null ? "" : String(cell);
      return td;
    }))));
    return el("table", null, [thead, tbody]);
  }
  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso); if (isNaN(d)) return iso;
    return d.toLocaleString("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " ET";
  }
  function ago(iso) {
    if (!iso) return "";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return Math.round(s) + "s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 172800) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  function dayKey(iso) { return iso ? new Date(iso).toLocaleDateString("en-CA", { timeZone: TZ }) : ""; }
  function pct(x) { return x == null || isNaN(x) ? "–" : Math.round(x * 100) + "%"; }
  function nice(s) { return String(s || "").replace(/_/g, " ").toLowerCase(); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }

  const tip = $("tooltip");
  function showTip(evt, label, value) {
    clear(tip); tip.appendChild(el("div", null, [el("span", { class: "k" }), el("b", { text: String(value) }), " ", el("span", { class: "muted", text: label })]));
    tip.style.display = "block"; moveTip(evt);
  }
  function moveTip(evt) {
    const r = evt.target && evt.target.getBoundingClientRect ? evt.target.getBoundingClientRect() : { right: 0, top: 0 };
    const x = evt.clientX != null ? evt.clientX : r.right, y = evt.clientY != null ? evt.clientY : r.top;
    tip.style.left = Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8) + "px"; tip.style.top = Math.max(8, y - tip.offsetHeight - 10) + "px";
  }
  function hideTip() { tip.style.display = "none"; }
  function bars(container, items, title) {
    clear(container);
    if (title) { container.appendChild(el("div", { class: "lbl", text: "" })); container.appendChild(el("div", { class: "small muted", text: title })); }
    const max = Math.max(1, ...items.map((i) => i.count));
    items.forEach((item) => {
      container.appendChild(el("div", { class: "lbl", text: item.label, title: item.label }));
      const bar = el("div", { class: "bar", tabindex: "0", role: "img", "aria-label": item.label + ": " + item.count });
      bar.style.width = Math.max(0.4, (item.count / max) * 100) + "%";
      const over = (e) => showTip(e, item.label, item.count);
      bar.addEventListener("pointerenter", over); bar.addEventListener("pointermove", moveTip); bar.addEventListener("pointerleave", hideTip);
      bar.addEventListener("focus", over); bar.addEventListener("blur", hideTip);
      container.appendChild(el("div", { class: "track" }, [bar, el("span", { class: "val", text: String(item.count) })]));
    });
    if (!items.length) container.appendChild(el("div", { class: "empty", text: "No data yet." }));
  }

  // ---------- theme ----------
  const themeBtn = $("theme"), THEMES = ["system", "light", "dark"];
  function applyTheme(t) {
    if (t === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
    themeBtn.textContent = "Theme: " + t;
    try { localStorage.setItem("bv-theme", t); } catch (e) { /* private mode */ }
  }
  let theme = "system";
  try { theme = localStorage.getItem("bv-theme") || "system"; } catch (e) { /* ignore */ }
  applyTheme(theme);
  themeBtn.addEventListener("click", () => { theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]; applyTheme(theme); });

  // ---------- load ----------
  async function loadAll(base) {
    const src = base || SRC, useLocal = src.startsWith(LOCAL);
    const stamp = Date.now(), out = {};
    await Promise.all(FILES.map(async (name) => {
      try { const res = await (useLocal ? localFetch : fetch)(src + name + ".json?t=" + stamp, { cache: "no-store" }); out[name] = res.ok ? await res.json() : null; }
      catch (e) { out[name] = null; }
    }));
    return out;
  }
  async function loadLiveRun() {
    try {
      const res = await fetch(API_RUNS, { headers: { Accept: "application/vnd.github+json" } });
      if (!res.ok) return null;
      const data = await res.json();
      return (data.workflow_runs || [])[0] || null;
    } catch (e) { return null; }
  }

  // ---------- per-contact helpers ----------
  function contactTone(c) {
    switch (c.status) {
      case "BOOKED": return ["good", "Booked"];
      case "IN_OUTREACH": return ["brand", "In outreach"];
      case "READY": return ["neutral", "Queued"];
      case "SUPPRESSED": return ["critical", "Do not contact"];
      case "UNREACHABLE": return ["warning", "Unreachable"];
      case "DECLINED": return ["neutral", c.last_outcome === "NOT_A_FIT" ? "Not a fit" : c.last_outcome === "DECLINED_RECORDING" ? "Declined recording" : "Declined"];
      default: return ["warning", "Needs review"];
    }
  }
  function nextStep(c, interview, gate) {
    if (c.status === "BOOKED" && interview) {
      const when = fmtTime(interview.scheduled_for);
      const lead = interview.scheduled_for ? when + ", " : "Agreed to talk; Anirudh reaches out" + (interview.contact_method && interview.contact_method !== "unknown" ? " via " + nice(interview.contact_method) : "") + ", ";
      return interview.status === "approved" ? ["good", lead + "approved by " + interview.approved_by] : ["warning", lead + "waiting for approval"];
    }
    if (c.status === "IN_OUTREACH") {
      if (CHANNEL === "email") return ["brand", "Awaiting reply" + (c.next_attempt_at ? "; follow-up " + fmtTime(c.next_attempt_at) : "")];
      return ["brand", c.next_attempt_at ? "Retry " + fmtTime(c.next_attempt_at) + (c.last_outcome === "CALLBACK_REQUESTED" ? " (asked for a call back)" : "") : "Call in progress"];
    }
    if (c.status === "READY") return ["neutral", gate && !gate.allowed ? (gate.deferred ? "Waiting: " + gate.reasons.map(nice).join(", ") : "Blocked: " + gate.reasons.map(nice).join(", ")) : (CHANNEL === "email" ? "Email on the next run" : "Call when the window opens")];
    if (c.status === "SUPPRESSED") return ["critical", "Never contact again"];
    if (c.status === "UNREACHABLE") return ["warning", c.last_outcome === "NO_REPLY" ? "No reply after the follow-up" : "No more attempts (cap reached)"];
    if (c.status === "DECLINED") return ["neutral", c.last_outcome === "NOT_A_FIT" ? "Closed: not a fit" : "Closed"];
    if (c.duplicate_of) return ["warning", "Possible duplicate, review and merge"];
    if (c.verification_reason) return ["warning", "Cannot reach: " + nice(c.verification_reason)];
    return ["neutral", "Awaiting verification"];
  }

  // ---------- render ----------
  function render(d, live) {
    const run = d.run || {}, s = d.summary || {};
    const contacts = d.contacts || [], attempts = d.call_attempts || [], transcripts = d.transcripts || [];
    const interviews = d.interviews || [], errors = d.errors || [], alerts = d.alerts || [], dlq = d.dlq || [];
    const decisions = d.gate_decisions || [], scripts = d.scripts || [];
    const emailAttempts = d.email_attempts || [], em = s.emails || {};
    const channel = run.channel || s.channel || "email";  // email is the primary channel; old snapshots lack the field
    const isEmail = channel === "email"; CHANNEL = channel;
    const byId = Object.fromEntries(contacts.map((c) => [c.id, c]));
    const attemptById = Object.fromEntries(attempts.map((a) => [a.id, a]));
    const emailById = Object.fromEntries(emailAttempts.map((a) => [a.id, a]));
    const enrichByContact = Object.fromEntries((d.enrichments || []).map((e) => [e.contact_id, e]));
    const anyAttempt = (id) => attemptById[id] || emailById[id];
    const transcriptById = Object.fromEntries(transcripts.map((t) => [t.id, t]));
    const name = (cid) => { const c = byId[cid]; return c ? (c.full_name || "(no name)") + (c.external_id ? " · " + c.external_id : "") : cid; };
    const calls = s.calls || {}, tr = s.transcripts || {}, comp = s.compliance || {};
    const st = run.stages || {}, cl = st.calls || {}, wh = st.webhooks || {}, dv = st.delivery || {}, ing = st.ingest || {};
    const lastRun = run.run_id;
    const runAttempts = attempts.filter((a) => a.run_id === lastRun);
    const runBooked = runAttempts.filter((a) => a.outcome === "BOOKED").length;
    const runCategorised = runAttempts.filter((a) => a.failure_category).length;
    const queue = s.queue || [];
    const inProgress = attempts.filter((a) => !a.outcome).length;
    const pendingApproval = interviews.filter((i) => i.status === "draft_pending_approval").length;
    const retries = contacts.filter((c) => c.status === "IN_OUTREACH" && c.next_attempt_at).length;
    const pendingDelivery = transcripts.filter((t) => t.delivery_status === "pending").length;
    const stE = st.email || {}, rp = st.replies || {};
    const emailsQueued = emailAttempts.filter((a) => a.status === "queued").length;
    const awaitingReply = em.awaiting_reply ?? emailAttempts.filter((a) => a.status === "sent" && !a.outcome).length;
    const agreedTotal = em.agreed ?? contacts.filter((c) => c.status === "BOOKED").length;
    const followUpsDue = em.follow_ups_due ?? queue.filter((q) => q.next_attempt_at && new Date(q.next_attempt_at) <= Date.now()).length;
    const runAgreed = emailAttempts.filter((a) => a.run_id === lastRun && a.outcome === "AGREED_TO_TALK").length;

    // header chips
    const chips = clear($("run-chips"));
    if (run.mode === "live") chips.appendChild(el("span", { class: "chip critical", title: "Real calls through Vapi to allowlisted team numbers only." }, [el("span", { class: "dot" }), el("b", { text: "LIVE" })]));
    else chips.appendChild(el("span", { class: "chip mode", title: "No real calls are placed; the voice provider and the Brain are stand-ins and every practice is fictional." }, [el("b", { text: "DRY RUN" })]));
    chips.appendChild(el("span", { class: "chip" }, ["Snapshot ", el("b", { text: run.finished_at ? fmtTime(run.finished_at) : "never" })]));
    if (run.simulated_time) chips.appendChild(el("span", { class: "chip warning" }, [el("span", { class: "dot" }), "simulated clock ", el("b", { text: fmtTime(run.now_used) })]));
    chips.appendChild(el("span", { class: "chip " + (run.kill_switch ? "critical" : "good") }, [el("span", { class: "dot" }), "Kill switch ", el("b", { text: run.kill_switch ? "ON" : "off" })]));
    const sh = (live && live.__sheet) || null;
    if (sh && sh.path) chips.appendChild(el(sh.url ? "a" : "span", Object.assign({ class: "chip", title: (sh.backend === "google" ? "Google Sheet: " : "Workbook: ") + sh.path }, sh.url ? { href: sh.url, target: "_blank", rel: "noopener" } : {}), [(sh.backend === "google" ? "Google Sheet " : "Sheet "), el("b", { text: String(sh.rows || 0) + " practices" }), sh.last_import || sh.last_export ? " · synced " + fmtTime(sh.last_import && sh.last_import > (sh.last_export || "") ? sh.last_import : sh.last_export) : ""]));
    else if (s.practices) chips.appendChild(el("span", { class: "chip" }, ["Practices ", el("b", { text: (s.practices.dummy || 0) + " dummy · " + (s.practices.real || 0) + " real" })]));
    if (run.paused) chips.appendChild(el("span", { class: "chip warning" }, [el("span", { class: "dot" }), "Calling paused"]));
    if (run.brain_down) chips.appendChild(el("span", { class: "chip serious" }, [el("span", { class: "dot" }), "Brain simulated down"]));
    if (run.callback_number_set === false) chips.appendChild(el("span", { class: "chip critical" }, [el("span", { class: "dot" }), "Callback number not set"]));
    if (run.git_sha) chips.appendChild(el("span", { class: "chip" }, [el("a", { href: REPO + "/commit/" + run.git_sha, text: run.git_sha, target: "_blank", rel: "noopener" })]));

    // ---------- the loop ----------
    const stages = isEmail ? [
      { key: "list", title: "Approved list", now: contacts.length, nowLabel: "practices", last: "Loaded " + (ing.rows ?? contacts.length) + " practices, " + ((s.contacts || {}).verified ?? 0) + " verified" },
      { key: "gate", title: "Gate check", now: queue.length, nowLabel: "waiting", last: "Gate: " + (stE.allowed ?? 0) + " allowed, " + (stE.blocked ?? 0) + " blocked, " + (stE.deferred ?? 0) + " deferred" },
      { key: "send", title: "Compose + send", now: emailsQueued, nowLabel: "sending", last: "Sent " + plural(stE.sent ?? stE.placed ?? 0, "email") + (stE.script_errors ? ", " + stE.script_errors + " blocked by content checks" : "") },
      { key: "log", title: "Delivery logged", now: pendingDelivery, nowLabel: "to deliver", last: "Processed " + (rp.applied ?? rp.replies ?? 0) + " replies, delivered " + (dv.delivered ?? 0) + " threads" + (dv.held ? ", " + dv.held + " held" : "") + (dv.failed ? ", " + dv.failed + " failed" : "") },
      { key: "reply", title: "Reply → handoff", now: pendingApproval, nowLabel: "to approve", last: runAgreed + " agreed, handoffs drafted for approval", cls: "decision" },
      { key: "follow", title: "Follow-up", now: awaitingReply, nowLabel: "awaiting reply", last: followUpsDue + " follow-ups due (one per practice, after " + (run.limits && run.limits.follow_up_days ? run.limits.follow_up_days + " days" : "a few days") + ")" },
    ] : [
      { key: "list", title: "Approved list", now: contacts.length, nowLabel: "practices", last: "Loaded " + (ing.rows ?? contacts.length) + " practices, " + ((s.contacts || {}).verified ?? 0) + " verified" },
      { key: "gate", title: "Gate check", now: queue.length, nowLabel: "waiting", last: "Gate: " + (cl.allowed ?? 0) + " allowed, " + (cl.blocked ?? 0) + " blocked, " + (cl.deferred ?? 0) + " deferred" },
      { key: "call", title: "Script + call", now: inProgress, nowLabel: "in progress", last: "Placed " + plural(cl.placed ?? 0, "call") + (cl.script_errors ? ", " + cl.script_errors + " blocked by script checks" : "") },
      { key: "log", title: "Outcome logged", now: pendingDelivery, nowLabel: "to deliver", last: "Logged " + (wh.applied ?? 0) + " outcomes, delivered " + (dv.delivered ?? 0) + " transcripts" + (dv.held ? ", " + dv.held + " held" : "") + (dv.failed ? ", " + dv.failed + " failed" : "") },
      { key: "book", title: "Booked → invite", now: pendingApproval, nowLabel: "to approve", last: runBooked + " booked, invites drafted for approval", cls: "decision" },
      { key: "review", title: "Review → retry", now: retries, nowLabel: "retries set", last: runCategorised + " calls categorised, retries scheduled" },
    ];
    const nodesHost = clear($("nodes")), listHost = clear($("nodes-list"));
    const nodeEls = [];
    // direction arrows between nodes, clockwise around the ring (r=190 around 260,260 in the 520 viewBox)
    const arcs = clear(document.getElementById("arcs"));
    for (let i = 0; i < stages.length; i++) {
      const a1 = (-90 + i * 60 + 24) * Math.PI / 180, a2 = (-90 + (i + 1) * 60 - 24) * Math.PI / 180;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M " + (260 + 190 * Math.cos(a1)).toFixed(1) + " " + (260 + 190 * Math.sin(a1)).toFixed(1) + " A 190 190 0 0 1 " + (260 + 190 * Math.cos(a2)).toFixed(1) + " " + (260 + 190 * Math.sin(a2)).toFixed(1));
      path.setAttribute("class", "arc"); path.setAttribute("marker-end", "url(#arrow)");
      arcs.appendChild(path);
    }
    stages.forEach((sg, i) => {
      const angle = (-90 + i * 60) * Math.PI / 180;
      const node = el("div", { class: "node " + (sg.cls || ""), "data-key": sg.key }, [
        el("div", { class: "t", text: sg.title }),
        el("div", { class: "c" }, [String(sg.now), el("small", { text: sg.nowLabel })]),
        el("div", { class: "d", text: "now" }),
      ]);
      node.style.left = (50 + 36.5 * Math.cos(angle)) + "%";
      node.style.top = (50 + 36.5 * Math.sin(angle)) + "%";
      nodesHost.appendChild(node); nodeEls.push(node);
      listHost.appendChild(el("div", { class: "node " + (sg.cls || "") }, [el("div", { class: "t", text: sg.title }), el("div", { class: "c" }, [String(sg.now), el("small", { text: sg.nowLabel + " now" })])]));
    });

    // live state
    const running = live && (live.status === "in_progress" || live.status === "queued");
    const ring = $("ring"), stateEl = $("state"), liveDot = $("live");
    ring.classList.toggle("running", !!running);
    clear(stateEl); stateEl.className = "state" + (running ? " running" : "");
    stateEl.appendChild(liveDot); liveDot.className = "live" + (running ? " on" : "");
    stateEl.appendChild(document.createTextNode(running ? "Running" : "Idle"));
    const newer = live && live.status === "completed" && run.finished_at && new Date(live.updated_at) - new Date(run.finished_at) > 90000;
    $("since").textContent = running ? "started " + ago(live.run_started_at || live.created_at) + " · " + nice(live.event) : (newer ? "a newer run finished " + ago(live.updated_at) + "; this snapshot is publishing" : "last run " + ago(run.finished_at));

    const nowEl = clear($("now"));
    if (running) {
      nowEl.appendChild(el("span", null, [el("b", { text: "Working through the loop now. " }), el("a", { href: live.html_url, text: "Watch the run", target: "_blank", rel: "noopener" })]));
    } else {
      const due = queue.filter((q) => q.next_attempt_at).sort((a, b) => a.next_attempt_at.localeCompare(b.next_attempt_at))[0];
      const windowWait = queue.filter((q) => !q.next_attempt_at).length;
      if (due) nowEl.appendChild(el("div", null, [el("b", { text: isEmail ? "Next follow-up: " : "Next call: " }), due.name + " " + fmtTime(due.next_attempt_at) + (new Date(due.next_attempt_at) < Date.now() ? " (due, waits for the next run)" : "")]));
      else if (windowWait) nowEl.appendChild(el("div", null, [el("b", { text: "Next: " }), plural(windowWait, "practice") + (isEmail ? " ready to email on the next run" : " waiting for the calling window (Mon–Fri 9–5 ET)")]));
      else if (!(isEmail && awaitingReply)) nowEl.appendChild(el("div", null, [el("b", { text: "Nothing queued. " }), "Approve a new batch to continue."]));
      if (isEmail && awaitingReply) nowEl.appendChild(el("div", null, [el("b", { text: "Awaiting reply: " }), plural(awaitingReply, "practice") + " emailed, no reply yet"]));
      if (pendingApproval) nowEl.appendChild(el("div", null, [el("b", { text: "Waiting on you: " }), plural(pendingApproval, "handoff", "handoffs") + " to approve"]));
      if (dlq.length) nowEl.appendChild(el("div", null, [el("b", { text: "Needs a redrive: " }), plural(dlq.length, "transcript") + " dead-lettered"]));
    }

    // last-run feed + replay
    const feed = clear($("feed"));
    const labels = isEmail ? ["load", "gate", "send", "log", "reply", "follow"] : ["load", "gate", "call", "log", "book", "review"];
    stages.forEach((sg, i) => feed.appendChild(el("li", { "data-i": String(i) }, [el("span", { class: "w", text: labels[i] }), el("span", { class: "x", text: sg.last })])));
    $("feed-note").textContent = "Last run " + fmtTime(run.started_at) + (run.trigger ? " · " + nice(run.trigger) : "");
    let timer = null;
    const restingIndex = running ? -1 : (pendingApproval ? 4 : queue.length ? 1 : 0);
    function settle() {
      nodeEls.forEach((n, i) => { n.classList.remove("active"); n.classList.toggle("pulse", !running && i === restingIndex); });
      feed.querySelectorAll("li").forEach((li) => li.classList.remove("now"));
    }
    function replay() {
      if (timer) clearInterval(timer);
      let i = 0; settle(); nodeEls.forEach((n) => n.classList.remove("pulse"));
      timer = setInterval(() => {
        nodeEls.forEach((n, k) => n.classList.toggle("active", k === i));
        feed.querySelectorAll("li").forEach((li, k) => li.classList.toggle("now", k === i));
        i += 1;
        if (i >= stages.length) { clearInterval(timer); timer = null; setTimeout(settle, 900); }
      }, 750);
    }
    $("replay").addEventListener("click", replay);
    if (running) {
      let k = 0;
      timer = setInterval(() => { nodeEls.forEach((n, i) => n.classList.toggle("active", i === k)); k = (k + 1) % stages.length; }, 900);
    } else { replay(); }
    ring_ctl = {
      stop: () => { if (timer) { clearInterval(timer); timer = null; } nodeEls.forEach((n) => n.classList.remove("pulse")); },
      highlight: (indexes) => nodeEls.forEach((n, i) => n.classList.toggle("active", indexes.includes(i))),
      feedRow: (i, text) => { const li = feed.querySelectorAll("li")[i]; if (li) { li.querySelector(".x").textContent = text; feed.querySelectorAll("li").forEach((row, k) => row.classList.toggle("now", k === i)); } },
      settle,
    };

    // ---------- KPIs ----------
    const kp = clear($("kpis"));
    const tile = (label, value, kind, note) => kp.appendChild(el("div", { class: "tile" }, [el("div", { class: "label", text: label }), el("div", { class: "value", text: value }), el("div", { class: "note " + (kind || "") }, kind && kind !== "plain" ? [el("span", { text: ICON[kind] }), note] : [note || ""])]));
    const today = dayKey(run.now_used || run.finished_at);
    const todayCalls = attempts.filter((a) => dayKey(a.started_at) === today).length;
    const cap = (run.limits && run.limits.max_calls_per_day) || 10;
    if (isEmail) {
      const todayEmails = emailAttempts.filter((a) => a.sent_at && dayKey(a.sent_at) === today).length;
      const ecap = (run.limits && (run.limits.max_emails_per_day || run.limits.max_calls_per_day)) || 10;
      tile("Agreed", agreedTotal, pendingApproval ? "warning" : "good", pendingApproval ? pendingApproval + " awaiting approval" : "handoffs to Anirudh");
      tile("Awaiting reply", awaitingReply, followUpsDue ? "warning" : "plain", followUpsDue ? followUpsDue + " follow-ups due" : "emailed, no reply yet");
      tile("Emails " + (run.simulated_time ? "that day" : "today"), todayEmails + " / " + ecap, todayEmails >= ecap ? "warning" : "plain", todayEmails >= ecap ? "daily cap reached" : "daily cap");
      const enr = s.enrichment || {};
      tile("Researched", (enr.total ?? Object.keys(enrichByContact).length) + " / " + contacts.length, "plain", (enr.with_glp1 ?? 0) + " mention GLP-1 care");
    } else {
      tile("Booked", contacts.filter((c) => c.status === "BOOKED").length, pendingApproval ? "warning" : "good", pendingApproval ? pendingApproval + " awaiting approval" : "all approved");
      tile("In queue", queue.length, "plain", retries + " retries scheduled");
      tile("Calls " + (run.simulated_time ? "that day" : "today"), todayCalls + " / " + cap, todayCalls >= cap ? "warning" : "plain", todayCalls >= cap ? "daily cap reached" : "daily cap");
    }
    tile("Transcripts to Brain", tr.delivery_rate == null ? "–" : pct(tr.delivery_rate), tr.held_pii_review ? "warning" : "plain", (tr.delivered || 0) + "/" + ((tr.total || 0) - (tr.held_pii_review || 0)) + (tr.held_pii_review ? ", " + tr.held_pii_review + " held" : ""));
    tile("Gate escapes", comp.gate_escapes || 0, (comp.gate_escapes || 0) === 0 ? "good" : "critical", (comp.gate_escapes || 0) === 0 ? "none" : (isEmail ? "emails past a failed gate" : "calls past a failed gate"));
    tile("Dead letters", dlq.length, dlq.length === 0 ? "good" : "serious", dlq.length === 0 ? "queue empty" : "run redrive");
    tile("Errors / alerts", (s.errors || 0) + " / " + (s.alerts || 0), (s.alerts || 0) ? "warning" : "plain", "see the cards");

    // ---------- practice cards ----------
    const latestGate = {};
    decisions.forEach((g) => { if (!latestGate[g.contact_id] || g.run_id >= latestGate[g.contact_id].run_id) latestGate[g.contact_id] = g; });
    const errorsFor = (c) => errors.filter((e) => e.contact_id === c.id || (e.call_id && anyAttempt(e.call_id) && anyAttempt(e.call_id).contact_id === c.id));
    const alertsFor = (c) => alerts.filter((a) => a.contact_id === c.id || (a.call_attempt_id && anyAttempt(a.call_attempt_id) && anyAttempt(a.call_attempt_id).contact_id === c.id) || (a.transcript_id && transcriptById[a.transcript_id] && transcriptById[a.transcript_id].contact_id === c.id));
    const dlqFor = (c) => dlq.filter((x) => transcriptById[x.transcript_id] && transcriptById[x.transcript_id].contact_id === c.id);
    const order = { BOOKED: 0, IN_OUTREACH: 1, READY: 2, VERIFIED: 3, SOURCED: 3, NEW: 3, UNREACHABLE: 4, DECLINED: 5, SUPPRESSED: 6 };
    const sorted = contacts.slice().sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || (a.full_name || "zz").localeCompare(b.full_name || "zz"));
    const views = sorted.map((c) => {
      const mine = attempts.filter((a) => a.contact_id === c.id).sort((a, b) => a.attempt_number - b.attempt_number);
      const interview = interviews.find((i) => i.contact_id === c.id);
      const mails = emailAttempts.filter((a) => a.contact_id === c.id).sort((a, b) => (a.attempt_number || 0) - (b.attempt_number || 0) || (a.sent_at || "").localeCompare(b.sent_at || ""));
      const errs = errorsFor(c), als = alertsFor(c), dl = dlqFor(c);
      const held = mine.concat(mails).some((a) => { const t = transcriptById[a.transcript_id]; return t && (t.delivery_status === "held_pii_review" || t.delivery_status === "failed"); });
      const mailTrouble = mails.some((a) => a.status === "failed" || a.status === "bounced" || a.outcome === "BOUNCED");
      const attention = errs.length > 0 || als.length > 0 || dl.length > 0 || held || mailTrouble || (interview && interview.status === "draft_pending_approval") || ["VERIFIED", "SOURCED", "NEW"].includes(c.status);
      return { c, mine, mails, interview, errs, als, dl, attention, gate: latestGate[c.id] };
    });

    const hasHandoff = (h) => !!(h && (h.contact_email || (h.contact_method && h.contact_method !== "unknown") || h.contact_number || h.availability || h.notes));
    function handoffBlock(h) {
      return el("div", { class: "handoff" }, [
        el("h4", { text: "Handoff to Anirudh" }),
        h.contact_email ? el("div", { class: "issue" }, [el("span", { class: "k", text: "email" }), el("span", { text: h.contact_email })]) : null,
        (h.contact_method && h.contact_method !== "unknown") ? el("div", { class: "issue" }, [el("span", { class: "k", text: "reach via" }), el("span", { text: nice(h.contact_method) + (h.contact_number ? " · " + h.contact_number : "") })]) : null,
        h.availability ? el("div", { class: "issue" }, [el("span", { class: "k", text: "available" }), el("span", { text: h.availability })]) : null,
        h.person_spoken_to ? el("div", { class: "issue" }, [el("span", { class: "k", text: "spoke to" }), el("span", { text: nice(h.person_spoken_to) })]) : null,
        h.notes ? el("div", { class: "issue" }, [el("span", { class: "k", text: "notes" }), el("span", { text: h.notes })]) : null,
      ]);
    }
    function emailSection(v) {
      const msec = el("div", { class: "sec" }, [el("h4", null, ["Emails", el("span", { text: plural(v.mails.length, "email") })])]);
      v.mails.forEach((m, idx) => {
        const t = transcriptById[m.transcript_id];
        const sStatus = m.status === "sent" ? status("good", "sent") : m.status === "bounced" ? status("critical", "bounced") : m.status === "failed" ? status("critical", "send failed") : status("neutral", nice(m.status || "queued"));
        const tStatus = t ? (t.delivery_status === "delivered" ? status("good", "thread delivered to Brain") : t.delivery_status === "held_pii_review" ? status("warning", "thread held for review") : t.delivery_status === "failed" ? status("critical", "thread delivery failed") : status("neutral", "thread " + nice(t.delivery_status))) : null;
        const line = el("div", { class: "line" }, [
          el("div", null, [el("b", { text: m.outcome ? nice(m.outcome) : (m.status === "sent" ? "awaiting reply" : nice(m.status || "queued")) }), " ", el("span", { class: "when", text: (m.sent_at ? fmtTime(m.sent_at) : "not sent") + (m.script_version ? " · script " + m.script_version : "") })]),
          el("div", { class: "muted small", text: m.subject || "" }),
          el("div", { class: "tags" }, [m.failure_category ? el("span", { class: "cat", text: m.failure_category }) : null, sStatus, tStatus]),
        ]);
        if (m.outcome_reason) line.appendChild(el("div", { class: "muted small", text: m.outcome_reason }));
        if (m.redirected_to) line.appendChild(el("div", { class: "muted small", text: "delivered to " + m.redirected_to + " for the demo" }));
        if (m.personalization && m.personalization.length) line.appendChild(el("div", { class: "muted small", text: "Personalised with: " + m.personalization.join(" · ") }));
        if (m.body) line.appendChild(el("details", null, [el("summary", { text: "Email sent" }), el("pre", { class: "block", text: m.body })]));
        if (m.reply_text) line.appendChild(el("details", { open: "" }, [el("summary", { text: "Reply" + (m.replied_at ? " · " + fmtTime(m.replied_at) : "") }), el("div", { class: "reply-quote", text: m.reply_text })]));
        if (hasHandoff(m.handoff)) line.appendChild(handoffBlock(m.handoff));
        msec.appendChild(el("div", { class: "mail" }, [el("div", { class: "num", text: String(m.attempt_number || idx + 1) }), line]));
      });
      return msec;
    }
    function cardFor(v) {
      const c = v.c, [tone, label] = contactTone(c);
      const card = el("article", { class: "pcard", "data-tone": tone });
      card.appendChild(el("div", { class: "ph" }, [
        el("div", null, [el("h3", { text: c.full_name || "(name missing)" }), el("div", { class: "practice", text: c.practice_name || "(practice missing)" }), el("div", { class: "ext", text: (c.external_id ? c.external_id + " · " : "") + c.id })]),
        el("div", { class: "pills" }, [el("span", { class: "tag " + ((c.source_tag || (c.external_id && c.external_id.startsWith("t_") ? "dummy" : "real")) === "dummy" ? "dummy" : "real"), text: c.source_tag || (c.external_id && c.external_id.startsWith("t_") ? "dummy" : "real") }), pill(tone === "neutral" ? "" : tone, label)])]));
      const [nk, ntext] = nextStep(c, v.interview, v.gate);
      card.appendChild(el("div", { class: "next " + (nk === "brand" || nk === "neutral" ? "" : nk) }, [el("b", { text: "Next: " }), ntext]));
      const en = enrichByContact[c.id];
      if (en) {
        const about = el("div", { class: "about" }, [el("h4", null, ["About the practice", el("span", { text: en.source === "web" ? "from " + (en.website || "the website").replace(/^https?:\/\//, "") : en.source === "sheet" ? "from the practice sheet" : "from seed fixture" })])]);
        if (en.summary) about.appendChild(el("div", { class: "small", text: en.summary }));
        if (en.services && en.services.length) about.appendChild(el("div", { class: "chips-sm" }, en.services.slice(0, 6).map((sv) => el("span", { class: "m", text: sv }))));
        if (en.doctor_bio) about.appendChild(el("div", { class: "muted small", text: en.doctor_bio }));
        if (en.recent_note) about.appendChild(el("div", { class: "muted small", text: "Recent: " + en.recent_note }));
        if (en.patient_focus || en.languages) about.appendChild(el("div", { class: "muted small", text: [en.patient_focus ? "Focus: " + en.patient_focus : "", en.languages ? "Languages: " + en.languages : ""].filter(Boolean).join(" · ") }));
        const site = en.website || c.website;
        if (site && /^https?:\/\//.test(site)) about.appendChild(el("div", { class: "small" }, [el("a", { href: site, text: site.replace(/^https?:\/\//, "").replace(/\/$/, ""), target: "_blank", rel: "noopener" }), en.website_source ? el("span", { class: "muted", text: " · found via " + nice(en.website_source) + (en.phone_match === "match" ? ", phone matches" : en.phone_match === "mismatch" ? ", phone does not match" : "") }) : null]));
        const published = (c.channels || []).find((ch) => ch.type === "email" && ch.source === "website");
        if (published) about.appendChild(el("div", { class: "muted small", text: "Email " + published.value + " published on " + (published.source_url || "the site").replace(/^https?:\/\//, "") }));
        if (en.research_note) about.appendChild(el("div", { class: "muted small", text: "Research: " + en.research_note }));
        card.appendChild(about);
      }

      if (isEmail) {
        if (v.mails.length) card.appendChild(emailSection(v));
        else card.appendChild(el("div", { class: "sec" }, [el("h4", null, ["Emails", el("span", { text: "none yet" })]), el("div", { class: "muted small", text: v.gate && !v.gate.allowed ? "Gate: " + v.gate.reasons.map(nice).join("; ") : "No email sent yet." })]));
      } else if (v.mails.length) card.appendChild(emailSection(v));
      if (v.mine.length || !isEmail) {
      const sec = el("div", { class: "sec" }, [el("h4", null, ["Call attempts", el("span", { text: v.mine.length ? plural(v.mine.length, "attempt") : "none yet" })])]);
      if (!v.mine.length) sec.appendChild(el("div", { class: "muted small", text: v.gate && !v.gate.allowed ? "Gate: " + v.gate.reasons.map(nice).join("; ") : "No call placed yet." }));
      v.mine.forEach((a) => {
        const t = transcriptById[a.transcript_id];
        const tStatus = !t ? (a.outcome_reason && a.outcome_reason.includes("malformed") ? status("critical", "transcript malformed") : status("neutral", "no transcript")) : t.delivery_status === "delivered" ? status("good", "transcript delivered") : t.delivery_status === "held_pii_review" ? status("warning", "transcript held for review") : t.delivery_status === "failed" ? status("critical", "transcript delivery failed") : status("neutral", "transcript " + nice(t.delivery_status));
        const sc = scripts.find((x) => x.call_id === a.id);
        const line = el("div", { class: "line" }, [
          el("div", null, [el("b", { text: nice(a.outcome) || "in progress" }), " ", el("span", { class: "when", text: fmtTime(a.started_at) + (a.duration_sec ? " · " + a.duration_sec + "s" : "") + " · script " + a.script_version })]),
          el("div", { class: "tags" }, [a.failure_category ? el("span", { class: "cat", text: a.failure_category }) : null, tStatus]),
        ]);
        if (a.outcome_reason) line.appendChild(el("div", { class: "muted small", text: a.outcome_reason }));
        if (a.callback_requested_at) line.appendChild(el("div", { class: "muted small", text: "Call back requested for " + fmtTime(a.callback_requested_at) }));
        if (a.provider === "vapi") line.appendChild(el("div", { class: "muted small" }, ["Vapi: " + (a.provider_ended_reason || "") + (a.cost_usd ? " · $" + a.cost_usd.toFixed(2) : ""), a.recording_ref && a.recording_ref.startsWith("http") ? [" · ", el("a", { href: a.recording_ref, text: "recording", target: "_blank", rel: "noopener" })] : null].flat().filter(Boolean)));
        if (t && t.utterances && t.utterances.length) line.appendChild(el("details", null, [el("summary", { text: "Transcript, " + plural(t.utterances.length, "line") + (t.pii_flagged ? " · flagged: " + t.pii_reason : "") }), el("ul", { class: "utter" }, t.utterances.map((u) => el("li", { class: u.speaker }, [el("b", { text: u.speaker }), u.text])))]));
        if (sc) line.appendChild(el("details", null, [el("summary", { text: "Script used" }), el("pre", { class: "block", text: sc.first_message + "\n\n— system prompt —\n" + sc.system_prompt })]));
        sec.appendChild(el("div", { class: "attempt" }, [el("div", { class: "num", text: String(a.attempt_number) }), line]));
      });
      card.appendChild(sec);
      }

      if (v.interview) {
        const i = v.interview;
        card.appendChild(el("div", { class: "sec" }, [el("h4", null, ["Conversation", el("span", { text: i.status === "approved" ? "approved" : "awaiting approval" })]),
          el("div", { class: "small" }, [i.scheduled_for ? fmtTime(i.scheduled_for) + " · 30 min · " : "No time fixed yet · ", nice(i.confirmation_email) + " ", i.calendar_event_ref ? el("a", { href: REPO + "/blob/main/" + i.calendar_event_ref.replace(/^file:/, ""), text: "draft .ics", target: "_blank", rel: "noopener" }) : null]),
          hasHandoff(i) ? handoffBlock(i) : null]));
      }
      const n = v.errs.length + v.als.length + v.dl.length;
      const issues = el("div", { class: "sec" }, [el("h4", null, ["Errors and alerts", el("span", { text: n ? plural(v.errs.length, "error") + ", " + plural(v.als.length, "alert") + (v.dl.length ? ", " + v.dl.length + " dead-lettered" : "") : "none" })])]);
      if (!n) issues.appendChild(el("div", { class: "ok", text: "✓ Clean" }));
      v.errs.forEach((e) => issues.appendChild(el("div", { class: "issue error" }, [el("span", { class: "k", text: nice(e.error_type) }), el("span", { text: (e.field ? e.field + ": " : "") + e.detail })])));
      v.als.forEach((a) => issues.appendChild(el("div", { class: "issue alert" }, [el("span", { class: "k", text: nice(a.type) }), el("span", { text: a.detail })])));
      v.dl.forEach((x) => issues.appendChild(el("div", { class: "issue alert" }, [el("span", { class: "k", text: "dead letter" }), el("span", { text: x.last_error + " (" + x.attempts + " attempts)" })])));
      card.appendChild(issues);
      return card;
    }
    let filter = "all", query = "";
    function matches(v) {
      const c = v.c;
      if (query) { const q = query.toLowerCase(); if (!((c.full_name || "").toLowerCase().includes(q) || (c.practice_name || "").toLowerCase().includes(q) || c.external_id === q || c.id === q)) return false; }
      switch (filter) {
        case "booked": return c.status === "BOOKED";
        case "outreach": return c.status === "IN_OUTREACH";
        case "queued": return c.status === "READY";
        case "attention": return v.attention;
        case "closed": return ["DECLINED", "UNREACHABLE", "SUPPRESSED"].includes(c.status);
        default: return true;
      }
    }
    function drawCards() {
      const host = clear($("cards")); const shown = views.filter(matches);
      shown.forEach((v) => host.appendChild(cardFor(v)));
      if (!shown.length) host.appendChild(el("div", { class: "empty", text: "No practices match." }));
      $("cards-count").textContent = shown.length + " of " + plural(views.length, "practice") + " · " + views.filter((v) => v.attention).length + " need attention";
    }
    $("filters").querySelectorAll(".f").forEach((btn) => btn.addEventListener("click", () => { filter = btn.getAttribute("data-filter"); $("filters").querySelectorAll(".f").forEach((b) => b.setAttribute("aria-pressed", String(b === btn))); drawCards(); }));
    $("tracker-search").addEventListener("input", (e) => { query = e.target.value.trim(); drawCards(); });
    drawCards();

    // ---------- gate (blocked and deferred only) ----------
    const kind = (g) => g.allowed ? "allowed" : (g.deferred ? "deferred" : "blocked");
    const held = decisions.filter((g) => g.run_id === lastRun && !g.allowed).sort((a, b) => (a.deferred ? 1 : 0) - (b.deferred ? 1 : 0) || name(a.contact_id).localeCompare(name(b.contact_id)));
    const lr = comp.last_run || {};
    $("gate-count").textContent = (lr.allowed || 0) + " allowed · " + (lr.blocked || 0) + " blocked · " + (lr.deferred || 0) + " deferred";
    clear($("gate")).appendChild(table(["Practice", "Decision", "Why"], held.map((g) => { const k = kind(g); return [name(g.contact_id), status(k === "deferred" ? "warning" : "critical", k), el("div", { class: "reasons" }, (g.reasons || []).map((r) => el("span", { class: "reason", text: r })))]; }), { empty: "Nothing was held back in the last run.", nowrap: [1] }));

    // ---------- collapsed detail ----------
    const merged = Object.assign({}, calls.by_outcome || {});
    Object.entries(em.by_outcome || {}).forEach(([k, v]) => { merged[k] = (merged[k] || 0) + v; });
    const outcomes = Object.entries(merged).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: nice(k), count: v }));
    bars($("outcomes"), outcomes, "outcomes, calls and emails");
    const catCounts = Object.assign({}, calls.by_failure_category || {});
    emailAttempts.forEach((a) => { if (a.failure_category) catCounts[a.failure_category] = (catCounts[a.failure_category] || 0) + 1; });
    const cats = Object.entries(catCounts).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, count: v }));
    bars($("fail-bars"), cats, "failure categories");
    $("outcomes-count").textContent = plural((calls.attempts || 0) + (em.attempts ?? emailAttempts.length), "attempt");
    $("queue-count").textContent = plural(queue.length, "practice");
    clear($("queue")).appendChild(table(["Practice", "Attempts", "Last outcome", "Next attempt"], queue.map((q) => [q.name + (q.external_id ? " · " + q.external_id : ""), q.attempts, nice(q.last_outcome) || "–", q.next_attempt_at ? fmtTime(q.next_attempt_at) : "when the window opens"]), { num: [1], empty: "Queue is empty.", nowrap: [3] }));
    $("interviews-count").textContent = plural(interviews.length, "booked");
    clear($("interviews")).appendChild(table(["Practice", "Scheduled for", "Status", "Draft"], interviews.map((i) => [name(i.contact_id), fmtTime(i.scheduled_for), i.status === "approved" ? status("good", "approved by " + i.approved_by) : status("warning", "awaiting approval"), i.calendar_event_ref ? el("a", { href: REPO + "/blob/main/" + i.calendar_event_ref.replace(/^file:/, ""), text: ".ics", target: "_blank", rel: "noopener" }) : "–"]), { nowrap: [1] }));
    const dStatus = (t) => t.delivery_status === "delivered" ? status("good", "delivered") : t.delivery_status === "held_pii_review" ? status("warning", "held (PII)") : t.delivery_status === "failed" ? status("critical", "failed") : status("neutral", t.delivery_status);
    $("transcripts-count").textContent = (tr.delivered || 0) + " delivered · " + (tr.held_pii_review || 0) + " held · " + (tr.failed || 0) + " failed";
    clear($("transcripts")).appendChild(table(["Practice", "Outcome", "Delivery", "Tries"], transcripts.map((t) => { const a = anyAttempt(t.call_attempt_id) || {}; return [name(t.contact_id), nice(a.outcome), dStatus(t), t.delivery_attempts]; }), { num: [3] }));
    const errRows = errors.slice().reverse().map((e) => [fmtTime(e.timestamp), status("warning", nice(e.error_type)), e.contact_id ? name(e.contact_id) : e.call_id, (e.field ? e.field + ": " : "") + e.detail]);
    const alertRows = alerts.slice().reverse().map((a) => [fmtTime(a.timestamp), status(a.type === "kill_switch_on" || a.type === "gate_escape" ? "critical" : (a.type.includes("failed") ? "serious" : "warning"), nice(a.type)), a.contact_id ? name(a.contact_id) : (a.transcript_id || a.run_id || ""), a.detail]);
    $("errors-count").textContent = plural(errors.length, "error") + " · " + plural(alerts.length, "alert") + " · " + dlq.length + " dead-lettered";
    clear($("errors")).appendChild(table(["Time", "Type", "Practice", "Detail"], alertRows.concat(errRows), { nowrap: [0, 1] }));
    const runs = (d.runs || []).slice().reverse();
    $("runs-count").textContent = plural(runs.length, "run");
    clear($("runs")).appendChild(table(["Run", "Trigger", "Placed", "Blocked", "Deferred", "Delivered", "Escapes"], runs.map((r) => { const rs = r.stages || {}, rc = rs.calls || {}, rd = rs.delivery || {}; return [fmtTime(r.started_at) + (r.simulated_time ? " (sim)" : ""), nice(r.trigger), rc.placed ?? 0, rc.blocked ?? 0, rc.deferred ?? 0, rd.delivered ?? 0, r.gate_escapes ?? 0]; }), { num: [2, 3, 4, 5, 6], nowrap: [0] }));
    const audit = (d.audit_log || []).slice().reverse().slice(0, 100);
    $("audit-count").textContent = "latest 100";
    clear($("audit")).appendChild(table(["Time", "Entity", "Action", "Actor", "Id"], audit.map((e) => [fmtTime(e.timestamp), e.entity, nice(e.action), e.actor, e.entity_id]), { mono: [4], nowrap: [0] }));

    const input = $("search"), hist = $("history");
    function renderHistory(q) {
      q = q.trim().toLowerCase(); clear(hist);
      if (!q) { hist.textContent = "Type to search."; return; }
      const hit = contacts.find((x) => x.id === q || x.external_id === q) || contacts.find((x) => (x.full_name || "").toLowerCase().includes(q) || (x.practice_name || "").toLowerCase().includes(q));
      if (!hit) { hist.textContent = "No practice matches."; return; }
      const kv = el("dl", { class: "kv" });
      [["Name", hit.full_name || "(missing)"], ["Practice", hit.practice_name], ["Specialty", hit.specialty || "(missing)"], ["Segment", nice(hit.segment)], ["Status", hit.status], ["Verification", hit.verification_status + (hit.verification_reason ? " (" + hit.verification_reason + ")" : "")], ["Attempts", hit.attempts], ["Next attempt", hit.next_attempt_at ? fmtTime(hit.next_attempt_at) : "–"], ["Time zone", hit.time_zone], ["Hook", hit.hook || "–"], ["Ids", hit.id + (hit.external_id ? " · " + hit.external_id : "")]]
        .forEach(([k, v]) => { kv.appendChild(el("dt", { text: k })); kv.appendChild(el("dd", { text: String(v ?? "") })); });
      hist.appendChild(kv);
      hist.appendChild(table(["Channel", "Value (masked)", "Line type", "Verified", "Clearance"], (hit.channels || []).map((ch) => [ch.type, ch.value, ch.line_type, ch.verified ? "yes" : "no" + (ch.verification_reason ? " (" + ch.verification_reason + ")" : ""), nice(ch.call_clearance)])));
      hist.appendChild(el("h3", { class: "small", text: "Gate decisions" }));
      hist.appendChild(table(["Run", "Decision", "Reasons"], decisions.filter((g) => g.contact_id === hit.id).reverse().map((g) => [g.run_id, kind(g), (g.reasons || []).join("; ")])));
      hist.appendChild(el("h3", { class: "small", text: "Audit trail" }));
      hist.appendChild(table(["Time", "Entity", "Action", "Change"], (d.audit_log || []).filter((e) => e.entity_id === hit.id || e.contact_id === hit.id).reverse().map((e) => [fmtTime(e.timestamp), e.entity + " " + e.entity_id, nice(e.action), JSON.stringify(e.after || {})]), { nowrap: [0] }));
    }
    input.addEventListener("input", () => renderHistory(input.value));
    const params = new URLSearchParams(location.search);
    if (params.get("contact")) { input.value = params.get("contact"); renderHistory(input.value); input.closest("details").open = true; }

    $("footer").textContent = "Snapshot " + (s.generated_at ? fmtTime(s.generated_at) : "–") + " · run " + (run.run_id || "–") + (newer ? " · a newer run has finished, refresh shortly" : "");
  }

  // ---------- local control service ----------
  async function localState() {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 1500);
    try { const res = await localFetch(LOCAL + "/api/state", { signal: ctl.signal }); clearTimeout(t); return res.ok ? await res.json() : null; }
    catch (e) { clearTimeout(t); return null; }
  }

  // ---------- the agent run from the page (local service) ----------
  const STAGE_NODE = { ingest: [0], verify: [0], call: [1, 2], email: [1, 2], webhooks: [3], replies: [3], deliver: [3] };
  function showRunProgress(run) {
    if (!ring_ctl) return;
    ring_ctl.stop();
    $("ring").classList.add("running");
    const stateEl = $("state"); clear(stateEl); stateEl.className = "state running";
    stateEl.appendChild(el("span", { class: "live on", id: "live" })); stateEl.appendChild(document.createTextNode("Running"));
    const stage = run.stage || "";
    $("since").textContent = stage ? "stage: " + stage : "starting";
    ring_ctl.highlight(STAGE_NODE[stage] || []);
    renderAgentSteps(run);
  }
  function stageCounts(run, key) { const st = run.stages || {}; return (st[key] && st[key].counts) || null; }
  function renderAgentSteps(run) {
    const host = $("agent-steps");
    const feedBits = [$("feed"), $("replay") && $("replay").parentNode, $("now")];
    if (!run || (run.status === "idle" && !run.email)) { host.hidden = true; feedBits.forEach((n) => { if (n) n.hidden = false; }); return; }
    host.hidden = false; clear(host); feedBits.forEach((n) => { if (n) n.hidden = true; });
    const e = run.email || null;
    const ing = stageCounts(run, "ingest"), ver = stageCounts(run, "verify"), em = stageCounts(run, "email") || stageCounts(run, "call");
    const running = run.status === "running";
    const step = (cls, title, detail) => { const li = el("li", { class: cls }, [el("div", { class: "t", text: title })]); (detail || []).forEach((d) => { if (d) li.appendChild(d); }); host.appendChild(li); return li; };
    const stageDone = (k) => (run.stages || {})[k] && (run.stages || {})[k].status === "done";
    // 1. list
    step(stageDone("verify") ? "done" : (running ? "active" : ""), ing ? "Loaded the approved list: " + plural(ing.rows ?? 0, "practice") : "Loading the approved list", ver ? [el("div", { class: "d", text: (ver.verified ?? 0) + " verified" + (ver.suppressed ? ", " + ver.suppressed + " on the do-not-contact list" : "") })] : []);
    // 2. pick + gate
    const picked = e && e.contact_name ? "Picked " + (e.contact_name.startsWith("Dr") ? e.contact_name : "Dr. " + e.contact_name.split(" ").slice(-1)[0]) + ", " + (e.practice || "") : (em ? "Gate: " + (em.allowed ?? 0) + " allowed, " + (em.blocked ?? 0) + " blocked, " + (em.deferred ?? 0) + " deferred" : "Checking the gate");
    step(e ? "done" : (stageDone("verify") && running ? "active" : ""), picked, em && e ? [el("div", { class: "d", text: "Gate: allowed (cleared address, not suppressed)" + (em.blocked ? " · " + em.blocked + " others blocked" : "") })] : []);
    if (e && e.status === "error" && !e.subject) { step("err", "No email could be composed", [el("div", { class: "d", text: e.error || "" })]); return; }
    // 2b. research
    if (e && e.research) {
      const r = e.research;
      step("done", "Researched the practice: " + (r.summary || r.hook || "no details found"), [
        el("div", { class: "d", text: (r.services && r.services.length ? "Services: " + r.services.slice(0, 5).join(", ") + ". " : "") + (r.doctor_bio ? r.doctor_bio + " " : "") + (r.glp1 ? "Already supports GLP-1 patients." : "") }),
        el("div", { class: "d muted", text: (r.source === "web" ? "From the practice website" : "From the seed fixture standing in for a web lookup") + (e.personalization && e.personalization.length ? " · used in the draft: " + e.personalization.join(" · ") : "") }),
      ]);
    } else if (e && e.subject) step("done", "Researched the practice: nothing on file", [el("div", { class: "d muted", text: "No enrichment for this practice; the generic template was used." })]);
    // 3. compose: a draft waits for review
    if (e && e.status === "draft") {
      const li = step("active", "Draft ready for review", [
        el("div", { class: "d subject" }, ["Subject: " + (e.subject || ""), el("span", { class: "variant", text: "variant " + ((e.variant || 0) + 1) + " of 4" })]),
        el("pre", { class: "block draft", text: e.body || "" }),
        e.personalization && e.personalization.length ? el("div", { class: "d muted", text: "Personalised with: " + e.personalization.join(" · ") }) : null,
        el("div", { class: "d muted", text: "Goes to " + (e.to || "the demo inbox") + " for the demo, from your linked account." }),
      ]);
      const send = el("button", { type: "button", class: "btn primary", text: "Send" });
      const regen = el("button", { type: "button", class: "btn", text: "Regenerate" });
      const note = el("span", { class: "count" });
      const act = async (path, busyText) => {
        send.disabled = regen.disabled = true; note.textContent = busyText;
        try {
          const res = await localFetch(LOCAL + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ attempt: e.attempt_id || "" }) });
          const body = await res.json();
          if (!res.ok) { note.textContent = body.message || body.error || "failed"; send.disabled = regen.disabled = false; return; }
          run.email = body.email; renderAgentSteps(run);
          window.dispatchEvent(new CustomEvent("bv-email-changed", { detail: run }));
        } catch (err) { note.textContent = "Could not reach the local service."; send.disabled = regen.disabled = false; }
      };
      send.addEventListener("click", () => act("/api/draft/send", "Sending…"));
      regen.addEventListener("click", () => act("/api/draft/regenerate", "Rewriting…"));
      li.appendChild(el("div", { class: "row" }, [send, regen, note]));
      return;
    }
    // 3. compose
    step(e && e.subject ? "done" : (running ? "active" : ""), e && e.subject ? "Composed the email" : "Composing the email", e && e.subject ? [el("div", { class: "d subject", text: "Subject: " + e.subject }), el("details", null, [el("summary", { text: "Read the email" }), el("pre", { class: "block", text: e.body || "" })])] : []);
    // 4. send
    if (e) {
      const to = e.to ? " to " + e.to : "";
      if (e.status === "queued") step("active", "Queued for Gmail", [el("div", { class: "d", text: "Claude sends it from your linked Gmail account" + to + (e.redirected_to ? " (the practice's copy is redirected to your inbox for the demo)" : "") })]);
      else if (e.status === "error") step("err", "Send failed", [el("div", { class: "d", text: e.error || "" })]);
      else {
        const link = e.thread_id ? el("a", { href: "https://mail.google.com/mail/u/0/#all/" + encodeURIComponent(e.thread_id), text: "open in Gmail", target: "_blank", rel: "noopener" }) : null;
        step("done", "Sent via " + (e.transport === "smtp" ? "SMTP" : "Gmail") + (e.sent_at ? " at " + fmtTime(e.sent_at) : ""), [el("div", { class: "d" }, ["Delivered" + to + (e.redirected_to ? " (redirected for the demo)" : "") + " ", link])]);
      }
      // 5. reply
      if (e.status === "sent") {
        const li = step("active", "Waiting for a reply…", [el("div", { class: "d", text: "Replies are read from the linked inbox. If you answered elsewhere, paste the reply here:" })]);
        const ta = el("textarea", { rows: "2", placeholder: "e.g. Yes, Tuesday afternoons work. Send the invite to this address." });
        const btn = el("button", { type: "button", class: "btn", text: "Record reply" });
        const note = el("span", { class: "count" });
        btn.addEventListener("click", async () => {
          const text = ta.value.trim(); if (!text) { note.textContent = "Paste the reply first."; return; }
          btn.disabled = true; note.textContent = "Classifying…";
          try {
            const res = await localFetch(LOCAL + "/api/reply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, attempt: e.attempt_id || "" }) });
            const body = await res.json();
            note.textContent = res.ok ? "Recorded" : (body.message || body.error || "could not record");
          } catch (err) { note.textContent = "Could not reach the local service."; }
          btn.disabled = false;
        });
        li.appendChild(ta); li.appendChild(el("div", { class: "row" }, [btn, note]));
      } else if (e.status === "replied" && e.reply) {
        const r = e.reply, h = r.handoff || {};
        const first = (r.text || "").split("\n")[0];
        const handoff = [h.contact_email, h.contact_method && h.contact_method !== "unknown" ? nice(h.contact_method) + (h.contact_number ? " " + h.contact_number : "") : "", h.availability].filter(Boolean).join(" · ");
        step(r.outcome === "DO_NOT_CONTACT" || r.outcome === "DECLINED" ? "err" : "done", "Reply received" + (r.at ? " " + fmtTime(r.at) : ""), [el("div", { class: "reply-quote", text: first }), el("div", { class: "d" }, [el("b", { text: "Outcome: " }), nice(r.outcome) || "needs review", handoff ? " · handoff: " + handoff : ""])]);
      }
    }
  }
  function wireRun(local) {
    const btn = $("run-btn"), link = $("run-link");
    if (!local) return;
    link.hidden = true; btn.hidden = false;
    let poll = null, slow = null, lastEmailStatus = null;
    const refreshData = async () => { const d2 = await loadAll(LOCAL + "/data/"); if (d2.summary) { render(d2, null); } };
    const watchEmail = (run) => {
      // keep polling while the email is queued or awaiting a reply, so the step list follows the inbox
      const st = run.email ? run.email.status : "";
      if (st !== lastEmailStatus) { lastEmailStatus = st; if (st) refreshData().then(() => renderAgentSteps(run)); }
      if ((st === "queued" || st === "sent") && !slow) slow = setInterval(async () => { const s2 = await localState(); if (s2 && s2.run) watchEmail(s2.run); }, 2000);
      if (st !== "queued" && st !== "sent" && slow) { clearInterval(slow); slow = null; }
    };
    const finish = async (run) => {
      if (poll) { clearInterval(poll); poll = null; }
      btn.disabled = false; btn.textContent = "Run the agent again";
      $("ring").classList.remove("running");
      if (run.status === "error") { $("error").textContent = "Run failed: " + (run.error || "unknown error"); if (ring_ctl) ring_ctl.settle(); renderAgentSteps(run); return; }
      await refreshData();
      if (ring_ctl) ring_ctl.highlight([3]);
      renderAgentSteps(run);
      watchEmail(run);
    };
    const tick = async () => {
      const st = await localState(); if (!st || !st.run) return;
      const run = st.run;
      if (run.status === "running") showRunProgress(run); else if (run.status === "done" || run.status === "error") finish(run);
    };
    window.addEventListener("bv-email-changed", (ev) => watchEmail(ev.detail));
    if (local.run && local.run.email && local.run.status !== "running") { renderAgentSteps(local.run); watchEmail(local.run); }  // a draft or sent email survives a reload
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "Running…"; $("error").textContent = ""; lastEmailStatus = null;
      try {
        const res = await localFetch(LOCAL + "/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
        const body = await res.json();
        if (!res.ok) { $("error").textContent = body.message || body.error || "could not start"; btn.disabled = false; btn.textContent = "Run the agent"; return; }
      } catch (e) { $("error").textContent = "Could not reach the local service."; btn.disabled = false; btn.textContent = "Run the agent"; return; }
      poll = setInterval(tick, 1000);
      tick();
    });
    if (local.run && local.run.status === "running") { btn.disabled = true; btn.textContent = "Running…"; poll = setInterval(tick, 1000); }
    else if (local.run && (local.run.status === "done" || local.run.status === "error")) { renderAgentSteps(local.run); watchEmail(local.run); if (local.run.status === "done") btn.textContent = "Run the agent again"; }
  }

  localState().then((local) => Promise.all([loadAll(local ? LOCAL + "/data/" : SRC), loadLiveRun()]).then(([d, live]) => [d, live, local])).then(([d, live, local]) => {
    if (!d.summary) {
      const err = clear($("error"));
      err.appendChild(document.createTextNode(local ? "The local service answered but has no data yet. Press Run the agent." : "No pipeline data yet. Start `python -m infra serve` on the machine with the Gmail link to run the agent from this page, or open the local console: "));
      if (!local) err.appendChild(el("a", { href: LOCAL_CONSOLE, text: LOCAL_CONSOLE }));
      if (!local) return;
    }
    if (local && local.sheet) live = Object.assign({}, live || {}, { __sheet: local.sheet });
    if (d.summary) render(d, live);
    wireRun(local);
    if (local && local.sheet) {
      let seenImport = local.sheet.last_import || "";
      setInterval(async () => { const st = await localState(); if (!st || !st.sheet) return; if ((st.sheet.last_import || "") !== seenImport && !(st.run && st.run.status === "running")) { seenImport = st.sheet.last_import || ""; location.reload(); } }, 3000);
    }
    setInterval(async () => { const live2 = await loadLiveRun(); const running = live2 && (live2.status === "in_progress" || live2.status === "queued"); const was = $("ring").classList.contains("running"); if (running !== was && !local) location.reload(); }, 60000);
  }).catch((e) => { $("error").textContent = "Failed to render: " + e.message; console.error(e); });
})();
