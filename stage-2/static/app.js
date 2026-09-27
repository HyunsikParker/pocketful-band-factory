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
  function formatted(value, units = me?.minor_units ?? 2, currency = me?.currency ?? "EUR") {
    const amount = BigInt(value);
    const scale = 10n ** BigInt(units);
    const whole = amount / scale;
    return `${whole}${units ? "." + (amount % scale).toString().padStart(units, "0") : ""} ${currency}`;
  }
  function decimal(value) {
    const amount = BigInt(value);
    const units = me.minor_units;
    const scale = 10n ** BigInt(units);
    return `${amount / scale}${units ? "." + (amount % scale).toString().padStart(units, "0") : ""}`;
  }
  function minor(raw) {
    if (!me) throw new Error("Your wallet is still loading. Please try again.");
    const input = raw.trim();
    if (!/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(input)) throw new Error("Enter an amount using digits and a decimal point.");
    const [whole, fraction = ""] = input.split(".");
    if (fraction.length > me.minor_units) throw new Error(`Use no more than ${me.minor_units} decimal places for ${me.currency}.`);
    const value = BigInt(whole || "0") * (10n ** BigInt(me.minor_units)) + BigInt(fraction.padEnd(me.minor_units, "0") || "0");
    if (value < 1n || value > 1000000000n) throw new Error("Enter an amount greater than zero and within the payment limit.");
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
        state.attempt = {version: state.version, signature, body: JSON.stringify(body), key: crypto.randomUUID()};
      }
      const attempt = state.attempt;
      const button = config.button;
      const originalLabel = button.textContent;
      state.busy = true; button.disabled = true; button.textContent = "Working…";
      feedback(config.feedback, `${config.name}-loading`, "loading", "Confirming your action…");
      let outcome;
      try {
        await api(config.path(), {method: "POST", body: attempt.body,
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
      if (outcome !== "uncertain") await refresh();
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
        el("div", {class: "record-meta"}, text("span", direction), badge(payment.visibility), text("time", dateLabel(payment.created_at), {datetime: payment.created_at}))));
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
    for (const [href, label, symbol] of [["/", "Wallet", "◈"], ["/requests", "Requests", "↔"], ["/split", "Split a bill", "÷"], ["/authorizations", "Authorizations", "◷"]]) {
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
      "/authorizations": ["Authorizations", "Money set aside. Ready to collect, or release when plans change."]};
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
    refresh();
  }
  shell();
  if (route === "/login" || route === "/signup") authScreen();
  else if (!token) window.location.replace("/login");
  else protectedScreen();
})();
