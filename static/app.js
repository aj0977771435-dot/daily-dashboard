// 每日看板前端：每 15 秒跟自己的後端（/api/data）要資料，後端才是真正去外面抓資料的地方。
const POLL_MS = 15000;
const state = { filter: "all", day: 0, data: null, failed: false };

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 2) => n == null ? "—" : Number(n).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });
const ntd = (n) => n == null ? "—" : "NT$" + Number(n).toLocaleString("zh-TW", { maximumFractionDigits: 0 });

const tpeFmt = new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
const tpeTime = (iso) => iso ? tpeFmt.format(new Date(iso)) : "—";
const tpeDate = (offset) => {
  const t = new Date(Date.now() + offset * 86400000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(t); // YYYY-MM-DD
};

function foot(left, right = "") {
  return `<div class="foot"><span>資料時間：${esc(left)}</span><span>${right}</span></div>`;
}
function staleNote(d) {
  return d && d.ok === false ? `<div class="warn">暫時抓不到${d.price != null || d.best || d.games?.length ? "，顯示上一次的資料" : ""}</div>` : "";
}

// ---------- 股票
function stockCard(s) {
  const tw = s.market === "TW";
  const tag = tw ? `<span class="tag tw">股票・台股</span>` : `<span class="tag us">股票・美股</span>`;
  const unit = tw ? "元" : "美元";
  let body;
  if (s.price != null && s.prev_close) {
    const ch = s.price - s.prev_close, pct = ch / s.prev_close * 100;
    const cls = ch > 0 ? "up" : ch < 0 ? "down" : "";
    const sign = ch > 0 ? "▲ +" : ch < 0 ? "▼ " : "";
    body = `<div class="price ${cls}">${fmt(s.price)}<span class="unit">${unit}</span></div>
      <div class="chg ${cls}">${sign}${fmt(ch)}（${sign.replace("▲ ", "").replace("▼ ", "")}${fmt(pct)}%）</div>
      <div class="sub">昨收 ${fmt(s.prev_close)} ・ 來源：${esc(s.source || "")}</div>`;
  } else if (s.note === "尚無成交") {
    body = `<div class="price">尚無成交</div>
      <div class="sub">最佳買價 ${fmt(s.bid)} ／ 最佳賣價 ${fmt(s.ask)}</div>
      <div class="sub">昨收 ${fmt(s.prev_close)}</div>`;
  } else {
    body = `<div class="price muted">—</div><div class="sub">${esc(s.error || "暫時抓不到")}</div>`;
  }
  const link = `<a href="https://tw.stock.yahoo.com/quote/${encodeURIComponent(s.symbol)}${tw ? (s.ex === "otc" ? ".TWO" : ".TW") : ""}" target="_blank" rel="noopener">Yahoo 股市 ↗</a>`;
  return `<article class="card" data-kind="stock">${tag}
    <div class="name">${esc(s.symbol)} <span class="sub">${esc(s.name)}</span></div>
    ${staleNote(s)}${body}
    ${foot(s.data_time || tpeTime(s.fetched), link)}</article>`;
}

// ---------- NBA
function periodText(g) {
  const p = g.period;
  const name = p > 4 ? `延長賽${p > 5 ? p - 4 : ""}` : `第${p}節`;
  if (/half/i.test(g.status_text)) return "中場休息";
  if (!g.clock || g.clock === "0:00" || g.clock === "0.0") return `${name}結束`;
  return `${name} 剩 ${g.clock}`;
}
function gameCard(g, nba) {
  const live = g.status === "live", fin = g.status === "final";
  const as = g.away.score, hs = g.home.score;
  const awayWin = fin && as > hs, homeWin = fin && hs > as;
  const row = (t, isHome, win) => `<div class="team ${fin ? (win ? "win" : "lose") : ""}">
      <span>${esc(t.name)}${isHome ? '<span class="home-mark">主</span>' : ""}</span>
      <span class="score">${g.status === "upcoming" ? "" : esc(t.score ?? "")}</span></div>`;
  let st;
  if (live) st = `<div class="state live">${esc(periodText(g))}</div>`;
  else if (fin) st = `<div class="state">已結束${g.period > 4 ? "（延長賽）" : ""}</div>`;
  else st = `<div class="state">台灣時間開打</div><div class="tip">${esc(tpeTime(g.start))}</div>`;
  const hupu = nba.hupu ? `<a class="hupu-btn" href="${esc(nba.hupu.replace("{gameId}", g.id))}" target="_blank" rel="noopener">虎撲評分 ↗</a>` : "";
  return `<article class="card" data-kind="nba">
    <span class="tag nba">NBA</span>${live ? '<span class="live-dot">進行中</span>' : ""}
    ${staleNote(nba.meta)}${st}
    ${row(g.away, false, awayWin)}${row(g.home, true, homeWin)}
    ${foot(tpeTime(nba.meta.fetched), `<span class="hupu-slot">${hupu}</span> <a href="${esc(g.url)}" target="_blank" rel="noopener">ESPN 比賽頁 ↗</a>`)}</article>`;
}

// ---------- 3C
function productCard(p) {
  const hasOfficial = p.official_price != null && p.official_price !== "";
  let body = hasOfficial
    ? `<div class="sub">官方價</div><div class="price">${ntd(p.official_price)}</div>`
    : `<div class="sub">各通路最低價（未填官方價）</div><div class="price">${p.best ? ntd(p.best.price) : "—"}</div>`;
  if (p.best) {
    const diff = (p.official_price || 0) - p.best.price;
    const cls = diff > 0 ? "down" : diff < 0 ? "up" : "";
    const shopLink = (o, arrow = "") => o.url
      ? `<a href="${esc(o.url)}" target="_blank" rel="noopener" title="${esc(o.title)}">${esc(o.store)}${arrow}</a>`
      : `<span>${esc(o.store)}</span>`;
    body += `<div class="chg">最低 ${ntd(p.best.price)} ・ ${shopLink(p.best, " ↗")}</div>
      ${hasOfficial ? `<div class="sub ${cls}">${diff > 0 ? `比官方便宜 ${ntd(diff)}` : diff < 0 ? `比官方貴 ${ntd(-diff)}` : "與官方價相同"}</div>` : ""}
      <ul class="offers">${(p.offers || []).map((o) => `<li>${shopLink(o)}<span>${ntd(o.price)}</span><small class="offer-title">${esc(o.title)}</small></li>`).join("")}</ul>
      <div class="sub small">比對到 ${p.matched} 筆（關鍵字「${esc(p.keyword)}」）</div>`;
  } else if (p.ok) {
    body += `<div class="sub">各通路都沒找到符合的商品，只顯示官方價</div>`;
  } else {
    body += `<div class="sub">通路價暫時抓不到，只顯示官方價</div>`;
  }
  const link = p.official_url ? `<a href="${esc(p.official_url)}" target="_blank" rel="noopener">官網 ↗</a>` : "";
  return `<article class="card" data-kind="3c"><span class="tag c3">3C 價格</span>
    <div class="name">${esc(p.name)}</div>${p.best ? staleNote(p) : ""}${body}
    ${foot(p.fetched ? tpeTime(p.fetched) : "—", link)}</article>`;
}

function infoCard() {
  return `<article class="card info" data-kind="info"><span class="tag info">說明</span>
    <div class="name">在台灣合法看 NBA</div>
    <ul>
      <li><b>緯來（LiTV）</b>：本季台灣獨家。YouTube 頻道會員每月 NT$100，每週約 9 場。
        <a href="https://sport.videoland.com.tw/" target="_blank" rel="noopener">緯來體育 ↗</a></li>
      <li><b>NBA League Pass</b>：官方串流。
        <a href="https://www.nba.com/nba-league-pass-faqs" target="_blank" rel="noopener">官方說明 ↗</a> ・
        <a href="https://support.watch.nba.com/hc/zh-tw/articles/115000585813" target="_blank" rel="noopener">中文購買說明 ↗</a></li>
    </ul></article>`;
}

// ---------- 排版：進行中比賽最前，接著 股票／即將開打／3C／已結束 輪流穿插
function interleave(lists) {
  const out = [];
  for (let i = 0; lists.some((l) => i < l.length); i++)
    for (const l of lists) if (i < l.length) out.push(l[i]);
  return out;
}

function render() {
  const d = state.data;
  if (!d) return;
  const day = tpeDate(state.day);
  const nba = { meta: d.nba || {}, hupu: d.hupu_url_template };
  const games = (d.nba.games || []).filter((g) => g.date === day);
  const live = games.filter((g) => g.status === "live").map((g) => gameCard(g, nba));
  const upcoming = games.filter((g) => g.status === "upcoming").map((g) => gameCard(g, nba));
  const finals = games.filter((g) => g.status === "final").map((g) => gameCard(g, nba));
  const stocks = d.stocks.map(stockCard);
  const products = d.products.map(productCard);

  const f = state.filter;
  const show = (k) => f === "all" || f === k;
  let cards = [
    ...(show("nba") ? live : []),
    ...interleave([show("stock") ? stocks : [], show("nba") ? upcoming : [], show("3c") ? products : [], show("nba") ? finals : []]),
  ];
  if (show("nba") && !games.length)
    cards.push(`<article class="card" data-kind="nba"><span class="tag nba">NBA</span>
      <div class="name">${nba.meta.ok === false && !(d.nba.games || []).length
        ? "NBA 比賽資料暫時抓不到" : `${["昨天", "今天", "明天"][state.day + 1]}（${day}）沒有比賽`}</div>
      ${staleNote(nba.meta)}${foot(tpeTime(nba.meta.fetched))}</article>`);
  if (show("nba")) cards.push(infoCard());
  $("#grid").innerHTML = cards.join("") || `<div class="empty">沒有資料</div>`;
}

async function poll() {
  try {
    const r = await fetch("/api/data", { cache: "no-store" });
    state.data = await r.json();
    state.failed = false;
  } catch {
    state.failed = true;  // 後端沒回應：保留畫面上的舊資料
  }
  $("#status").textContent = state.failed
    ? "⚠ 連不到看板程式（黑色視窗是不是被關掉了？），畫面顯示的是上一次的資料。"
    : `畫面每 15 秒自動更新 ・ 最後更新 ${tpeTime(state.data.now)}`;
  render();
}

function tick() {
  $("#clock").textContent = "台灣時間 " + new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date());
}

$("#filters").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.filter = b.dataset.f;
  document.querySelectorAll("#filters button").forEach((x) => x.classList.toggle("on", x === b));
  $("#days").classList.toggle("hidden", state.filter === "stock" || state.filter === "3c");
  render();
});
$("#days").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.day = Number(b.dataset.d);
  document.querySelectorAll("#days button").forEach((x) => x.classList.toggle("on", x === b));
  render();
});

// ---------- 設定面板：在網頁上改自選股與 3C 商品，存回 config.json
const dlg = $("#settings");
const val = (el, sel) => el.querySelector(sel).value.trim();

function stockRow(s = { symbol: "", name: "", market: "TW" }) {
  const div = document.createElement("div");
  div.className = "row stock-row";
  div.innerHTML = `<input class="s-symbol" placeholder="代號" value="${esc(s.symbol)}" aria-label="股票代號">
    <input class="s-name" placeholder="名稱（可留空）" value="${esc(s.name)}" aria-label="股票名稱">
    <select class="s-market" aria-label="市場"><option value="TW">台股</option><option value="US">美股</option></select>
    <button type="button" class="del">刪除</button>`;
  div.querySelector(".s-market").value = (s.market || "TW").toUpperCase();
  div.querySelector(".del").onclick = () => div.remove();
  $("#stock-rows").append(div);
}

function urlRow(box, u = { store: "", url: "" }) {
  const div = document.createElement("div");
  div.className = "row url-row";
  div.innerHTML = `<input class="u-store" placeholder="店家（可留空）" value="${esc(u.store)}" aria-label="店家">
    <input class="u-url" placeholder="查價網址 https://…" value="${esc(u.url)}" aria-label="查價網址">
    <button type="button" class="del">刪除</button>`;
  div.querySelector(".del").onclick = () => div.remove();
  box.append(div);
}

function productBox(p = { name: "", official_price: "", official_url: "", price_urls: [] }) {
  const box = document.createElement("div");
  box.className = "product";
  box.innerHTML = `<div class="row product-row">
      <input class="p-name" placeholder="商品名稱" value="${esc(p.name)}" aria-label="商品名稱">
      <input class="p-price" placeholder="官方價（不知道可留空）" inputmode="numeric" value="${esc(p.official_price)}" aria-label="官方價">
      <button type="button" class="del">刪除商品</button></div>
    <div class="row official-row"><input class="p-url" placeholder="官網網址（可留空）" value="${esc(p.official_url)}" aria-label="官網網址"></div>
    <div class="row search-row">
      <label>搜尋關鍵字<input class="p-search" placeholder="留空就用商品名稱" value="${esc(p.search || "")}"></label>
      <label>排除的字<input class="p-exclude" placeholder="例如 Max, 殼（用逗號分開）" value="${esc((p.exclude || []).join(", "))}"></label>
    </div>
    <div class="urls"></div>
    <button type="button" class="add small-add">＋ 另外指定商品頁（可不填）</button>`;
  const urls = box.querySelector(".urls");
  (p.price_urls || []).forEach((u) => urlRow(urls, typeof u === "string" ? { store: "", url: u } : u));
  box.querySelector(".del").onclick = () => box.remove();
  box.querySelector(".small-add").onclick = () => urlRow(urls);
  $("#product-rows").append(box);
}

function setMsg(text, cls = "") {
  $("#settings-msg").textContent = text;
  $("#settings-msg").className = "small " + cls;
}

$("#open-settings").onclick = async () => {
  setMsg("");
  try {
    const cfg = await (await fetch("/api/settings", { cache: "no-store" })).json();
    $("#stock-rows").innerHTML = ""; $("#product-rows").innerHTML = "";
    cfg.stocks.forEach(stockRow);
    cfg.products.forEach(productBox);
    dlg.showModal();
  } catch {
    alert("連不到看板程式，請確認黑色視窗還開著。");
  }
};
$("#add-stock").onclick = () => { stockRow(); $("#stock-rows").lastElementChild.querySelector("input").focus(); };
$("#add-product").onclick = () => { productBox(); $("#product-rows").lastElementChild.querySelector("input").focus(); };
$("#cancel-settings").onclick = () => dlg.close();

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    stocks: [...document.querySelectorAll(".stock-row")].map((r) => ({
      symbol: val(r, ".s-symbol"), name: val(r, ".s-name"), market: r.querySelector(".s-market").value })),
    products: [...document.querySelectorAll(".product")].map((b) => ({
      name: val(b, ".p-name"), official_price: val(b, ".p-price").replace(/[,，NT$\s]/g, ""),
      official_url: val(b, ".p-url"), search: val(b, ".p-search"), exclude: val(b, ".p-exclude"),
      price_urls: [...b.querySelectorAll(".url-row")].map((u) => ({ store: val(u, ".u-store"), url: val(u, ".u-url") })) })),
  };
  setMsg("儲存中…");
  try {
    const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const res = await r.json();
    if (!r.ok) return setMsg("沒有儲存：" + (res.error || "發生錯誤"), "err");
    setMsg("已儲存！新資料幾秒內會出現。", "ok");
    setTimeout(() => dlg.close(), 700);
    setTimeout(poll, 1000); setTimeout(poll, 5000); setTimeout(poll, 12000);
  } catch {
    setMsg("沒有儲存：連不到看板程式", "err");
  }
});

tick(); setInterval(tick, 1000);
poll(); setInterval(poll, POLL_MS);
