const money = (n) =>
  n == null ? "\u2014" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });

function banner(text, kind) {
  const b = document.getElementById("banner");
  b.textContent = text;
  b.className = "banner " + (kind || "");
  b.hidden = !text;
}

// His own model: a fortnightly allowance, not a bank balance. Rent, loans,
// insurance, subscriptions and the parking spot never touch this number.
// Split out of load() so saving an answer can redraw just this.
function paint(p) {
  const el = document.getElementById("wallet");
  el.textContent = money(Math.max(p.left, 0));
  el.style.color = p.left < 0 ? "#f08c8c" : "";
  document.getElementById("wallet-label").textContent =
    p.left < 0 ? "Out of spending money" : "Left until payday";
  const line = (k, v, cls) =>
    `<div class="l ${cls || ""}"><span>${k}</span><span>${v}</span></div>`;
  document.getElementById("wallet-sum").innerHTML =
    line("This paycheck", money(p.per_period)) +
    (p.set_aside ? line(`Set aside ${p.set_aside_label}`, "-" + money(p.set_aside)) : "") +
    (p.moved_in ? line(`Taken out ${p.moved_in_label}`, "-" + money(p.moved_in)) : "") +
    (Math.round(p.rolled_over)
      ? line(p.rolled_over > 0 ? "Left over from last paycheck" : "Overspent last paycheck",
             (p.rolled_over > 0 ? "+" : "-") + money(Math.abs(p.rolled_over)))
      : "") +
    (p.paid_back ? line("Money back in", "+" + money(p.paid_back)) : "") +
    line("Spent since payday", "-" + money(p.spent)) +
    line(p.left < 0 ? "Over by" : "Left", money(Math.abs(p.left)), "tot") +
    `<div class="l" style="color:var(--muted)"><span>Next payday ${p.next_payday}</span>` +
    `<span>${p.days_to_payday} days</span></div>`;
}

async function load() {
  const s = await (await fetch("/api/status")).json();
  await loadTags();
  await loadMessages();

  document.getElementById("tx-count").textContent = s.counts.transactions;
  document.getElementById("acct-count").textContent = s.counts.accounts;
  document.getElementById("item-count").textContent = s.counts.items;

  const plaid = document.getElementById("plaid-state");
  plaid.textContent = s.plaid_configured ? `ready (${s.plaid_env})` : "no keys yet";
  plaid.className = "pill " + (s.plaid_configured ? "on" : "off");

  const dbPill = document.getElementById("db-state");
  dbPill.textContent = "ready";
  dbPill.className = "pill on";

  paint(s.pay);

  if (s.accounts.length) {
    const net = s.accounts.reduce((t, a) => {
      const bal = a.current_balance ?? 0;
      return t + (a.type === "credit" || a.type === "loan" ? -bal : bal);
    }, 0);
    document.getElementById("networth").textContent = money(net);

    document.getElementById("accounts-card").hidden = false;
    document.getElementById("accounts").innerHTML = s.accounts
      .map((a) => {
        const bal = a.type === "credit" || a.type === "loan"
          ? -(a.current_balance ?? 0)
          : (a.current_balance ?? 0);
        return `<div class="row"><span class="k">${a.name}${a.mask ? " ··" + a.mask : ""}
          <em>${a.subtype || a.type}</em></span><span>${money(bal)}</span></div>`;
      })
      .join("");
  }


  document.getElementById("tr-count").textContent = s.transfers_excluded;
  document.getElementById("spent").textContent = money(s.spent_this_month);

  renderHealth(s.banks, s.silent_accounts);
  await loadCategories();
  if (s.counts.transactions) await loadTransactions();
  await loadReview(s.review_count);
}

async function loadTransactions() {
  const d = await (await fetch("/api/transactions")).json();
  if (!d.transactions.length) return;

  document.getElementById("tx-card").hidden = false;
  document.getElementById("transactions").innerHTML = d.transactions
    .map((t) => {
      const incoming = t.amount > 0;
      return `<div class="tx">
        <div class="left">
          <div class="what">${t.what || "Transaction"}${t.pending ? '<span class="tag">pending</span>' : ""}${t.is_transfer ? `<span class="tag tr">${(t.transfer_kind||"transfer").replace("_"," ")}</span>` : ""}${t.needs_review ? '<span class="tag">review</span>' : ""}</div>
          <div class="meta">${t.date} &middot; ${t.account}</div>
          ${t.is_transfer ? "" : `<select data-tx="${t.id}">${catOptions(t.category_id)}</select>`}
          ${tellBlock(t)}
        </div>
        <div class="amt ${incoming ? "in" : ""}">${money(t.amount)}</div>
      </div>`;
    })
    .join("");

  // Pending charges are real money that is gone, so say so out loud. They sit
  // under the wallet arithmetic rather than being hidden in a tooltip.
  const sums = document.getElementById("wallet-sum");
  if (d.pending_total && sums && !sums.querySelector(".pend-line")) {
    sums.insertAdjacentHTML(
      "beforeend",
      `<div class="l pend-line"><span>Pending charges, already counted</span>` +
      `<span>${money(Math.abs(d.pending_total))}</span></div>`
    );
  }
}

async function refresh() {
  const btn = document.getElementById("refresh");
  btn.disabled = true;
  btn.textContent = "Syncing...";
  banner("");
  try {
    const r = await fetch("/api/sync", { method: "POST" });
    const out = await r.json();
    if (!r.ok) throw new Error(out.detail);
    const bits = [`${out.added} new`, `${out.modified} updated`, `${out.removed} removed`];
    banner(`Synced ${out.banks} bank${out.banks === 1 ? "" : "s"}: ${bits.join(", ")}.`,
           out.errors.length ? "bad" : "good");
    if (out.errors.length) banner(out.errors.join(" | "), "bad");
    await load();
  } catch (e) {
    banner(String(e.message || e), "bad");
  }
  btn.disabled = false;
  btn.textContent = "Refresh";
}

async function connect() {
  const btn = document.getElementById("connect");
  btn.disabled = true;
  btn.textContent = "Opening...";
  banner("");
  try {
    const res = await fetch("/api/link/token", { method: "POST" });
    if (!res.ok) throw new Error((await res.json()).detail);
    const { link_token } = await res.json();

    const handler = Plaid.create({
      token: link_token,
      onSuccess: async (public_token) => {
        banner("Saving the connection...", "");
        const r = await fetch("/api/link/exchange", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ public_token }),
        });
        const out = await r.json();
        if (!r.ok) {
          banner(out.detail || "Linking failed.", "bad");
        } else {
          banner(`Connected ${out.institution || "bank"}: ${out.accounts_added} accounts.`, "good");
          await load();
          await refresh();
        }
        btn.disabled = false;
        btn.textContent = "Connect a bank";
      },
      onExit: (err) => {
        banner(err ? `Link closed: ${err.error_message || err.error_code}` : "", err ? "bad" : "");
        btn.disabled = false;
        btn.textContent = "Connect a bank";
      },
    });
    handler.open();
  } catch (e) {
    banner(String(e.message || e), "bad");
    btn.disabled = false;
    btn.textContent = "Connect a bank";
  }
}

document.getElementById("connect").addEventListener("click", connect);
document.getElementById("refresh").addEventListener("click", refresh);
load().catch(() => banner("Could not reach the server.", "bad"));


async function loadReview(count) {
  const card = document.getElementById("review-card");
  if (!count) { card.hidden = true; return; }
  const d = await (await fetch("/api/review")).json();
  document.getElementById("review-count").textContent = d.items.length;
  card.hidden = d.items.length === 0;

  document.getElementById("review-list").innerHTML = d.items
    .map(
      (r) => `<div class="ritem">
        <div class="top">
          <div><strong>${r.what || "Transaction"}</strong>
            <div class="why">${r.date} &middot; ${r.account}</div>
            <div class="why">${r.review_reason || ""}</div>
          </div>
          <div class="amt">${money(r.amount)}</div>
        </div>
        <div class="choices">
          <button class="chip" data-id="${r.id}" data-res="expense">It was spending</button>
          <button class="chip" data-id="${r.id}" data-res="transfer">Transfer to an untracked account</button>
          <button class="chip" data-id="${r.id}" data-res="reimbursement">Reimbursement</button>
        </div>
      </div>`
    )
    .join("");

  document.querySelectorAll("#review-list .chip").forEach((b) =>
    b.addEventListener("click", async () => {
      await fetch("/api/review/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transaction_id: Number(b.dataset.id),
          resolution: b.dataset.res,
        }),
      });
      await load();
    })
  );
}


// ---------- categories ----------

let CATEGORIES = [];

function catOptions(selected) {
  const opts = ['<option value="">uncategorised</option>'];
  for (const c of CATEGORIES) {
    const label = c.is_active ? c.name : `${c.name} (inactive)`;
    opts.push(`<option value="${c.id}"${c.id === selected ? " selected" : ""}>${label}</option>`);
  }
  return opts.join("");
}

async function loadCategories() {
  const d = await (await fetch("/api/categories")).json();
  CATEGORIES = d.categories;
  const box = document.getElementById("categories");
  box.innerHTML = CATEGORIES.length
    ? `<div class="cats">${CATEGORIES.map(
        (c) => `<span class="cat ${c.is_active ? "" : "off"}">${c.name}<span class="n">${c.used}</span></span>`
      ).join("")}</div>`
    : '<div class="pending">None yet. A category says what the money was for, never which account it came from.</div>';
}

async function addCategory() {
  const input = document.getElementById("cat-name");
  const err = document.getElementById("cat-error");
  const name = input.value.trim();
  if (!name) return;
  const r = await fetch("/api/categories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) {
    err.textContent = (await r.json()).detail;
    err.hidden = false;
    return;
  }
  err.hidden = true;
  input.value = "";
  await loadCategories();
}

document.getElementById("cat-add").addEventListener("click", addCategory);
document.getElementById("cat-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addCategory();
});

// Assigning a category to a transaction.
document.addEventListener("change", async (e) => {
  const sel = e.target.closest("#transactions select[data-tx]");
  if (!sel) return;
  await fetch("/api/transactions/category", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      transaction_id: Number(sel.dataset.tx),
      category_id: sel.value ? Number(sel.value) : null,
    }),
  });
  await loadCategories();
});

// ---------- telling Claude about a transaction ----------
//
// The automatic rules cannot know that a Zelle credit was a partner paying their
// half, or that a Costco run came out of money set aside. He can, so he gets to
// say so, and what he says overrides the rules.

let TAGS = [];

async function loadTags() {
  try {
    TAGS = (await (await fetch("/api/tags")).json()).tags || [];
  } catch (e) {
    TAGS = [];
  }
}

function tellBlock(t) {
  const chosen = TAGS.find((x) => x.id === t.tag);
  const summary = chosen ? chosen.label : t.note ? "Note added" : "Tell Claude";
  return `<details class="tell"${t.tag || t.note ? " open" : ""}>
    <summary class="${t.tag || t.note ? "set" : ""}">${esc(summary)}</summary>
    <div class="tellbody">
      ${TAGS.map(
        (x) => `<label class="opt"><input type="radio" name="tag-${t.id}" value="${x.id}"${
          x.id === t.tag ? " checked" : ""
        }> ${esc(x.label)}</label>`
      ).join("")}
      <label class="opt"><input type="radio" name="tag-${t.id}" value=""${
        t.tag ? "" : " checked"
      }> Work it out from the category</label>
      <textarea data-note="${t.id}" rows="2" placeholder="Anything else Claude should know">${esc(
        t.note || ""
      )}</textarea>
      <button class="btn ghost" data-save="${t.id}">Save</button>
    </div>
  </details>`;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("#transactions button[data-save]");
  if (!btn) return;
  const id = Number(btn.dataset.save);
  const picked = document.querySelector(`input[name="tag-${id}"]:checked`);
  const note = document.querySelector(`textarea[data-note="${id}"]`);
  btn.disabled = true;
  btn.textContent = "Saving...";
  try {
    const r = await fetch("/api/transactions/note", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transaction_id: id,
        tag: picked && picked.value ? picked.value : null,
        note: note ? note.value : null,
      }),
    });
    const d = await r.json();
    if (d.pay) paint(d.pay);
    await loadTransactions();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Save";
  }
});

// ---------- telling Claude something not about one transaction ----------

async function loadMessages() {
  try {
    const d = await (await fetch("/api/messages")).json();
    document.getElementById("msg-list").innerHTML = (d.messages || [])
      .map(
        (m) =>
          `<div class="msg"><div class="meta">${esc(m.created_at)}</div>${esc(m.body)}</div>`
      )
      .join("");
  } catch (e) {
    /* the card still works without the history */
  }
}

document.getElementById("msg-send").addEventListener("click", async () => {
  const box = document.getElementById("msg-body");
  const status = document.getElementById("msg-status");
  const text = box.value.trim();
  if (!text) return;
  try {
    const r = await fetch("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: text }),
    });
    if (!r.ok) throw new Error(await r.text());
    box.value = "";
    status.textContent = "Saved for Claude.";
    await loadMessages();
  } catch (e) {
    status.textContent = "Couldn't save that.";
  }
});

// ---------- connection health ----------

function renderHealth(banks, silent) {
  const box = document.getElementById("health");
  const broken = (banks || []).filter((b) => b.status !== "healthy");
  const silentWarnings = (silent || []).map(
    (a) => `<div class="health">
      <div>
        <strong>${a.institution_name} is not reporting ${a.name}${a.mask ? " ··" + a.mask : ""}</strong>
        <div class="why">The bank sends no transactions for this account while its other
          accounts report fine, so anything on it is invisible here and its payments can
          never be matched. Check this one in the bank's own app.</div>
      </div>
    </div>`
  );

  box.innerHTML = silentWarnings.join("") + broken
    .map(
      (b) => `<div class="health">
        <div>
          <strong>${b.institution_name || "A bank"} needs reconnecting</strong>
          <div class="why">${b.last_sync_error || "The connection stopped working."}<br>
            Last good sync: ${b.last_sync_at || "never"}.</div>
        </div>
        <button class="btn" data-reconnect="${b.id}">Reconnect</button>
      </div>`
    )
    .join("");

  box.querySelectorAll("[data-reconnect]").forEach((b) =>
    b.addEventListener("click", () => reconnect(Number(b.dataset.reconnect)))
  );
}

async function reconnect(itemId) {
  banner("");
  try {
    const r = await fetch("/api/link/token/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: itemId }),
    });
    const out = await r.json();
    if (!r.ok) throw new Error(out.detail);
    Plaid.create({
      token: out.link_token,
      onSuccess: async () => {
        banner("Reconnected. Syncing...", "good");
        await refresh();
      },
      onExit: (err) => {
        if (err) banner(`Reconnect cancelled: ${err.error_message || err.error_code}`, "bad");
      },
    }).open();
  } catch (e) {
    banner(String(e.message || e), "bad");
  }
}


// ---------- envelopes ----------

function renderEnvelopes(env) {
  const box = document.getElementById("envelopes");
  if (!env.envelopes.length) {
    box.innerHTML = '<div class="pending">Nothing set aside yet. Add what you have already committed ' +
      'for the month and the wallet number above becomes what is genuinely free.</div>';
    return;
  }
  box.innerHTML = env.envelopes
    .map((e) => {
      const pct = e.amount > 0 ? Math.min(100, (e.spent / e.amount) * 100) : 0;
      const over = e.overspent_by > 0;
      const sub = over
        ? `Over by ${money(e.overspent_by)} — this is now coming out of your wallet`
        : `${money(e.still_fenced)} left, still fenced off`;
      return `<div class="env ${over ? "over" : ""}">
        <div class="top">
          <span class="nm">${e.name}</span>
          <span class="fig">${money(e.spent)} of ${money(e.amount)}
            <button class="x" data-del="${e.id}" title="Remove">&times;</button></span>
        </div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <div class="sub">${sub}</div>
      </div>`;
    })
    .join("");

  box.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      await fetch(`/api/envelopes/${b.dataset.del}`, { method: "DELETE" });
      await load();
    })
  );
}

async function addEnvelope() {
  const name = document.getElementById("env-name");
  const amt = document.getElementById("env-amt");
  const err = document.getElementById("env-error");
  const value = parseFloat(String(amt.value).replace(/[^0-9.\-]/g, ""));
  if (!name.value.trim() || isNaN(value)) {
    err.textContent = "Give it a name and an amount.";
    err.hidden = false;
    return;
  }
  const r = await fetch("/api/envelopes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.value.trim(), amount: value }),
  });
  if (!r.ok) {
    err.textContent = (await r.json()).detail;
    err.hidden = false;
    return;
  }
  err.hidden = true;
  name.value = "";
  amt.value = "";
  await load();
}

document.getElementById("env-add").addEventListener("click", addEnvelope);
document.getElementById("env-amt").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addEnvelope();
});
