const API_ROOT = "https://api.dexscreener.com";
const WATCH_CHAINS = new Set(["ethereum", "solana", "bsc", "base"]);

const narratives = [
  { id: "AI", words: ["ai", "agent", "gpt", "llm", "bot", "compute", "model"] },
  { id: "Meme", words: ["meme", "dog", "cat", "pepe", "frog", "viral", "mascot"] },
  { id: "DeFi", words: ["defi", "yield", "swap", "vault", "stake", "liquidity", "lend"] },
  { id: "Gaming", words: ["game", "gaming", "play", "arena", "quest", "metaverse"] },
  { id: "RWA", words: ["rwa", "real world", "asset", "treasury", "bond", "credit"] },
  { id: "DePIN", words: ["depin", "infra", "node", "sensor", "storage", "wireless"] },
  { id: "Social", words: ["social", "creator", "community", "fan", "chat"] },
  { id: "Infra", words: ["layer", "rollup", "oracle", "bridge", "index", "protocol"] },
  { id: "NFT", words: ["nft", "collectible", "ordinal", "art"] },
  { id: "Privacy", words: ["privacy", "zk", "zero knowledge", "encrypt"] }
];

const state = {
  raw: [],
  enriched: [],
  sort: "score",
  minScore: 45,
  query: "",
  chains: new Set(WATCH_CHAINS)
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const fmt = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("en", { maximumFractionDigits: 1, signDisplay: "exceptZero" });

function normalizeList(value) {
  if (Array.isArray(value)) return value;
  if (!value) return [];
  return [value];
}

async function getJson(path) {
  const res = await fetch(`${API_ROOT}${path}`, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} returned ${res.status}`);
  return normalizeList(await res.json());
}

async function hydrateTokenBatch(chainId, addresses) {
  const chunks = [];
  for (let i = 0; i < addresses.length; i += 30) {
    chunks.push(addresses.slice(i, i + 30));
  }
  const responses = await Promise.allSettled(
    chunks.map((chunk) => getJson(`/tokens/v1/${chainId}/${chunk.join(",")}`))
  );
  return responses.flatMap((item) => item.status === "fulfilled" ? item.value : []);
}

function textFor(item, pair) {
  const links = normalizeList(item.links).map((link) => `${link.label || ""} ${link.type || ""} ${link.url || ""}`).join(" ");
  const socials = normalizeList(pair?.info?.socials).map((social) => `${social.platform || ""} ${social.handle || ""}`).join(" ");
  return [
    item.description,
    links,
    socials,
    pair?.baseToken?.name,
    pair?.baseToken?.symbol
  ].filter(Boolean).join(" ").toLowerCase();
}

function detectNarratives(item, pair) {
  const haystack = textFor(item, pair);
  const hits = narratives
    .filter((narrative) => narrative.words.some((word) => haystack.includes(word)))
    .map((narrative) => narrative.id);
  return hits.length ? hits : ["Emerging"];
}

function scoreToken(item, pair) {
  const volume = Number(pair?.volume?.h24 || 0);
  const liquidity = Number(pair?.liquidity?.usd || 0);
  const change = Number(pair?.priceChange?.h24 || 0);
  const txns = pair?.txns?.h24 || {};
  const buys = Number(txns.buys || 0);
  const sells = Number(txns.sells || 0);
  const boost = Number(item.amount || item.totalAmount || pair?.boosts?.active || 0);
  const created = Number(pair?.pairCreatedAt || 0);
  const ageHours = created ? Math.max(1, (Date.now() - created) / 36e5) : 720;
  const buyPressure = buys + sells > 0 ? buys / (buys + sells) : 0.5;

  let score = 18;
  score += Math.min(25, Math.log10(volume + 1) * 3.2);
  score += Math.min(18, Math.log10(liquidity + 1) * 2.2);
  score += Math.max(-16, Math.min(22, change / 4));
  score += Math.min(12, boost * 1.8);
  score += Math.max(-6, Math.min(10, (buyPressure - 0.5) * 28));
  score += ageHours < 48 ? 8 : ageHours < 168 ? 4 : 0;

  return Math.round(Math.max(0, Math.min(100, score)));
}

function tokenIdentity(item, pair) {
  return {
    address: item.tokenAddress,
    chainId: item.chainId,
    name: pair?.baseToken?.name || "Unlisted token",
    symbol: pair?.baseToken?.symbol || item.tokenAddress?.slice(0, 6) || "TKN",
    url: pair?.url || item.url || "#",
    image: pair?.info?.imageUrl || item.icon || item.header || "",
    description: item.description || pair?.baseToken?.name || "Fresh on-chain profile with sparse metadata.",
    pair
  };
}

async function loadRadar() {
  setHealth("scanning");
  $("#refreshBtn").disabled = true;
  try {
    const [profiles, boosts, topBoosts, takeovers] = await Promise.all([
      getJson("/token-profiles/latest/v1"),
      getJson("/token-boosts/latest/v1"),
      getJson("/token-boosts/top/v1"),
      getJson("/community-takeovers/latest/v1")
    ]);

    const merged = new Map();
    [...profiles, ...boosts, ...topBoosts, ...takeovers]
      .filter((item) => WATCH_CHAINS.has(item.chainId) && item.tokenAddress)
      .forEach((item) => {
        const key = `${item.chainId}:${item.tokenAddress}`.toLowerCase();
        merged.set(key, { ...merged.get(key), ...item });
      });

    state.raw = Array.from(merged.values()).slice(0, 220);
    const byChain = new Map();
    state.raw.forEach((item) => {
      const bucket = byChain.get(item.chainId) || [];
      bucket.push(item);
      byChain.set(item.chainId, bucket);
    });
    const pairs = [];
    for (const [chainId, items] of byChain.entries()) {
      const addresses = [...new Set(items.map((item) => item.tokenAddress))];
      pairs.push(...await hydrateTokenBatch(chainId, addresses));
    }
    const pairMap = new Map(pairs.map((pair) => [`${pair.chainId}:${pair.baseToken?.address}`.toLowerCase(), pair]));

    state.enriched = state.raw.map((item) => {
      const pair = pairMap.get(`${item.chainId}:${item.tokenAddress}`.toLowerCase());
      const base = tokenIdentity(item, pair);
      const detected = detectNarratives(item, pair);
      return {
        ...base,
        source: item.amount || item.totalAmount ? "boost" : item.claimDate ? "takeover" : "profile",
        score: scoreToken(item, pair),
        narratives: detected,
        boost: Number(item.amount || item.totalAmount || pair?.boosts?.active || 0),
        volume: Number(pair?.volume?.h24 || 0),
        liquidity: Number(pair?.liquidity?.usd || 0),
        change: Number(pair?.priceChange?.h24 || 0),
        createdAt: Number(pair?.pairCreatedAt || 0)
      };
    });

    setHealth("live");
    render();
  } catch (error) {
    console.error(error);
    setHealth("api delayed");
    renderError(error);
  } finally {
    $("#refreshBtn").disabled = false;
  }
}

function setHealth(text) {
  $("#healthText").textContent = text;
}

function selectedTokens() {
  const query = state.query.trim().toLowerCase();
  return state.enriched
    .filter((token) => state.chains.has(token.chainId))
    .filter((token) => token.score >= state.minScore)
    .filter((token) => {
      if (!query) return true;
      return [
        token.symbol,
        token.name,
        token.chainId,
        token.source,
        token.narratives.join(" ")
      ].join(" ").toLowerCase().includes(query);
    })
    .sort((a, b) => {
      if (state.sort === "volume") return b.volume - a.volume;
      if (state.sort === "liquidity") return b.liquidity - a.liquidity;
      if (state.sort === "age") return (b.createdAt || 0) - (a.createdAt || 0);
      return b.score - a.score;
    });
}

function render() {
  const tokens = selectedTokens();
  renderMetrics(tokens);
  renderTokens(tokens);
  renderNarratives(tokens);
  renderRiskTape(tokens);
}

function renderMetrics(tokens) {
  $("#metricTokens").textContent = fmt.format(tokens.length);
  const counts = narrativeCounts(tokens);
  $("#metricNarrative").textContent = counts[0]?.[0] || "--";
  const liquid = tokens.map((token) => token.liquidity).filter(Boolean).sort((a, b) => a - b);
  $("#metricLiquidity").textContent = liquid.length ? money(liquid[Math.floor(liquid.length / 2)]) : "--";
  $("#metricUpdated").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("#resultCount").textContent = `${tokens.length} found`;
}

function renderTokens(tokens) {
  const list = $("#tokenList");
  list.replaceChildren();
  if (!tokens.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No tokens match the current radar filters.";
    list.append(empty);
    return;
  }

  const template = $("#tokenTemplate");
  tokens.slice(0, 80).forEach((token) => {
    const node = template.content.cloneNode(true);
    const img = $(".token-icon", node);
    img.src = token.image || fallbackImage(token.symbol);
    img.alt = `${token.symbol} icon`;
    $(".token-name", node).textContent = `${token.symbol} / ${token.name}`;
    $(".token-name", node).href = token.url;
    $(".token-meta", node).textContent = `${token.chainId.toUpperCase()} / ${token.source} / ${shortAddress(token.address)}`;
    $(".score-pill", node).textContent = token.score;
    $('[data-field="volume"]', node).textContent = money(token.volume);
    $('[data-field="liquidity"]', node).textContent = money(token.liquidity);
    $('[data-field="change"]', node).textContent = `${pct.format(token.change)}%`;
    $('[data-field="age"]', node).textContent = ageLabel(token.createdAt);
    $(".token-description", node).textContent = cleanDescription(token.description);

    const tags = $(".tag-row", node);
    [...token.narratives, token.chainId, token.boost ? `boost ${token.boost}` : "organic"].forEach((tag) => {
      const el = document.createElement("span");
      el.textContent = tag;
      tags.append(el);
    });
    list.append(node);
  });
}

function narrativeCounts(tokens) {
  const map = new Map();
  tokens.forEach((token) => {
    token.narratives.forEach((name) => map.set(name, (map.get(name) || 0) + token.score));
  });
  return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
}

function renderNarratives(tokens) {
  const root = $("#narrativeList");
  root.replaceChildren();
  const counts = narrativeCounts(tokens);
  const max = counts[0]?.[1] || 1;
  counts.slice(0, 8).forEach(([name, value]) => {
    const row = document.createElement("div");
    row.className = "narrative-row";
    row.innerHTML = `<span>${escapeHtml(name)}</span><span class="bar"><i style="width:${Math.max(6, value / max * 100)}%"></i></span><span>${Math.round(value)}</span>`;
    root.append(row);
  });
  if (!counts.length) root.textContent = "No narrative cluster yet.";
}

function renderRiskTape(tokens) {
  const root = $("#riskTape");
  root.replaceChildren();
  const riskItems = tokens
    .filter((token) => token.score >= 55)
    .slice(0, 8)
    .map((token) => {
      const notes = [];
      if (token.liquidity < 50000) notes.push("thin liquidity");
      if (token.change > 80) notes.push("extended 24h move");
      if (!token.pair) notes.push("pair data sparse");
      if (token.boost > 0) notes.push("paid boost present");
      return { token, notes: notes.length ? notes : ["clean first pass"] };
    });

  riskItems.forEach(({ token, notes }) => {
    const row = document.createElement("div");
    row.className = "risk-item";
    row.innerHTML = `<strong>${escapeHtml(token.symbol)} / ${token.score}</strong>${escapeHtml(notes.join(", "))}`;
    root.append(row);
  });
  if (!riskItems.length) root.textContent = "Waiting for high-conviction signals.";
}

function renderError(error) {
  const list = $("#tokenList");
  list.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = `Radar API request failed: ${error.message}`;
  list.append(empty);
}

function money(value) {
  return value ? `$${fmt.format(value)}` : "--";
}

function shortAddress(value = "") {
  return value.length > 12 ? `${value.slice(0, 6)}...${value.slice(-4)}` : value;
}

function ageLabel(timestamp) {
  if (!timestamp) return "--";
  const hours = Math.max(1, Math.round((Date.now() - timestamp) / 36e5));
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function cleanDescription(value = "") {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
}

function fallbackImage(symbol) {
  const letters = encodeURIComponent((symbol || "NR").slice(0, 3).toUpperCase());
  return `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 96 96'%3E%3Crect width='96' height='96' rx='16' fill='%2318202b'/%3E%3Ctext x='48' y='57' text-anchor='middle' font-family='Arial' font-size='24' font-weight='700' fill='%2351d88a'%3E${letters}%3C/text%3E%3C/svg%3E`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bindEvents() {
  $("#refreshBtn").addEventListener("click", loadRadar);
  $("#searchInput").addEventListener("input", (event) => {
    state.query = event.target.value;
    render();
  });
  $("#minScore").addEventListener("input", (event) => {
    state.minScore = Number(event.target.value);
    $("#minScoreValue").textContent = state.minScore;
    render();
  });
  $$(".tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".tabs button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.sort = button.dataset.sort;
      render();
    });
  });
  $$(".chain-list input").forEach((input) => {
    input.addEventListener("change", () => {
      state.chains = new Set($$(".chain-list input:checked").map((item) => item.value));
      render();
    });
  });
}

bindEvents();
loadRadar();
