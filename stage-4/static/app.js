"use strict";
(() => {
  const root = document.getElementById("app");
  const route = window.location.pathname;
  let token = localStorage.getItem("pocketful.session") || "";
  let me = null;
  let generation = 0;
  let activity = [];
  let activityMore = false;
  const attempts = new Map();
  const refs = {};

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== null && value !== false) node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  const test = (id) => ({"data-testid": id});
  const text = (tag, value, attrs = {}) => el(tag, attrs, value);
  function newRequestKey() {
    // getRandomValues also works on standalone HTTP origins; randomUUID does not.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  }
  function formatted(value, units = me?.minor_units ?? 2, currency = me?.currency ?? "EUR") {
    const signed = BigInt(value);
    const amount = signed < 0n ? -signed : signed;
    const scale = 10n ** BigInt(units);
    const whole = amount / scale;
    return `${signed < 0n ? "−" : ""}${whole}${units ? "." + (amount % scale).toString().padStart(units, "0") : ""} ${currency}`;
  }
  function decimal(value) {
    const amount = BigInt(value);
    const units = me.minor_units;
    const scale = 10n ** BigInt(units);
    return `${amount / scale}${units ? "." + (amount % scale).toString().padStart(units, "0") : ""}`;
  }
  function minor(raw, allowZero = false) {
    if (!me) throw new Error("Your wallet is still loading. Please try again.");
    const input = raw.trim();
    if (!/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(input)) throw new Error("Enter an amount using digits and a decimal point.");
    const [whole, fraction = ""] = input.split(".");
    if (fraction.length > me.minor_units) throw new Error(`Use no more than ${me.minor_units} decimal places for ${me.currency}.`);
    const value = BigInt(whole || "0") * (10n ** BigInt(me.minor_units)) + BigInt(fraction.padEnd(me.minor_units, "0") || "0");
    if (value < (allowZero ? 0n : 1n) || value > 1000000000n) throw new Error(allowZero ? "Enter zero or a positive amount within the payment limit." : "Enter an amount greater than zero and within the payment limit.");
    return Number(value);
  }
  function message(error) {
    const messages = {
      insufficient_funds: "There aren’t enough available funds. Your balance has been refreshed.",
      not_found: "We couldn’t find that person or item. Please check the handle and try again.",
      self_payment: "Choose someone other than yourself to pay.", self_request: "Choose someone other than yourself to request from.",
      request_not_pending: "This request is no longer pending. We’ve refreshed the list.",
      authorization_not_open: "This authorization is no longer open. We’ve refreshed the list.",
      authorization_expired: "This authorization has expired. The remaining funds have been released.",
      capture_exceeds_authorization: "The capture is greater than the remaining authorized amount.",
      stale_revision: "This payment has a newer revision. Review its history, then use the latest revision before submitting again.",
      historical_overdraft: "This change would leave a wallet short at an earlier point in time. Adjust the amount or effective time.",
      linked_payment_immutable: "Captures and refunds cannot be corrected. Settlement members must be corrected together by an operator.",
      refund_exceeds_payment: "This amount exceeds what remains refundable, or lowers a payment below its completed refunds.",
      invalid_refund_target: "A refund cannot itself be refunded.",
      incomplete_settlement: "Include every payment in the settlement before submitting this batch.",
      forbidden: "This action isn’t available to your account.",
      email_taken: "An account already uses this email. Try signing in.",
      handle_taken: "An account already uses the handle derived from this email. Use another email.",
      unauthenticated: "Your session is unavailable. Please sign in again.",
      validation_failed: "Check the amount, handle, note and selected options, then try again."
    };
    return messages[error.code] || error.message || "Something went wrong. Please try again.";
  }
  class APIError extends Error {
    constructor(status, code, msg) { super(msg); this.status = status; this.code = code; }
  }
  async function api(path, options = {}) {
    const headers = {Accept: "application/json", ...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers};
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(path, {...options, headers, cache: "no-store", signal: controller.signal});
      let data;
      try { data = response.status === 204 ? null : await response.json(); }
      catch { throw new Error("The response couldn’t be confirmed."); }
      if (!response.ok) throw new APIError(response.status, data?.error?.code, data?.error?.message);
      return data;
    } finally { clearTimeout(timer); }
  }
  function feedback(container, name, kind, content) {
    container.replaceChildren();
    if (content) container.append(text("p", content, {class: `feedback ${kind}`, role: kind === "error" ? "alert" : "status", ...test(name)}));
  }
  function clearFeedback(container) { container.replaceChildren(); }
  function field(name, label, options = {}) {
    const input = options.choices ? el("select", {id: name, name, ...test(name)}, options.choices.map(([value, label]) => el("option", {value}, label)))
      : el(options.multiline ? "textarea" : "input", {id: name, name, ...(options.multiline ? {rows: 2} : {type: options.type || "text"}), autocomplete: options.autocomplete || "off", ...test(name),
          ...(options.money ? {inputmode: "decimal", placeholder: "0.00"} : {}), ...(options.placeholder ? {placeholder: options.placeholder} : {})});
    return {input, wrap: el("div", {class: "field" + (options.wide ? " wide" : "")}, text("label", label, {for: name}), input,
      options.help ? text("small", options.help) : null)};
  }
  const privacyChoices = [["public", "Public · visible to everyone"], ["private", "Private · just the two of you"]];
  function cardTitle(title, caption, kicker) {
    return el("div", {class: "card-head"}, el("div", {}, text("h2", title), caption ? text("p", caption, {class: "subtext"}) : null), kicker ? text("span", kicker, {class: "card-kicker"}) : null);
  }

  function wireWrite(form, config) {
    const state = attempts.get(config.name) || {version: 0, attempt: null, busy: false};
    attempts.set(config.name, state);
    form.addEventListener("input", () => { state.version++; clearFeedback(config.feedback); config.onEdit?.(); });
    form.addEventListener("change", () => { state.version++; clearFeedback(config.feedback); config.onEdit?.(); });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.busy) return;
      let body;
      try { body = config.body(); }
      catch (error) { feedback(config.feedback, config.errorId, "error", message(error)); return; }
      const signature = JSON.stringify(config.raw ? config.raw() : body);
      if (!state.attempt || state.attempt.version !== state.version || state.attempt.signature !== signature) {
        state.attempt = {version: state.version, signature, body: JSON.stringify(body), key: newRequestKey()};
      }
      const attempt = state.attempt;
      const button = config.button;
      const originalLabel = button.textContent;
      state.busy = true; button.disabled = true; button.textContent = "Working…";
      feedback(config.feedback, `${config.name}-loading`, "loading", "Confirming your action…");
      let outcome, result;
      try {
        result = await api(config.path(), {method: "POST", body: attempt.body,
          headers: config.keyed === false ? {} : {"Idempotency-Key": attempt.key}});
        outcome = "success";
        if (attempt === state.attempt && attempt.version === state.version) feedback(config.feedback, `${config.name}-success`, "success", config.success);
      } catch (error) {
        if (error instanceof APIError && error.status < 500) {
          outcome = "refused";
          feedback(config.feedback, config.errorId, "error", message(error));
        } else {
          outcome = "uncertain";
          feedback(config.feedback, config.uncertainId || `${config.name}-uncertain`, "uncertain",
            "We couldn’t confirm the response. Keep these details and try again safely; the same action won’t be applied twice.");
        }
      } finally {
        state.busy = false; button.disabled = false; button.textContent = originalLabel;
      }
      if (outcome !== "uncertain") {
        await refresh();
        await config.afterOutcome?.(outcome, result);
      }
    });
  }

  function transferForm(kind) {
    const isRequest = kind === "request", isHold = kind === "authorize";
    const heading = isRequest ? "Request money" : isHold ? "Authorize a payment" : "Send a payment";
    const subtitle = isRequest ? "Ask someone for their share. They choose when to pay." : isHold ? "Reserve funds now for someone to collect later." : "A little thank-you. Your share of dinner. Send it here.";
    const form = el("form", {novalidate: true, class: `card form-${kind}`});
    const handle = field(`${kind}-handle`, isRequest ? "Request from" : "To", {placeholder: "Recipient’s handle", help: "Use their handle, without @"});
    const amount = field(`${kind}-amount`, "Amount", {money: true});
    const note = field(`${kind}-note`, "What’s it for?", {wide: true, multiline: true, placeholder: "Add a note (optional)"});
    const visibility = isRequest ? null : field(`${kind}-visibility`, "Who can see this payment?", {wide: true, choices: privacyChoices});
    const fields = el("div", {class: "fields"}, handle.wrap, amount.wrap, note.wrap, visibility?.wrap);
    const button = text("button", isRequest ? "Send request" : isHold ? "Authorize funds" : "Send payment", {type: "submit", class: "primary", ...test(`${kind}-submit`)});
    const status = el("div", {"aria-live": "polite"});
    form.append(cardTitle(heading, subtitle), fields,
      el("div", {class: "form-actions"}, text("span", isRequest ? "No money moves until they pay." : isHold ? "A hold reserves funds. It doesn’t send them." : "Paid directly from your available funds.", {class: "form-footnote"}), button), status);
    wireWrite(form, {name: kind, button, feedback: status, errorId: `${kind}-error`, uncertainId: kind === "pay" ? "pay-uncertain" : null,
      path: () => isRequest ? "/requests" : isHold ? "/authorizations" : "/payments",
      body: () => ({[isRequest ? "payer_handle" : "to_handle"]: handle.input.value.trim(), amount: minor(amount.input.value), note: note.input.value,
        ...(visibility ? {visibility: visibility.input.value} : {})}),
      raw: () => [handle.input.value, amount.input.value, note.input.value, visibility?.input.value],
      success: isRequest ? "Request sent. You’ll see it in outgoing requests." : isHold ? "Funds authorized. You can review or release the hold in Authorizations." : "Payment sent. Your wallet and activity are up to date."});
    return form;
  }

  function empty(id, title, caption, icon = "○") {
    return el("div", {class: "empty", ...test(id)}, text("span", icon, {class: "empty-icon", "aria-hidden": "true"}), text("strong", title), text("span", caption));
  }
  function dateLabel(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(date);
  }
  function badge(value) { return text("span", value[0].toUpperCase() + value.slice(1), {class: `badge ${value}`}); }
  function renderIdentity() {
    refs.identity.replaceChildren();
    if (!me) {
      refs.identity.append(el("a", {href: "/login"}, "Sign in")); return;
    }
    const logout = text("button", "Sign out", {class: "text-button", type: "button", ...test("logout-button"), onclick: () => {
      localStorage.removeItem("pocketful.session"); token = ""; attempts.clear(); window.location.assign("/login");
    }});
    refs.identity.append(text("span", Array.from(me.display_name || me.handle)[0]?.toUpperCase() || "P", {class: "avatar", "aria-hidden": "true"}),
      el("div", {class: "identity-copy"}, text("div", me.display_name, {class: "person-name", ...test("current-user")}), text("div", me.handle, {class: "handle-line", ...test("current-handle")})), logout);
  }
  function renderBalance() {
    if (!refs.balance) return;
    const total = me.total ?? me.balance, held = me.held ?? 0, available = me.available ?? me.balance;
    const meta = el("dl", {class: "balance-meta"}, el("div", {}, text("dt", "Total balance"), text("dd", formatted(total), {...test("wallet-balance"), "data-amount": total})));
    if (BigInt(held) !== 0n) meta.append(el("div", {}, text("dt", "Held for authorizations"), text("dd", formatted(held), {...test("wallet-held"), "data-amount": held})));
    refs.balance.replaceChildren(el("div", {}, text("p", "Available to spend", {class: "balance-label"}),
      text("div", formatted(available), {class: "balance-value" + (formatted(available).length > 16 ? " balance-long" : ""), ...test("wallet-available"), "data-amount": available}), meta), text("div", "P", {class: "balance-icon", "aria-hidden": "true"}));
    for (const input of document.querySelectorAll('[inputmode="decimal"]')) input.placeholder = me.minor_units ? "0." + "0".repeat(me.minor_units) : "0";
  }
  function renderActivity() {
    const container = refs.activity;
    container.replaceChildren();
    if (!activity.length) { container.append(empty("empty-activity", "A fresh start", "Your payments and shared moments will appear here.")); return; }
    const list = el("div", {class: "record-list", ...test("activity-list")});
    for (const payment of activity) {
      const id = payment.payment_id;
      const direction = payment.from_user_id === me.user_id ? "You sent" : payment.to_user_id === me.user_id ? "You received" : "Shared payment";
      list.append(el("article", {class: "record", ...test(`activity-item-${id}`), "data-visibility": payment.visibility},
        el("div", {class: "record-top"}, text("div", `${payment.from_handle} → ${payment.to_handle}`, {class: "record-people", ...test(`activity-parties-${id}`)}),
          text("div", formatted(payment.amount), {class: "record-money", ...test(`activity-amount-${id}`)})),
        text("p", payment.note, {class: "record-note", ...test(`activity-note-${id}`)}),
        el("div", {class: "record-meta"}, text("span", direction), badge(payment.visibility), text("time", dateLabel(payment.created_at), {datetime: payment.created_at})),
        text("p", payment.refund_of ? "Refund · original receipt" : "Original receipt", {class: "date-exact"}),
        payment.from_user_id === me.user_id || payment.to_user_id === me.user_id ? historyLink(id) : null));
    }
    container.append(list);
    if (activityMore) container.append(text("button", "Show older activity", {type: "button", class: "secondary load-more", onclick: async (event) => {
      const current = generation; event.currentTarget.disabled = true;
      try {
        const result = await api(`/activity?limit=50&offset=${activity.length}`);
        if (current !== generation) return;
        const seen = new Set(activity.map(p => p.payment_id));
        activity.push(...result.payments.filter(p => !seen.has(p.payment_id))); activityMore = result.has_more; renderActivity();
      } catch (error) { if (current === generation) feedback(refs.global, "page-error", "error", message(error)); }
    }}));
  }

  function requestAction(request, action, label) {
    const form = el("form", {novalidate: true, class: action === "pay" ? "request-pay-form" : ""});
    const visibility = action === "pay" ? field(`request-visibility-${request.request_id}`, "Payment visibility", {choices: privacyChoices}) : null;
    const button = text("button", label, {type: "submit", class: action === "pay" ? "primary" : "secondary", ...test(`request-${action}-${request.request_id}`)});
    if (visibility) form.append(visibility.wrap);
    form.append(button);
    wireWrite(form, {name: `request-${request.request_id}-${action}`, button, feedback: refs.requestFeedback, errorId: "request-error", keyed: action === "pay",
      path: () => `/requests/${encodeURIComponent(request.request_id)}/${action}`, body: () => action === "pay" ? {visibility: visibility.input.value} : {},
      success: action === "pay" ? "Request paid. The payment is in your activity." : action === "decline" ? "Request declined." : "Request cancelled."});
    return form;
  }
  function renderRequests(requests) {
    refs.incoming.replaceChildren(); refs.outgoing.replaceChildren(); refs.requestsEmpty.replaceChildren();
    for (const request of requests) {
      const incoming = request.payer_id === me.user_id;
      const id = request.request_id;
      const actions = el("div", {class: "record-actions"});
      if (request.status === "pending") {
        if (incoming) actions.append(requestAction(request, "pay", "Pay request"), requestAction(request, "decline", "Decline"));
        else actions.append(requestAction(request, "cancel", "Cancel request"));
      }
      (incoming ? refs.incoming : refs.outgoing).append(el("article", {class: "record", ...test(`request-item-${id}`), "data-status": request.status},
        el("div", {class: "record-top"}, text("div", incoming ? `${request.requester_handle} requested from you` : `You requested from ${request.payer_handle}`, {class: "record-people"}),
          text("div", formatted(request.amount), {class: "record-money", ...test(`request-amount-${id}`)})),
        text("p", request.note, {class: "record-note"}), el("div", {class: "record-meta"}, badge(request.status), text("time", dateLabel(request.created_at), {datetime: request.created_at})), actions));
    }
    if (!requests.length) refs.requestsEmpty.append(empty("empty-requests", "All clear", "New requests will appear here. You can send one from your wallet."));
    if (!refs.incoming.children.length) refs.incoming.append(text("p", "No incoming requests.", {class: "subtext"}));
    if (!refs.outgoing.children.length) refs.outgoing.append(text("p", "No outgoing requests.", {class: "subtext"}));
  }

  function splitScreen() {
    const form = el("form", {class: "card", novalidate: true});
    const amount = field("split-amount", "Bill amount", {money: true, wide: true});
    const handles = field("split-handles", "Who’s splitting?", {wide: true, placeholder: "ada, bob, cy", help: "Separate handles with commas. Include yourself if you’re sharing the bill."});
    const note = field("split-note", "What’s it for?", {wide: true, multiline: true, placeholder: "Dinner, tickets, a weekend away…"});
    const preview = el("div", {class: "split-preview", ...test("split-preview"), "aria-live": "polite"});
    const status = el("div");
    const button = text("button", "Request everyone’s share", {type: "submit", class: "primary", ...test("split-submit")});
    function participants() {
      const people = handles.input.value.split(",").map(value => value.trim()).filter(Boolean);
      if (!people.length) throw new Error("Add at least one handle to split the bill.");
      if (new Set(people).size !== people.length) throw new Error("Each participant should appear only once.");
      return people;
    }
    function updatePreview() {
      preview.replaceChildren();
      if (!me || !amount.input.value || !handles.input.value) { preview.append(text("p", "Your shares will appear here before you send.", {class: "subtext"})); return; }
      try {
        const total = BigInt(minor(amount.input.value)), people = participants(), count = BigInt(people.length);
        preview.append(text("h3", "Everyone’s share"));
        people.forEach((handle, index) => preview.append(el("div", {class: "share-row"}, text("span", handle + (handle === me.handle ? " · you" : "")),
          text("span", formatted(total / count + (BigInt(index) < total % count ? 1n : 0n)), test(`split-share-${handle}`)))));
      } catch (error) { feedback(status, "split-error", "error", message(error)); }
    }
    form.append(cardTitle("One bill. Fair shares.", "Whole minor units, divided evenly. Any extra unit goes to the first people listed."),
      el("div", {class: "fields"}, amount.wrap, handles.wrap, note.wrap), preview, el("div", {class: "form-actions"}, button), status);
    wireWrite(form, {name: "split", button, feedback: status, errorId: "split-error", path: () => "/splits", onEdit: updatePreview,
      body: () => ({amount: minor(amount.input.value), participant_handles: participants(), note: note.input.value}),
      raw: () => [amount.input.value, handles.input.value, note.input.value], success: "Split created. The requests are ready in your outgoing list."});
    refs.preview = updatePreview;
    return form;
  }

  function renderAuthorizations(authorizations) {
    refs.authorizations.replaceChildren();
    if (!authorizations.length) { refs.authorizations.append(empty("empty-authorizations", "No authorizations yet", "Reserve a payment for someone to collect later.")); return; }
    const list = el("div", {class: "record-list", ...test("authorization-list")});
    for (const authorization of authorizations) {
      const id = authorization.authorization_id, incoming = authorization.to_user_id === me.user_id;
      const row = el("article", {class: "record", ...test(`authorization-item-${id}`), "data-status": authorization.status},
        el("div", {class: "record-top"}, text("div", incoming ? `${authorization.from_handle} authorized you` : `You authorized ${authorization.to_handle}`, {class: "record-people"}),
          text("div", formatted(authorization.amount), {class: "record-money", ...test(`authorization-amount-${id}`)})),
        text("p", authorization.note, {class: "record-note"}), el("div", {class: "record-meta"}, badge(authorization.status), badge(authorization.visibility)));
      row.append(text("p", `Expiry: ${dateLabel(authorization.expires_at)}`, {class: "subtext"}));
      const expiry = el("div", {class: "date-exact"}, text("time", authorization.expires_at, {datetime: authorization.expires_at, ...test(`authorization-expires-${id}`)}));
      row.append(expiry);
      if (authorization.status === "captured") row.append(el("p", {class: "subtext"}, "Captured: ", text("span", formatted(authorization.captured_amount), test(`authorization-captured-${id}`))));
      else if (authorization.captured_amount > 0) row.append(text("p", `Collected so far: ${formatted(authorization.captured_amount)}`, {class: "subtext"}));
      if (authorization.status === "open") {
        const form = el("form", {class: "record-actions", novalidate: true});
        const amount = incoming ? field(`authorization-capture-amount-${id}`, "Amount to collect", {money: true}) : null;
        const final = incoming ? el("input", {type: "checkbox", id: `capture-final-${id}`, checked: true}) : null;
        if (amount) { amount.input.value = decimal(authorization.remaining_amount); form.append(amount.wrap); }
        const action = incoming ? "capture" : "void";
        const button = text("button", incoming ? "Capture payment" : "Release hold", {type: "submit", class: incoming ? "primary" : "secondary", ...test(`authorization-${action}-${id}`)});
        form.append(button);
        if (final) form.append(el("label", {class: "checkbox-row", for: `capture-final-${id}`}, final, "Final capture · release any remainder"));
        wireWrite(form, {name: `authorization-${id}-${action}`, button, feedback: refs.authorizationFeedback, errorId: "authorization-error", keyed: incoming,
          path: () => `/authorizations/${encodeURIComponent(id)}/${action}`, body: () => incoming ? {amount: minor(amount.input.value), final: final.checked} : {},
          raw: () => incoming ? [amount.input.value, final.checked] : [], success: incoming ? "Payment captured. Your wallet is up to date." : "Hold released. The remaining funds are available again."});
        row.append(form);
      }
      list.append(row);
    }
    refs.authorizations.append(list);
  }

  function instant(raw, label, optional = true) {
    const value = raw.trim();
    if (!value && optional) return null;
    const parts = /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/.exec(value);
    if (!parts) throw new Error(`${label}: use a complete timestamp with Z or an offset, for example 2026-09-24T13:20:00+00:00.`);
    const [, year, month, day, hour, minute, second, zone] = parts;
    const leapYear = +year % 4 === 0 && (+year % 100 !== 0 || +year % 400 === 0);
    const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][+month - 1];
    if (+month < 1 || +month > 12 || +day < 1 || +day > days || +hour > 23 || +minute > 59 || +second > 60 ||
        (zone.length > 1 && (+zone.slice(1, 3) > 23 || +zone.slice(4) > 59)))
      throw new Error(`${label}: enter a valid calendar date, time and offset.`);
    // The server is authoritative for leap-second placement and future limits.
    // Do not round fractions or normalize offsets through JavaScript Date.
    return value;
  }
  function timeField(id, label, optional = true) {
    return field(id, label, {wide: true, placeholder: optional ? "Leave blank for the default" : "2026-09-24T13:20:00+00:00",
      help: "ISO date and time, including Z or an explicit offset (such as +02:00)."});
  }
  function historyLink(id, label = "Receipt & revision history") {
    return el("a", {href: `/history?payment=${encodeURIComponent(id)}`, class: "history-link"}, label);
  }
  function moneyFact(label, value, id) {
    return el("div", {}, text("dt", label), text("dd", formatted(value), id ? test(id) : {}));
  }
  function temporalBalanceScreen() {
    const form = el("form", {class: "card", novalidate: true});
    const asOf = timeField("history-as-of", "Balance at (inclusive)");
    const known = timeField("history-known-at", "Using information known by");
    const status = el("div", {"aria-live": "polite"}), result = el("div", {"aria-live": "polite"});
    const button = text("button", "View historical balance", {class: "primary", type: "submit", ...test("history-balance-submit")});
    let read = 0;
    form.append(cardTitle("Your wallet through time", "The balance at a point in time, using only the information known by your chosen date."),
      el("div", {class: "fields"}, asOf.wrap, known.wrap), el("div", {class: "form-actions"}, button), status, result);
    form.addEventListener("submit", async event => {
      event.preventDefault(); const current = ++read; const params = new URLSearchParams();
      try {
        const at = instant(asOf.input.value, "Balance at"), knowledge = instant(known.input.value, "Known by");
        if (at) params.set("as_of", at); if (knowledge) params.set("known_at", knowledge);
        feedback(status, "history-balance-loading", "loading", "Reading this historical view…");
        const user = await api(`/me?${params}`);
        if (current !== read) return;
        clearFeedback(status);
        result.replaceChildren(text("p", `Historical view · at ${at || "the time of this read"} · known by ${knowledge || "the time of this read"}`, {class: "subtext temporal-caption"}),
          el("dl", {class: "history-money"}, moneyFact("Available in this view", user.available, "history-available"), moneyFact("Total", user.total, "history-total"), moneyFact("Held", user.held, "history-held")));
      } catch (error) { if (current === read) feedback(status, "history-balance-error", "error", message(error)); }
    });
    return form;
  }
  function statementScreen() {
    const section = el("section", {class: "card"});
    const form = el("form", {novalidate: true});
    const from = timeField("statement-from", "From (inclusive)");
    const to = timeField("statement-to", "To (exclusive)");
    const known = timeField("statement-known-at", "Using information known by");
    const limit = field("statement-limit", "Entries per page", {choices: [["10", "10"], ["25", "25"], ["50", "50"], ["1", "1"]]});
    const submit = text("button", "Create a fresh statement", {class: "primary", type: "submit", ...test("statement-submit")});
    const status = el("div", {"aria-live": "polite"}), results = el("div", {"aria-live": "polite"});
    let snapshot = null, offset = 0, size = 10, read = 0, caption = "";
    form.append(cardTitle("A statement that stays put", "Each statement is a saved view. New payments and corrections will not change it while you turn the pages."),
      el("div", {class: "fields"}, from.wrap, to.wrap, known.wrap, limit.wrap), el("div", {class: "form-actions"}, submit));
    section.append(form, status, results);
    async function load(nextOffset, fresh) {
      const current = ++read;
      try {
        const params = new URLSearchParams();
        let nextSize = size, nextCaption = caption;
        if (fresh) {
          for (const [key, input, label] of [["from", from.input, "From"], ["to", to.input, "To"], ["known_at", known.input, "Known by"]]) {
            const value = instant(input.value, label); if (value) params.set(key, value);
          }
          nextSize = Number(limit.input.value);
          nextCaption = `From ${params.get("from") || "wallet opening"} · to ${params.get("to") || "the time this snapshot was created"} · known by ${params.get("known_at") || "the time this snapshot was created"}`;
        } else params.set("snapshot", snapshot);
        params.set("limit", nextSize); params.set("offset", nextOffset);
        feedback(status, "statement-loading", "loading", "Reading your statement…");
        const data = await api(`/statement?${params}`);
        if (current !== read) return;
        snapshot = data.snapshot; offset = nextOffset; size = nextSize; caption = nextCaption;
        clearFeedback(status);
        const list = el("div", {class: "record-list", ...test("statement-list")});
        for (const entry of data.entries) {
          const p = entry.payment, id = p.payment_id;
          const delta = BigInt(entry.delta);
          list.append(el("article", {class: "record", ...test(`statement-item-${id}`), "data-revision": entry.revision},
            el("div", {class: "record-top"}, text("strong", `${p.from_handle} → ${p.to_handle}`), text("span", `${delta < 0n ? "−" : delta > 0n ? "+" : ""}${formatted(delta < 0n ? -delta : delta)}`, {class: "record-money", ...test(`statement-delta-${id}`)})),
            text("p", p.note, {class: "record-note"}), text("p", `Selected revision ${entry.revision} · ${formatted(p.amount)}`, {class: "subtext"}),
            text("p", `Effective: ${entry.effective_at}`, {class: "date-exact"}), text("p", `Recorded: ${entry.recorded_at}`, {class: "date-exact"}),
            el("p", {class: "subtext"}, "Balance after: ", text("span", formatted(entry.balance_after), test(`statement-after-${id}`))), historyLink(id)));
        }
        if (!data.entries.length) list.append(empty("empty-statement", "No movements in this view", "Try another time window or create a fresh statement."));
        const previous = text("button", "Previous page", {type: "button", class: "secondary", disabled: offset === 0, ...test("statement-previous"), onclick: () => load(Math.max(0, offset - size), false)});
        const next = text("button", "Next page", {type: "button", class: "secondary", disabled: !data.has_more, ...test("statement-next"), onclick: () => load(offset + size, false)});
        results.replaceChildren(text("p", caption, {class: "subtext temporal-caption", ...test("statement-window")}),
          el("dl", {class: "history-money"}, moneyFact("Opening balance", data.opening_balance, "statement-opening"), moneyFact("Closing balance", data.closing_balance, "statement-closing")),
          text("p", "Saved snapshot · balances describe the entire window, not just this page.", {class: "snapshot-label", ...test("statement-snapshot")}), list,
          el("div", {class: "record-actions pagination"}, previous, text("span", `Page ${Math.floor(offset / size) + 1}`, test("statement-page")), next));
      } catch (error) { if (current === read) feedback(status, "statement-error", "error", error.code === "not_found" ? "This saved statement is no longer available. Create a fresh statement." : message(error)); }
    }
    form.addEventListener("submit", event => { event.preventDefault(); load(0, true); });
    refs.startTemporal = () => load(0, true);
    return section;
  }
  function historyScreen() {
    const section = el("section", {class: "card"});
    const list = el("div", {class: "record-list", ...test("history-list")});
    const detail = el("section", {class: "card history-detail", tabindex: "-1", ...test("history-detail")});
    const status = el("div", {"aria-live": "polite"});
    let selection = 0;
    section.append(cardTitle("Original receipts", "These amounts are the original payments. Open a receipt to inspect its immutable revision history."), status, list);
    async function open(payment) {
      const current = ++selection, id = payment.payment_id;
      detail.replaceChildren(text("p", "Loading revision history…", {role: "status"}));
      try {
        const data = await api(`/payments/${encodeURIComponent(id)}/revisions`);
        if (current !== selection) return;
        const original = data.revisions[0], latest = data.revisions.at(-1);
        const revisions = el("div", {class: "record-list", ...test("revision-list")});
        function draw(rows) {
          revisions.replaceChildren(...rows.map(revision => el("article", {class: "record", ...test(`revision-${revision.revision}`)},
            el("div", {class: "record-top"}, text("strong", `Revision ${revision.revision}${revision.revision === 1 ? " · original" : ""}`), text("span", formatted(revision.amount), {class: "record-money"})),
            text("p", `Effective: ${revision.effective_at}`, {class: "date-exact"}), text("p", `Recorded: ${revision.recorded_at}`, {class: "date-exact"}), text("p", revision.reason || "Original payment", {class: "record-note"}))));
        }
        draw(data.revisions);
        detail.replaceChildren(cardTitle("Receipt & revision history", `${payment.from_handle} → ${payment.to_handle}`),
          text("p", `Original receipt: ${formatted(original.amount)}`, {class: "original-receipt", ...test("original-receipt")}),
          text("p", `Money moved: ${payment.created_at}`, {class: "date-exact"}), text("p", payment.note, {class: "record-note"}), revisions);
        if (payment.from_user_id === me.user_id && !payment.authorization_id && !payment.settlement_id && !payment.refund_of) {
          const form = el("form", {novalidate: true, class: "correction-form"});
          const amount = field("correction-amount", "Corrected amount", {money: true, help: "Zero reverses the full payment. This replaces the amount; it is not an additional payment."});
          const expected = field("correction-expected", "Expected revision", {help: "Matches the current revision. A newer revision must be reviewed before retrying."});
          expected.input.readOnly = true; expected.input.value = latest.revision;
          amount.input.value = decimal(latest.amount);
          const effective = timeField("correction-effective", "Effective at", false); effective.input.value = latest.effective_at;
          const reason = field("correction-reason", "Reason for correction", {wide: true, multiline: true, help: "Required · 1–200 characters"});
          const feedbackBox = el("div", {"aria-live": "polite"});
          const button = text("button", "Save correction", {type: "submit", class: "primary", ...test("correction-submit")});
          let latestRevision = latest.revision;
          const reload = text("button", "Review latest history", {type: "button", class: "secondary", ...test("revision-refresh"), onclick: async () => {
            try { const updated = await api(`/payments/${encodeURIComponent(id)}/revisions`); if (current !== selection) return; draw(updated.revisions); latestRevision = updated.revisions.at(-1).revision; }
            catch (error) { feedback(feedbackBox, "correction-error", "error", message(error)); }
          }});
          const adopt = text("button", "Use latest revision", {type: "button", class: "secondary", ...test("correction-use-latest"), onclick: async () => {
            try { const updated = await api(`/payments/${encodeURIComponent(id)}/revisions`); if (current !== selection) return; draw(updated.revisions); latestRevision = updated.revisions.at(-1).revision;
              expected.input.value = latestRevision; expected.input.dispatchEvent(new Event("input", {bubbles: true})); }
            catch (error) { feedback(feedbackBox, "correction-error", "error", message(error)); }
          }});
          form.append(cardTitle("Correct this payment", "The original receipt stays unchanged. Corrections adjust the same two wallets and must keep their history affordable."),
            el("div", {class: "fields"}, amount.wrap, expected.wrap, effective.wrap, reason.wrap), el("div", {class: "record-actions"}, button, reload, adopt), feedbackBox);
          wireWrite(form, {name: `correction-${id}`, button, feedback: feedbackBox, errorId: "correction-error", uncertainId: "correction-uncertain", path: () => `/payments/${encodeURIComponent(id)}/corrections`,
            body: () => { const length = Array.from(reason.input.value).length; if (!length || length > 200) throw new Error("Add a reason of 1–200 characters.");
              return {expected_revision: Number(expected.input.value), amount: minor(amount.input.value, true), effective_at: instant(effective.input.value, "Effective at", false), reason: reason.input.value}; },
            raw: () => [expected.input.value, amount.input.value, effective.input.value, reason.input.value], success: "Correction saved. Original receipts and existing statement snapshots remain unchanged.",
            afterOutcome: async () => { try { const updated = await api(`/payments/${encodeURIComponent(id)}/revisions`); if (current !== selection) return; draw(updated.revisions); latestRevision = updated.revisions.at(-1).revision; } catch {} }});
          detail.append(form);
        } else detail.append(text("p", payment.from_user_id !== me.user_id ? "Only the original sender can correct this payment." : "Settlement members, captures and refunds cannot be corrected individually.", {class: "subtext"}));
        if (payment.refund_of) detail.append(el("p", {class: "snapshot-label", ...test("refund-link")}, "Refund of ", el("a", {href: `/history?payment=${encodeURIComponent(payment.refund_of)}`}, payment.refund_of), ". This refund is an immutable receipt."));
        else if (payment.to_user_id === me.user_id) {
          const form = el("form", {class: "correction-form", novalidate: true});
          const facts = el("div", {"aria-live": "polite", ...test("refund-eligibility")});
          const amount = field("refund-amount", "Refund amount", {money: true, help: "Paid from your available funds. Previous refunds count toward the current corrected payment amount."});
          const feedbackBox = el("div", {"aria-live": "polite"});
          const button = text("button", "Send refund", {type: "submit", class: "primary", ...test("refund-submit")});
          let reading = 0;
          async function eligibility() {
            const read = ++reading;
            try {
              const [updated, payments] = await Promise.all([api(`/payments/${encodeURIComponent(id)}/revisions`), allItems("/activity", "payments")]);
              if (current !== selection || read !== reading) return;
              draw(updated.revisions);
              const corrected = updated.revisions.at(-1).amount;
              const refunded = payments.filter(p => p.refund_of === id).reduce((sum, p) => sum + BigInt(p.amount), 0n);
              const remaining = BigInt(corrected) - refunded;
              facts.replaceChildren(el("dl", {class: "history-money"}, moneyFact("Current corrected amount", corrected, "refund-corrected"), moneyFact("Already refunded", refunded, "refund-total"), moneyFact("Remaining refundable", remaining, "refund-remaining")),
                text("p", remaining === 0n ? "This payment has no remaining refundable amount. An unchanged successful refund can still be retried safely." : "You can refund up to the remaining amount, subject to your available funds. Refresh before deciding; another action may change it.", {class: "subtext"}));
              await loadReceipts();
            } catch (error) { if (current === selection && read === reading) feedback(facts, "refund-read-error", "error", message(error)); }
          }
          const review = text("button", "Refresh refund eligibility", {type: "button", class: "secondary", ...test("refund-refresh"), onclick: async () => { await refresh(); await eligibility(); }});
          form.append(cardTitle("Refund this payment", "Return money to the original sender. The original receipt stays unchanged; requests and authorizations stay closed."), facts, amount.wrap,
            el("div", {class: "record-actions"}, button, review), feedbackBox);
          wireWrite(form, {name: `refund-${id}`, button, feedback: feedbackBox, errorId: "refund-error", uncertainId: "refund-uncertain", path: () => `/payments/${encodeURIComponent(id)}/refunds`,
            body: () => ({amount: minor(amount.input.value)}), raw: () => [amount.input.value], success: "Refund sent. The original receipt and saved statements remain unchanged.",
            afterOutcome: async () => { await eligibility(); }});
          detail.append(form); await eligibility();
        }
        detail.focus();
      } catch (error) { if (current === selection) feedback(detail, "history-error", "error", message(error)); }
    }
    async function loadReceipts() {
      try {
        const payments = (await allItems("/activity", "payments")).filter(p => p.from_user_id === me.user_id || p.to_user_id === me.user_id);
        list.replaceChildren(...payments.map(p => el("article", {class: "record"},
          el("div", {class: "record-top"}, text("strong", `${p.from_handle} → ${p.to_handle}`), text("span", formatted(p.amount), {class: "record-money"})),
          text("p", p.note, {class: "record-note"}), text("p", `${p.refund_of ? "Refund" : "Original"} · ${dateLabel(p.created_at)}`, {class: "subtext"}),
          text("button", "View receipt & history", {type: "button", class: "secondary", ...test(`history-open-${p.payment_id}`), onclick: () => open(p)}))));
        if (!payments.length) list.append(empty("empty-history", "No payment history yet", "Your own sent and received payments will appear here."));
        return payments;
      } catch (error) { feedback(status, "history-error", "error", message(error)); return []; }
    }
    refs.startTemporal = async () => {
        const payments = await loadReceipts();
        const requested = new URLSearchParams(location.search).get("payment");
        const payment = payments.find(p => p.payment_id === requested);
        if (payment) await open(payment);
        else if (requested) feedback(status, "history-error", "error", "This receipt is not available to your account.");
    };
    return el("div", {class: "column"}, temporalBalanceScreen(), el("div", {class: "workspace history-workspace"}, section, detail));
  }

  function operationsScreen() {
    const section = el("section", {class: "card"});
    const form = el("form", {novalidate: true});
    const rowsBox = el("div", {class: "column", ...test("batch-rows")});
    const status = el("div", {"aria-live": "polite"});
    const result = el("section", {"aria-live": "polite", ...test("batch-result")});
    const rows = []; let sequence = 0;
    const submit = text("button", "Apply corrections together", {type: "submit", class: "primary", ...test("batch-submit")});
    const add = text("button", "Add correction", {type: "button", class: "secondary", ...test("batch-add"), onclick: () => addRow(true)});
    function changed() { form.dispatchEvent(new Event("input", {bubbles: true})); }
    function controls() {
      add.disabled = rows.length >= 32;
      rows.forEach((row, index) => { row.legend.textContent = `Correction ${index + 1}`; row.remove.disabled = rows.length <= 1; });
    }
    function addRow(focus = false) {
      if (rows.length >= 32) return;
      const id = ++sequence;
      const payment = field(`batch-payment-${id}`, "Payment ID", {help: "Use the ID from the payment or settlement receipt."});
      const revision = field(`batch-revision-${id}`, "Expected revision", {help: "The current revision number for this payment."}); revision.input.value = "1";
      const amount = field(`batch-amount-${id}`, "Corrected amount", {money: true, help: "Replacement amount. Zero reverses the payment."});
      const effective = timeField(`batch-effective-${id}`, "Effective at", false);
      const reason = field(`batch-reason-${id}`, "Reason", {wide: true, multiline: true, help: "Required · 1–200 characters"});
      const legend = text("legend", "Correction", {class: "batch-legend"});
      const box = el("fieldset", {class: "batch-row", ...test(`batch-row-${id}`)}, legend, el("div", {class: "fields"}, payment.wrap, revision.wrap, amount.wrap, effective.wrap, reason.wrap));
      const row = {payment, revision, amount, effective, reason, legend, box};
      row.remove = text("button", "Remove correction", {type: "button", class: "secondary", ...test(`batch-remove-${id}`), onclick: () => {
        if (rows.length <= 1) return;
        const index = rows.indexOf(row); rows.splice(index, 1); box.remove(); controls(); changed();
        (rows[Math.min(index, rows.length - 1)]?.payment.input || add).focus();
      }});
      box.append(el("div", {class: "record-actions"}, row.remove)); rows.push(row); rowsBox.append(box); controls();
      if (focus) { changed(); payment.input.focus(); }
    }
    section.append(cardTitle("Batch corrections", "For settlement operators. All corrections are applied together, or none are applied."),
      text("p", "Operator permission is required. Enter the payment IDs and current revisions from your records; operator access does not reveal another person’s private history.", {class: "snapshot-label", ...test("batch-role")}),
      text("p", "Include every member of any settlement you correct, using the same effective instant. Captures and refunds cannot be corrected. Completed refunds set the minimum corrected amount.", {class: "subtext"}), form, result);
    form.append(rowsBox, el("div", {class: "record-actions"}, add, submit), status);
    addRow();
    wireWrite(form, {name: "batch", button: submit, feedback: status, errorId: "batch-error", uncertainId: "batch-uncertain", path: () => "/correction-batches",
      raw: () => rows.map(row => [row.payment.input.value, row.revision.input.value, row.amount.input.value, row.effective.input.value, row.reason.input.value]),
      body: () => {
        if (!rows.length || rows.length > 32) throw new Error("Include between 1 and 32 corrections.");
        const seen = new Set();
        return {corrections: rows.map(row => {
          const payment_id = row.payment.input.value.trim(), expected = row.revision.input.value.trim(), reason = row.reason.input.value;
          if (!payment_id || payment_id.length > 64) throw new Error("Enter a payment ID of 1–64 characters.");
          if (seen.has(payment_id)) throw new Error("Each payment can appear only once in a batch."); seen.add(payment_id);
          if (!/^[0-9]+$/.test(expected) || !Number.isSafeInteger(Number(expected)) || Number(expected) < 1) throw new Error("Expected revision must be a positive whole number.");
          if (!Array.from(reason).length || Array.from(reason).length > 200) throw new Error("Add a reason of 1–200 characters for each correction.");
          return {payment_id, expected_revision: Number(expected), amount: minor(row.amount.input.value, true), effective_at: instant(row.effective.input.value, "Effective at", false), reason};
        })};
      }, success: "Batch saved. Original receipts and previously saved statement snapshots remain unchanged.",
      afterOutcome: async (outcome, data) => {
        if (outcome !== "success") return;
        result.replaceChildren(cardTitle("Saved batch receipt", "Revisions are listed in the order submitted. This receipt stays unchanged on a retry."),
          text("p", `Batch: ${data.correction_batch_id}`, {class: "date-exact"}), text("p", `Recorded together: ${data.recorded_at}`, {class: "date-exact", ...test("batch-recorded")}),
          el("ol", {class: "record-list batch-results", ...test("batch-revisions")}, data.revisions.map((revision, index) => el("li", {class: "record", ...test(`batch-result-${index + 1}`)},
            text("strong", `${revision.payment_id} · Revision ${revision.revision}`, {class: "date-exact"}), text("p", formatted(revision.amount), {class: "record-money"}),
            text("p", `Effective: ${revision.effective_at}`, {class: "date-exact"}), text("p", `Recorded: ${revision.recorded_at}`, {class: "date-exact"}), text("p", revision.reason, {class: "record-note"})))));
      }});
    return section;
  }

  async function allItems(path, key) {
    const rows = []; let offset = 0;
    for (;;) {
      const result = await api(`${path}?limit=200&offset=${offset}`);
      rows.push(...result[key]);
      if (!result.has_more || !result[key].length) return rows;
      offset += result[key].length;
    }
  }
  async function refresh() {
    const current = ++generation;
    refs.loading.textContent = "Refreshing your wallet…";
    try {
      const [user, data] = await Promise.all([api("/me"), route === "/" ? api("/activity?limit=50&offset=0")
        : route === "/requests" ? allItems("/requests", "requests") : route === "/authorizations" ? allItems("/authorizations", "authorizations") : Promise.resolve(null)]);
      if (current !== generation) return;
      me = user; renderIdentity(); renderBalance(); clearFeedback(refs.global);
      if (route === "/") { activity = data.payments; activityMore = data.has_more; renderActivity(); }
      if (route === "/requests") renderRequests(data);
      if (route === "/authorizations") renderAuthorizations(data);
      refs.preview?.();
      if (refs.startTemporal) { const start = refs.startTemporal; refs.startTemporal = null; await start(); }
    } catch (error) {
      if (current !== generation) return;
      feedback(refs.global, "page-error", "error", message(error));
      if (error.status === 401) refs.global.append(el("a", {href: "/login"}, "Sign in again"));
    } finally { if (current === generation) refs.loading.textContent = ""; }
  }

  function shell() {
    refs.identity = el("div", {class: "identity"});
    refs.global = el("div", {class: "global-feedback", "aria-live": "polite"});
    refs.loading = el("p", {class: "page-loading", role: "status"});
    refs.main = el("main", {id: "main", tabindex: "-1"});
    const nav = el("nav", {class: "nav", "aria-label": "Main navigation"});
    for (const [href, label, symbol] of [["/", "Wallet", "◈"], ["/requests", "Requests", "↔"], ["/split", "Split a bill", "÷"], ["/authorizations", "Authorizations", "◷"], ["/statements", "Statements", "≡"], ["/history", "History", "↶"], ["/operations", "Operations", "≋"]]) {
      nav.append(el("a", {href, ...(route === href ? {"aria-current": "page"} : {})}, text("span", symbol, {class: "nav-symbol", "aria-hidden": "true"}), label));
    }
    root.replaceChildren(el("div", {class: "shell"}, el("aside", {class: "sidebar"},
      el("a", {href: "/", class: "brand", "aria-label": "Pocketful home"}, text("span", "P", {class: "brand-mark", "aria-hidden": "true"}), "pocketful"), nav,
      text("p", "A little more clarity.\nA little less settling up.", {class: "sidebar-note"})),
      el("div", {class: "main-wrap"}, el("header", {class: "topbar"}, text("span", "Money, together.", {class: "top-caption"}), refs.identity), refs.main)));
    renderIdentity();
  }
  function authScreen() {
    const signup = route === "/signup", prefix = signup ? "signup" : "login";
    const form = el("form", {class: "card auth-form", novalidate: true});
    const email = field(`${prefix}-email`, "Email address", {type: "email", autocomplete: "email", wide: true, placeholder: "you@example.com"});
    const password = field(`${prefix}-password`, "Password", {type: "password", autocomplete: signup ? "new-password" : "current-password", wide: true, help: signup ? "At least 8 characters" : null});
    const name = signup ? field("signup-display-name", "Your name", {autocomplete: "name", wide: true}) : null;
    const button = text("button", signup ? "Create account" : "Sign in", {type: "submit", class: "primary", ...test(`${prefix}-submit`)});
    const status = el("div", {"aria-live": "polite"});
    form.append(cardTitle(signup ? "Make yourself at home" : "Welcome back", signup ? "Your wallet starts here." : "Sign in to pick up where you left off."),
      el("div", {class: "fields"}, name?.wrap, email.wrap, password.wrap), el("div", {class: "form-actions"}, button), status,
      el("p", {class: "auth-switch"}, signup ? "Already have an account? " : "New to Pocketful? ", el("a", {href: signup ? "/login" : "/signup"}, signup ? "Sign in" : "Create an account")));
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (button.disabled) return;
      clearFeedback(status); button.disabled = true;
      try {
        const user = await api(`/auth/${signup ? "signup" : "login"}`, {method: "POST", body: JSON.stringify({email: email.input.value.trim(), password: password.input.value, ...(name ? {display_name: name.input.value} : {})})});
        token = user.token; localStorage.setItem("pocketful.session", token); window.location.assign("/");
      } catch (error) { feedback(status, "auth-error", "error", error.code === "unauthenticated" ? "That email and password don’t match. Please try again." : message(error)); }
      finally { button.disabled = false; }
    });
    refs.main.append(el("div", {class: "auth-layout"}, el("section", {class: "auth-story"}, text("div", "P", {class: "auth-shape", "aria-hidden": "true"}),
      text("p", "YOUR EVERYDAY WALLET", {class: "eyebrow"}), text("h1", "Good things are better shared."), text("p", "Pay a friend, split the little things, and know exactly where your money stands.")), form));
    if (token) api("/me").then(user => { me = user; renderIdentity(); }).catch(() => {});
  }
  function protectedScreen() {
    const titles = {"/": ["Your wallet", "A clear view of your money, and the people you share it with."],
      "/requests": ["Requests", "Stay on top of what’s coming in and what you owe."],
      "/split": ["Split a bill", "Keep the good part of sharing. We’ll handle the arithmetic."],
      "/authorizations": ["Authorizations", "Money set aside. Ready to collect, or release when plans change."],
      "/statements": ["Statements", "Your money movements, with the full story behind the numbers."],
      "/history": ["History & corrections", "Look back with confidence. Original receipts always stay original."],
      "/operations": ["Operations", "Apply related corrections together, with every original receipt preserved."]};
    const [title, caption] = titles[route] || titles["/"];
    const refreshButton = text("button", "↻ Refresh", {type: "button", class: "secondary", ...test("wallet-refresh"), onclick: refresh});
    refs.main.append(el("section", {class: "heading"}, el("div", {}, text("p", "POCKETFUL / " + (route === "/" ? "OVERVIEW" : title.toUpperCase()), {class: "eyebrow"}), text("h1", title), text("p", caption, {class: "subtext"})), refreshButton), refs.global, refs.loading);
    refs.balance = el("section", {class: "balance-card", "aria-label": "Wallet funds"}, text("p", "Loading your balance…", {class: "balance-label"}));
    refs.main.append(refs.balance);
    if (route === "/") {
      refs.activity = el("div", {}, text("p", "Loading activity…", {class: "subtext"}));
      refs.main.append(el("div", {class: "workspace home-workspace"}, el("div", {class: "column"}, transferForm("pay"), transferForm("request"), transferForm("authorize")),
        el("section", {class: "card activity-card"}, cardTitle("Recent activity", "Your payments, and what’s been shared.", "PAYMENTS"), refs.activity)));
    } else if (route === "/requests") {
      refs.requestFeedback = el("div", {"aria-live": "polite"}); refs.requestsEmpty = el("div");
      refs.incoming = el("div", {class: "record-list", ...test("incoming-list")}); refs.outgoing = el("div", {class: "record-list", ...test("outgoing-list")});
      refs.main.append(refs.requestFeedback, refs.requestsEmpty, el("div", {class: "requests-grid"},
        el("section", {class: "card"}, cardTitle("Incoming", "Requests for you to pay."), refs.incoming),
        el("section", {class: "card"}, cardTitle("Outgoing", "Requests you’ve sent."), refs.outgoing)));
    } else if (route === "/split") refs.main.append(el("div", {class: "single-column"}, splitScreen()));
    else if (route === "/authorizations") {
      refs.authorizationFeedback = el("div", {"aria-live": "polite"}); refs.authorizations = el("div");
      refs.main.append(refs.authorizationFeedback, el("div", {class: "workspace"}, el("section", {class: "card"}, cardTitle("Your authorizations", "Only you and the other person can see an open hold."), refs.authorizations),
        el("div", {class: "column"}, transferForm("authorize"))));
    }
    else if (route === "/statements") refs.main.append(statementScreen());
    else if (route === "/history") refs.main.append(historyScreen());
    else if (route === "/operations") refs.main.append(operationsScreen());
    refresh();
  }
  shell();
  if (route === "/login" || route === "/signup") authScreen();
  else if (!token) window.location.replace("/login");
  else protectedScreen();
})();
