/* Between Visits · Clinic Reach-Out Agent tracker.
   Reads the masked JSON snapshot in data/ (written by `python -m infra run`).
   Everything from the JSON is inserted with textContent; nothing is interpreted as HTML. */
(function () {
  "use strict";

  const REPO = "https://github.com/UmaSanskriti/between-visits-workflow";
  const FILES = ["run", "summary", "contacts", "call_attempts", "scripts", "transcripts", "interviews",
    "suppressions", "gate_decisions", "errors", "dlq", "alerts", "runs", "audit_log", "brain_inbox"];
  const ICON = { good: "✓", warning: "!", serious: "▲", critical: "✕", neutral: "·", brand: "●" };
  const TZ = "America/New_York";
  const REACHED = new Set(["BOOKED", "DECLINED", "CALLBACK_REQUESTED", "GATEKEEPER_BLOCKED", "DO_NOT_CONTACT", "NOT_A_FIT", "DECLINED_RECORDING"]);
  const LOCKED_OPEN = "AI assistant calling for Anirudh Bagla";
  // From the brief, section 5: what the reviewer changes for each failure category.
  const WHAT_CHANGES = {
    "No answer / voicemail": "Call time and day; the voicemail message.",
    "Stuck in phone tree": "Menu path; ask for the office manager; call time.",
    "Hung up at AI disclosure": "How the opener is delivered, never the disclosure itself.",
    "Screened out by front desk": "Opener; how the ask is framed; who it asks for.",
    "Pitch did not land": "Value proposition, proof points, order of the pitch.",
    "Interested, no time agreed": "Slots offered; call vs visit; time of day.",
    "Call back later": "Retry at that time; no script change.",
    "Not a fit": "Remove the practice; tighten the list filter.",
    "Do not contact": "Suppressed on every channel, permanently.",
    "Wrong number (list data)": "Fix the list; the number is marked failed.",
    "Technical failure": "Provider retry after an hour; alert if it repeats.",
  };

  // ---------- DOM helpers ----------
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
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
    if (!rows.length) return el("div", { class: "empty", text: opts.empty || "Nothing yet." });
    const thead = el("thead", null, [el("tr", null, headers.map((h, i) => el("th", { class: (opts.num || []).includes(i) ? "num" : "" }, [h])))]);
    const tbody = el("tbody", null, rows.map((r) => el("tr", null, r.map((cell, i) => {
      const td = el("td", { class: ((opts.num || []).includes(i) ? "num " : "") + ((opts.mono || []).includes(i) ? "mono " : "") + ((opts.nowrap || []).includes(i) ? "nowrap" : "") });
      if (cell instanceof Node) td.appendChild(cell); else td.textContent = cell == null ? "" : String(cell);
      return td;
    }))));
    return el("table", null, [thead, tbody]);
  }
  function fmtTime(iso, withDate) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const opts = { timeZone: TZ, hour: "numeric", minute: "2-digit" };
    if (withDate !== false) { opts.weekday = "short"; opts.month = "short"; opts.day = "numeric"; }
    return d.toLocaleString("en-US", opts) + " ET";
  }
  function dayKey(iso) { return iso ? new Date(iso).toLocaleDateString("en-CA", { timeZone: TZ }) : ""; }
  function pct(x) { return x == null || isNaN(x) ? "–" : Math.round(x * 100) + "%"; }
  function nice(s) { return String(s || "").replace(/_/g, " ").toLowerCase(); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }

  // ---------- tooltip + bars ----------
  const tip = $("tooltip");
  function showTip(evt, label, value, extra) {
    clear(tip);
    tip.appendChild(el("div", null, [el("span", { class: "k" }), el("b", { text: String(value) }), " ", el("span", { class: "muted", text: label })]));
    if (extra) tip.appendChild(el("div", { class: "muted small", text: extra }));
    tip.style.display = "block"; moveTip(evt);
  }
  function moveTip(evt) {
    const r = evt.target && evt.target.getBoundingClientRect ? evt.target.getBoundingClientRect() : { right: 0, top: 0 };
    const x = evt.clientX != null ? evt.clientX : r.right, y = evt.clientY != null ? evt.clientY : r.top;
    tip.style.left = Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = Math.max(8, y - tip.offsetHeight - 10) + "px";
  }
  function hideTip() { tip.style.display = "none"; }
  function bars(container, items) {
    clear(container);
    const max = Math.max(1, ...items.map((i) => i.count));
    items.forEach((item) => {
      container.appendChild(el("div", { class: "lbl", text: item.label, title: item.label }));
      const bar = el("div", { class: "bar", tabindex: "0", role: "img", "aria-label": item.label + ": " + item.count });
      bar.style.width = Math.max(0.4, (item.count / max) * 100) + "%";
      const over = (e) => showTip(e, item.label, item.count, item.extra);
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
  async function loadAll() {
    const stamp = Date.now(), out = {};
    await Promise.all(FILES.map(async (name) => {
      try { const res = await fetch("data/" + name + ".json?t=" + stamp, { cache: "no-store" }); out[name] = res.ok ? await res.json() : null; }
      catch (e) { out[name] = null; }
    }));
    return out;
  }

  // ---------- derived per-contact view ----------
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
      return interview.status === "approved" ? ["good", "Conversation " + when + ", invite approved by " + interview.approved_by] : ["warning", "Conversation " + when + ", invite drafted and waiting for Anirudh's approval"];
    }
    if (c.status === "IN_OUTREACH") return ["brand", c.next_attempt_at ? "Retry " + fmtTime(c.next_attempt_at) + (c.last_outcome === "CALLBACK_REQUESTED" ? " (they asked for a call back)" : "") : "Call in progress"];
    if (c.status === "READY") return ["neutral", gate && !gate.allowed ? (gate.deferred ? "Waiting: " + gate.reasons.map(nice).join(", ") : "Blocked: " + gate.reasons.map(nice).join(", ")) : "Call when the window opens"];
    if (c.status === "SUPPRESSED") return ["critical", "Never contact again, on any channel"];
    if (c.status === "UNREACHABLE") return ["warning", "No more attempts (cap reached)"];
    if (c.status === "DECLINED") return ["neutral", c.last_outcome === "NOT_A_FIT" ? "Closed: not a fit, remove from the list" : "Closed: no retry"];
    if (c.duplicate_of) return ["warning", "Looks like a duplicate of another practice, review and merge"];
    if (c.verification_reason) return ["warning", "Cannot call: " + nice(c.verification_reason)];
    return ["neutral", "Awaiting verification"];
  }

  // ---------- render ----------
  function render(d) {
    const run = d.run || {}, s = d.summary || {};
    const contacts = d.contacts || [], attempts = d.call_attempts || [], transcripts = d.transcripts || [];
    const interviews = d.interviews || [], errors = d.errors || [], alerts = d.alerts || [], dlq = d.dlq || [];
    const decisions = d.gate_decisions || [], scripts = d.scripts || [];
    const byId = Object.fromEntries(contacts.map((c) => [c.id, c]));
    const attemptById = Object.fromEntries(attempts.map((a) => [a.id, a]));
    const transcriptById = Object.fromEntries(transcripts.map((t) => [t.id, t]));
    const name = (cid) => { const c = byId[cid]; return c ? (c.full_name || "(no name)") + (c.external_id ? " · " + c.external_id : "") : cid; };
    const calls = s.calls || {}, tr = s.transcripts || {}, comp = s.compliance || {}, cs = s.contacts || {};
    const completed = attempts.filter((a) => a.outcome);
    const reached = completed.filter((a) => REACHED.has(a.outcome)).length;
    const bookedContacts = contacts.filter((c) => c.status === "BOOKED").length;
    const calledContacts = new Set(attempts.map((a) => a.contact_id)).size;

    // header chips
    const chips = clear($("run-chips"));
    chips.appendChild(el("span", { class: "chip mode" }, [el("b", { text: "DRY RUN" })]));
    chips.appendChild(el("span", { class: "chip" }, ["Last run ", el("b", { text: run.finished_at ? fmtTime(run.finished_at) : "never" })]));
    if (run.simulated_time) chips.appendChild(el("span", { class: "chip warning" }, [el("span", { class: "dot" }), "simulated clock ", el("b", { text: fmtTime(run.now_used) })]));
    chips.appendChild(el("span", { class: "chip" }, ["Trigger ", el("b", { text: run.trigger || "–" })]));
    if (run.git_sha) chips.appendChild(el("span", { class: "chip" }, ["Commit ", el("a", { href: REPO + "/commit/" + run.git_sha, text: run.git_sha, target: "_blank", rel: "noopener" })]));
    chips.appendChild(el("span", { class: "chip " + (run.kill_switch ? "critical" : "good") }, [el("span", { class: "dot" }), "Kill switch ", el("b", { text: run.kill_switch ? "ON" : "off" })]));
    if (run.paused) chips.appendChild(el("span", { class: "chip warning" }, [el("span", { class: "dot" }), "Calling paused"]));
    if (run.brain_down) chips.appendChild(el("span", { class: "chip serious" }, [el("span", { class: "dot" }), "Brain simulated down"]));
    if (run.callback_number_set === false) chips.appendChild(el("span", { class: "chip critical" }, [el("span", { class: "dot" }), "Callback number not set"]));

    // hero
    $("hero-figure").textContent = "";
    $("hero-figure").appendChild(document.createTextNode(String(bookedContacts)));
    $("hero-figure").appendChild(el("small", { text: bookedContacts === 1 ? "conversation booked" : "conversations booked" }));
    $("hero-sub").textContent = plural(contacts.length, "practice") + " on the list · " + calledContacts + " called · " + reached + " reached a person";
    const versions = Object.keys(calls.by_script_version || {});
    $("phase").textContent = "Discovery phase · script " + (versions.length ? versions.join(", ") : "v1") + " · playbook v1";

    const today = dayKey(run.now_used || run.finished_at);
    const todayCalls = attempts.filter((a) => dayKey(a.started_at) === today).length;
    const cap = (run.limits && run.limits.max_calls_per_day) || 10;
    $("batch-fill").style.width = Math.min(100, (todayCalls / cap) * 100) + "%";
    $("batch-label").textContent = ""; $("batch-label").appendChild(el("b", { text: String(todayCalls) })); $("batch-label").appendChild(document.createTextNode(" calls placed " + (run.simulated_time ? "on the simulated day" : "today")));
    $("batch-cap").textContent = "cap " + cap + " / day";

    const locked = scripts.filter((sc) => (sc.first_message || "").includes(LOCKED_OPEN)).length;
    const trio = clear($("trio"));
    [[pct(completed.length ? reached / completed.length : null), "reached a person"], [pct(attempts.length ? (calls.booked || 0) / attempts.length : null), "booked, per call"], [scripts.length ? locked + "/" + scripts.length : "–", "calls with locked lines"]]
      .forEach(([v, l]) => trio.appendChild(el("div", { class: "t" }, [el("div", { class: "v", text: v }), el("div", { class: "l", text: l })])));

    // the loop
    const steps = clear($("loop-steps"));
    const categorised = completed.filter((a) => a.failure_category).length;
    const retries = contacts.filter((c) => c.status === "IN_OUTREACH" && c.attempts > 0).length;
    const pendingApproval = interviews.filter((i) => i.status === "draft_pending_approval").length;
    const hooks = contacts.filter((c) => c.hook).length;
    const step = (n, title, count, small, detail, cls) => steps.appendChild(el("div", { class: "step " + (cls || "") }, [
      el("div", { class: "n", text: n }), el("div", { class: "t", text: title }),
      el("div", { class: "c" }, [String(count), small ? el("small", { text: small }) : null]), el("div", { class: "d", text: detail })]));
    step("1 · Anirudh signs off", "Approved list", contacts.length, "practices", (cs.verified || 0) + " verified, " + (comp.suppressions || 0) + " on the do-not-contact list");
    step("2 · main line + a hook", "Research", hooks, "with a hook", (contacts.length - hooks) + " still generic; slots loaded for every call");
    step("3 · disclosed, recorded", "AI phone call", attempts.length, "calls", calledContacts + " practices · " + reached + " reached a person");
    step("4 · decision", "Booked?", calls.booked || 0, "yes", (completed.length - (calls.booked || 0)) + " not booked, " + categorised + " categorised", "decision");
    step("5 · to doctor + Anirudh", "Calendar invite", interviews.length, "drafted", pendingApproval ? pendingApproval + " awaiting approval" : "all approved", "booked");
    step("6 · new time, pitch or both", "Reviewer → script v(n+1)", retries, "retries queued", "script " + (versions.length ? versions[versions.length - 1] : "v1") + " in use; v2 after the first review");
    $("loop-back").textContent = "Every call that doesn't book goes to the reviewer; the fix Anirudh approves powers the next call. Nothing goes out without him.";

    // KPIs
    const kp = clear($("kpis"));
    const tile = (label, value, kind, note) => kp.appendChild(el("div", { class: "tile" }, [
      el("div", { class: "label", text: label }), el("div", { class: "value", text: value }),
      el("div", { class: "note " + (kind || "") }, kind && kind !== "plain" ? [el("span", { text: ICON[kind] }), note] : [note || ""])]));
    tile("In queue", (s.queue || []).length, "plain", "ready or in outreach");
    tile("Transcripts to Brain", tr.delivery_rate == null ? "–" : pct(tr.delivery_rate), tr.held_pii_review ? "warning" : "plain", (tr.delivered || 0) + "/" + ((tr.total || 0) - (tr.held_pii_review || 0)) + (tr.held_pii_review ? ", " + tr.held_pii_review + " held" : " delivered"));
    tile("Gate escapes", comp.gate_escapes || 0, (comp.gate_escapes || 0) === 0 ? "good" : "critical", (comp.gate_escapes || 0) === 0 ? "none, as required" : "calls past a failed gate");
    tile("Dead letters", s.dlq || 0, (s.dlq || 0) === 0 ? "good" : "serious", (s.dlq || 0) === 0 ? "queue empty" : "needs redrive");
    tile("Errors / alerts", (s.errors || 0) + " / " + (s.alerts || 0), (s.alerts || 0) ? "warning" : "plain", "see the cards below");
    tile("Awaiting approval", pendingApproval, pendingApproval ? "warning" : "good", pendingApproval ? "invites held for Anirudh" : "nothing waiting");

    // ---------- practice tracker cards ----------
    const lastRun = run.run_id;
    const latestGate = {};
    decisions.forEach((g) => { if (!latestGate[g.contact_id] || g.run_id >= latestGate[g.contact_id].run_id) latestGate[g.contact_id] = g; });
    const errorsFor = (c) => errors.filter((e) => e.contact_id === c.id || (e.call_id && attemptById[e.call_id] && attemptById[e.call_id].contact_id === c.id));
    const alertsFor = (c) => alerts.filter((a) => a.contact_id === c.id || (a.call_attempt_id && attemptById[a.call_attempt_id] && attemptById[a.call_attempt_id].contact_id === c.id) || (a.transcript_id && transcriptById[a.transcript_id] && transcriptById[a.transcript_id].contact_id === c.id));
    const dlqFor = (c) => dlq.filter((x) => transcriptById[x.transcript_id] && transcriptById[x.transcript_id].contact_id === c.id);
    const order = { BOOKED: 0, IN_OUTREACH: 1, READY: 2, VERIFIED: 3, SOURCED: 3, NEW: 3, UNREACHABLE: 4, DECLINED: 5, SUPPRESSED: 6 };
    const sorted = contacts.slice().sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || (a.full_name || "zz").localeCompare(b.full_name || "zz"));
    const views = sorted.map((c) => {
      const mine = attempts.filter((a) => a.contact_id === c.id).sort((a, b) => a.attempt_number - b.attempt_number);
      const interview = interviews.find((i) => i.contact_id === c.id);
      const errs = errorsFor(c), als = alertsFor(c), dl = dlqFor(c);
      const held = mine.some((a) => { const t = transcriptById[a.transcript_id]; return t && (t.delivery_status === "held_pii_review" || t.delivery_status === "failed"); });
      const attention = errs.length > 0 || als.length > 0 || dl.length > 0 || held || (interview && interview.status === "draft_pending_approval") || ["VERIFIED", "SOURCED", "NEW"].includes(c.status);
      return { c, mine, interview, errs, als, dl, attention, gate: latestGate[c.id] };
    });

    function cardFor(v) {
      const c = v.c, [tone, label] = contactTone(c);
      const card = el("article", { class: "pcard", "data-tone": tone });
      card.appendChild(el("div", { class: "ph" }, [
        el("div", null, [el("h3", { text: c.full_name || "(name missing)" }), el("div", { class: "practice", text: c.practice_name || "(practice missing)" }), el("div", { class: "ext", text: (c.external_id ? c.external_id + " · " : "") + c.id })]),
        pill(tone === "neutral" ? "" : tone, label)]));
      const meta = el("div", { class: "meta" });
      [c.specialty && nice(c.specialty), c.practice_size && nice(c.practice_size), c.city && c.city + (c.state ? ", " + c.state : ""), c.segment && "segment: " + nice(c.segment)].filter(Boolean).forEach((m) => meta.appendChild(el("span", { class: "m", text: m })));
      card.appendChild(meta);
      if (c.hook) card.appendChild(el("div", { class: "hook", text: "Hook: " + c.hook }));

      const [nk, ntext] = nextStep(c, v.interview, v.gate);
      card.appendChild(el("div", { class: "next " + (nk === "brand" || nk === "neutral" ? "" : nk) }, [el("b", { text: "Next: " }), ntext]));

      // attempts
      const sec = el("div", { class: "sec" }, [el("h4", null, ["Call attempts", el("span", { text: v.mine.length ? plural(v.mine.length, "attempt") : "none yet" })])]);
      if (!v.mine.length) sec.appendChild(el("div", { class: "muted small", text: v.gate && !v.gate.allowed ? "Last gate decision: " + v.gate.reasons.map(nice).join("; ") : "No call placed yet." }));
      v.mine.forEach((a) => {
        const t = transcriptById[a.transcript_id];
        const tStatus = !t ? (a.outcome_reason && a.outcome_reason.includes("malformed") ? status("critical", "transcript malformed") : status("neutral", "no transcript")) : t.delivery_status === "delivered" ? status("good", "transcript delivered to Brain") : t.delivery_status === "held_pii_review" ? status("warning", "transcript held for review") : t.delivery_status === "failed" ? status("critical", "transcript delivery failed") : status("neutral", "transcript " + nice(t.delivery_status));
        const sc = scripts.find((x) => x.call_id === a.id);
        const line = el("div", { class: "line" }, [
          el("div", null, [el("b", { text: nice(a.outcome) || "in progress" }), " ", el("span", { class: "when", text: fmtTime(a.started_at) + (a.duration_sec ? " · " + a.duration_sec + "s" : "") + " · script " + a.script_version })]),
          el("div", { class: "tags" }, [a.failure_category ? el("span", { class: "cat", text: a.failure_category }) : null, tStatus]),
        ]);
        if (a.outcome_reason) line.appendChild(el("div", { class: "muted small", text: a.outcome_reason }));
        if (a.callback_requested_at) line.appendChild(el("div", { class: "muted small", text: "Call back requested for " + fmtTime(a.callback_requested_at) }));
        if (t && t.utterances && t.utterances.length) line.appendChild(el("details", null, [el("summary", { text: "Transcript, " + plural(t.utterances.length, "line") + (t.pii_flagged ? " · flagged: " + t.pii_reason : "") }), el("ul", { class: "utter" }, t.utterances.map((u) => el("li", { class: u.speaker }, [el("b", { text: u.speaker }), u.text])))]));
        if (sc) line.appendChild(el("details", null, [el("summary", { text: "Script sent to the agent" }), el("pre", { class: "block", text: sc.first_message + "\n\n— system prompt —\n" + sc.system_prompt })]));
        sec.appendChild(el("div", { class: "attempt" }, [el("div", { class: "num", text: String(a.attempt_number) }), line]));
      });
      card.appendChild(sec);

      // booking
      if (v.interview) {
        const i = v.interview;
        card.appendChild(el("div", { class: "sec" }, [el("h4", null, ["Conversation", el("span", { text: i.status === "approved" ? "approved" : "awaiting approval" })]),
          el("div", { class: "small" }, [fmtTime(i.scheduled_for) + " · 30 min · " + nice(i.confirmation_email) + " ", i.calendar_event_ref ? el("a", { href: REPO + "/blob/main/" + i.calendar_event_ref.replace(/^file:/, ""), text: "draft .ics", target: "_blank", rel: "noopener" }) : null])]));
      }

      // errors + alerts
      const issues = el("div", { class: "sec" }, [el("h4", null, ["Error report and alerts", el("span", { text: (v.errs.length + v.als.length + v.dl.length) ? plural(v.errs.length, "error") + ", " + plural(v.als.length, "alert") + (v.dl.length ? ", " + v.dl.length + " dead-lettered" : "") : "clean" })])]);
      if (!(v.errs.length + v.als.length + v.dl.length)) issues.appendChild(el("div", { class: "ok", text: "✓ Nothing in the error report or the alerts for this practice." }));
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
      const host = clear($("cards"));
      const shown = views.filter(matches);
      shown.forEach((v) => host.appendChild(cardFor(v)));
      if (!shown.length) host.appendChild(el("div", { class: "empty", text: "No practices match." }));
      $("cards-count").textContent = shown.length + " of " + plural(views.length, "practice") + " · " + views.filter((v) => v.attention).length + " need attention";
    }
    $("filters").querySelectorAll(".f").forEach((btn) => btn.addEventListener("click", () => {
      filter = btn.getAttribute("data-filter");
      $("filters").querySelectorAll(".f").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      drawCards();
    }));
    $("tracker-search").addEventListener("input", (e) => { query = e.target.value.trim(); drawCards(); });
    drawCards();

    // ---------- learning ----------
    const cats = Object.entries(calls.by_failure_category || {}).sort((a, b) => b[1] - a[1]);
    bars($("fail-bars"), cats.map(([k, v]) => ({ label: k, count: v, extra: WHAT_CHANGES[k] })));
    $("fail-count").textContent = plural(cats.reduce((n, [, v]) => n + v, 0), "call") + " categorised";
    const what = clear($("fail-what"));
    cats.forEach(([k]) => { if (WHAT_CHANGES[k]) what.appendChild(el("div", { class: "w" }, [el("span", { class: "k", text: k }), el("span", { class: "v", text: WHAT_CHANGES[k] })])); });

    const byVersion = {};
    attempts.forEach((a) => { const k = a.script_version || "(none)"; byVersion[k] = byVersion[k] || { calls: 0, reached: 0, booked: 0, cats: {} }; const v = byVersion[k]; v.calls++; if (REACHED.has(a.outcome)) v.reached++; if (a.outcome === "BOOKED") v.booked++; if (a.failure_category) v.cats[a.failure_category] = (v.cats[a.failure_category] || 0) + 1; });
    clear($("versions")).appendChild(table(["Script", "Calls", "Reached a person", "Booked", "Top category"], Object.entries(byVersion).map(([k, v]) => {
      const top = Object.entries(v.cats).sort((a, b) => b[1] - a[1])[0];
      return [k, v.calls, pct(v.calls ? v.reached / v.calls : null), pct(v.calls ? v.booked / v.calls : null), top ? top[0] + " (" + top[1] + ")" : "–"];
    }), { num: [1, 2, 3], empty: "No calls yet." }));

    const outcomes = Object.entries(calls.by_outcome || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: nice(k), count: v }));
    bars($("outcomes"), outcomes);
    $("outcomes-count").textContent = plural(calls.attempts || 0, "attempt");

    // ---------- compliance ----------
    const lastDecisions = decisions.filter((g) => g.run_id === lastRun);
    const kind = (g) => g.allowed ? "allowed" : (g.deferred ? "deferred" : "blocked");
    const ord = { blocked: 0, deferred: 1, allowed: 2 };
    lastDecisions.sort((a, b) => ord[kind(a)] - ord[kind(b)] || name(a.contact_id).localeCompare(name(b.contact_id)));
    const lr = comp.last_run || {};
    $("gate-count").textContent = (lr.allowed || 0) + " allowed · " + (lr.blocked || 0) + " blocked · " + (lr.deferred || 0) + " deferred";
    clear($("gate")).appendChild(table(["Practice", "Decision", "Reasons"], lastDecisions.map((g) => { const k = kind(g); return [name(g.contact_id), status(k === "allowed" ? "good" : (k === "deferred" ? "warning" : "critical"), k), el("div", { class: "reasons" }, (g.reasons || []).map((r) => el("span", { class: "reason", text: r })))]; }), { empty: "No gate decisions in the last run.", nowrap: [1] }));
    bars($("funnel"), (s.funnel || []).map((f) => ({ label: f.stage, count: f.count })));

    // ---------- operations detail ----------
    const queue = s.queue || [];
    $("queue-count").textContent = plural(queue.length, "practice");
    clear($("queue")).appendChild(table(["Practice", "Segment", "Attempts", "Last outcome", "Next attempt"], queue.map((q) => [q.name + (q.external_id ? " · " + q.external_id : ""), nice(q.segment), q.attempts, nice(q.last_outcome) || "–", q.next_attempt_at ? fmtTime(q.next_attempt_at) : "when the window opens"]), { num: [2], empty: "Queue is empty.", nowrap: [4] }));
    $("interviews-count").textContent = plural(interviews.length, "booked");
    clear($("interviews")).appendChild(table(["Practice", "Scheduled for", "Status", "Confirmation", "Draft"], interviews.map((i) => [name(i.contact_id), fmtTime(i.scheduled_for), i.status === "approved" ? status("good", "approved by " + i.approved_by) : status("warning", "draft, pending approval"), nice(i.confirmation_email), i.calendar_event_ref ? el("a", { href: REPO + "/blob/main/" + i.calendar_event_ref.replace(/^file:/, ""), text: ".ics", target: "_blank", rel: "noopener" }) : "–"]), { nowrap: [1] }));
    const dStatus = (t) => !t ? status("neutral", "none") : t.delivery_status === "delivered" ? status("good", "delivered") : t.delivery_status === "held_pii_review" ? status("warning", "held (PII)") : t.delivery_status === "failed" ? status("critical", "failed") : status("neutral", t.delivery_status);
    $("transcripts-count").textContent = (tr.delivered || 0) + " delivered · " + (tr.held_pii_review || 0) + " held · " + (tr.failed || 0) + " failed · Brain inbox " + (tr.brain_inbox || 0);
    clear($("transcripts")).appendChild(table(["Practice", "Outcome", "Delivery", "Tries", "Lines"], transcripts.map((t) => { const a = attemptById[t.call_attempt_id] || {}; return [name(t.contact_id), nice(a.outcome), dStatus(t), t.delivery_attempts, t.utterances.length ? el("details", null, [el("summary", { text: plural(t.utterances.length, "line") + (t.pii_flagged ? " · flagged: " + t.pii_reason : "") }), el("ul", { class: "utter" }, t.utterances.map((u) => el("li", { class: u.speaker }, [el("b", { text: u.speaker }), u.text])))]) : el("span", { class: "muted", text: "empty (no answer)" })]; }), { num: [3] }));
    const errRows = errors.slice().reverse();
    $("errors-count").textContent = plural(errRows.length, "entry", "entries");
    clear($("errors")).appendChild(table(["Time", "Type", "Practice", "Field", "Detail"], errRows.map((e) => [fmtTime(e.timestamp), nice(e.error_type), e.contact_id ? name(e.contact_id) : e.call_id, e.field, e.detail]), { nowrap: [0] }));
    const alertRows = alerts.slice().reverse().map((a) => [fmtTime(a.timestamp), status(a.type === "kill_switch_on" || a.type === "gate_escape" ? "critical" : (a.type.includes("failed") ? "serious" : "warning"), nice(a.type)), a.detail]);
    const dlqRows = dlq.map((x) => [fmtTime(x.timestamp), status("serious", "dead letter"), x.transcript_id + ": " + x.last_error + " (" + x.attempts + " attempts)"]);
    $("alerts-count").textContent = plural(alertRows.length, "alert") + " · " + dlqRows.length + " in DLQ";
    clear($("alerts")).appendChild(table(["Time", "Type", "Detail"], dlqRows.concat(alertRows), { nowrap: [0, 1] }));
    const runs = (d.runs || []).slice().reverse();
    $("runs-count").textContent = plural(runs.length, "run");
    clear($("runs")).appendChild(table(["Run", "Trigger", "Placed", "Blocked", "Deferred", "Delivered", "Escapes"], runs.map((r) => { const st = r.stages || {}, cl = st.calls || {}, dl = st.delivery || {}; return [fmtTime(r.started_at) + (r.simulated_time ? " (sim)" : ""), r.trigger, cl.placed ?? 0, cl.blocked ?? 0, cl.deferred ?? 0, dl.delivered ?? 0, r.gate_escapes ?? 0]; }), { num: [2, 3, 4, 5, 6], nowrap: [0] }));
    const audit = (d.audit_log || []).slice().reverse().slice(0, 100);
    $("audit-count").textContent = (d.audit_log || []).length + " in the latest export";
    clear($("audit")).appendChild(table(["Time", "Entity", "Action", "Actor", "Id"], audit.map((e) => [fmtTime(e.timestamp), e.entity, nice(e.action), e.actor, e.entity_id]), { mono: [4], nowrap: [0] }));

    // contact lookup
    const input = $("search"), hist = $("history");
    function renderHistory(q) {
      q = q.trim().toLowerCase(); clear(hist);
      if (!q) { hist.textContent = "Type to search."; return; }
      const hit = contacts.find((x) => x.id === q || x.external_id === q) || contacts.find((x) => (x.full_name || "").toLowerCase().includes(q) || (x.practice_name || "").toLowerCase().includes(q));
      if (!hit) { hist.textContent = "No practice matches."; return; }
      const kv = el("dl", { class: "kv" });
      [["Name", hit.full_name || "(missing)"], ["Practice", hit.practice_name], ["Specialty", hit.specialty || "(missing)"], ["Segment", nice(hit.segment)], ["Status", hit.status], ["Verification", hit.verification_status + (hit.verification_reason ? " (" + hit.verification_reason + ")" : "")], ["Attempts", hit.attempts], ["Next attempt", hit.next_attempt_at ? fmtTime(hit.next_attempt_at) : "–"], ["Time zone", hit.time_zone], ["Source", hit.lead_source], ["Hook", hit.hook || "–"], ["Ids", hit.id + (hit.external_id ? " · " + hit.external_id : "")], ["Test scenario", hit.test_scenario || "–"]]
        .forEach(([k, v]) => { kv.appendChild(el("dt", { text: k })); kv.appendChild(el("dd", { text: String(v ?? "") })); });
      hist.appendChild(kv);
      hist.appendChild(el("h3", { class: "small", text: "Channels" }));
      hist.appendChild(table(["Type", "Value (masked)", "Line type", "Verified", "Clearance", "Source"], (hit.channels || []).map((ch) => [ch.type, ch.value, ch.line_type, ch.verified ? "yes" : "no" + (ch.verification_reason ? " (" + ch.verification_reason + ")" : ""), nice(ch.call_clearance), ch.source])));
      hist.appendChild(el("h3", { class: "small", text: "Gate decisions" }));
      hist.appendChild(table(["Run", "Decision", "Reasons"], decisions.filter((g) => g.contact_id === hit.id).reverse().map((g) => [g.run_id, kind(g), (g.reasons || []).join("; ")])));
      hist.appendChild(el("h3", { class: "small", text: "Audit trail" }));
      hist.appendChild(table(["Time", "Entity", "Action", "Change"], (d.audit_log || []).filter((e) => e.entity_id === hit.id || e.contact_id === hit.id).reverse().map((e) => [fmtTime(e.timestamp), e.entity + " " + e.entity_id, nice(e.action), JSON.stringify(e.after || {})]), { nowrap: [0] }));
    }
    input.addEventListener("input", () => renderHistory(input.value));
    const params = new URLSearchParams(location.search);
    if (params.get("contact")) { input.value = params.get("contact"); renderHistory(input.value); input.closest("details").open = true; }

    $("footer").textContent = "Snapshot " + (s.generated_at ? fmtTime(s.generated_at) : "–") + " · run " + (run.run_id || "–") + " · infra v0, dry run";
  }

  loadAll().then((d) => {
    if (!d.summary) { $("error").textContent = "No pipeline data yet. Run `python -m infra run` locally or trigger the Dry-run pipeline workflow; the snapshot lands in docs/data/."; return; }
    render(d);
  }).catch((e) => { $("error").textContent = "Failed to render: " + e.message; console.error(e); });
})();
