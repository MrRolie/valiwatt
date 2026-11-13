// Valiwatt Live: shared page logic (docs/plan-live.md, step 3). Reads data.json (or demo.json with ?demo).
"use strict";
const T = {
  fr: {
    where: "DEMANDE AU QUÉBEC", recv: (m) => `reçues il y a ${m} min`, auto: "MAJ toutes les 15 min", late: "flux d'Hydro-Québec en retard",
    trial: (d) => `RODAGE · ESSAI COMPTÉ DÈS LE ${d}`, live: "ESSAI EN DIRECT", demo: "DÉMO · JOUR DE 2025 DÉJÀ VU",
    modeTag: "MODE EXPÉRIMENTAL", scope: "Ce tableau évalue la prévision, pas la capacité du réseau.",
    k1: "DEMANDE MESURÉE", k1s: (t) => `Lecture de ${t}.`, k2: "PRÉVISION POUR CETTE HEURE",
    k2s: (t) => `Émise à ${t}, d'après les données jusqu'à minuit.`, k3: "ÉCART ACTUEL",
    k3s: "Dernière heure complète : mesurée moins prévue, en % de la mesurée.", k4: "POINTE PRÉVUE AUJOURD'HUI",
    k4s: (t) => `Attendue à ${t}.`, k5: "DANS LA PLAGE DEPUIS MINUIT", k5s: (a, b) => `${a} heures sur ${b} dans la plage.`,
    target: "cible 90 %", today: "AUJOURD'HUI · MW", lAct: "mesurée", lFc: "prévision", lBand: "plage (vise 9 sur 10)",
    lBandX: "plage (exp.)", lOut: "hors plage", mode: "PRÉVISION :", mOff: "officielle", mExp: "expérimentale",
    now: "MAINTENANT", r1: "MONTRÉAL · TEMPÉRATURE PRÉVUE", r1v: (t, c) => `à ${t} : ${c} °C (NBM)`,
    r1s: "La température que la prévision a utilisée.", r2: "ERREUR · AUJOURD'HUI",
    r2v: (x) => `erreur absolue moyenne ${x}`, r2s: (b) => `En moyenne, la prévision était trop ${b}.`,
    low: "basse", high: "haute", r3: "DANS LA PLAGE · 7 DERNIERS JOURS", r3s: "Pointillé : cible 90 %. Orange : sous la cible. Gris clair : pas de prévision.",
    r4: "LA PLAGE DE PRÉVISION", r4off: "Tirée des erreurs passées : vise 9 mesures sur 10. Pas une limite du réseau.",
    r4exp: "Non testée : asymétrique selon la température prévue, centrée sur l'écart moyen de chaque plage de température.",
    strip: "ÉCART PAR HEURE (mesurée − prévue, %) · barre vers le haut : prévision trop basse",
    stripOut: "trait orange : hors de la plage", stripNext: "heures à venir : prévision en MW", details: "Détails techniques →",
    stOut: "HORS DE LA PLAGE", stWatch: "REVENUE DANS LA PLAGE", stOk: "FAIBLE ÉCART", stNone: "PAS DE PRÉVISION",
    sOut: (e) => `La dernière heure complète est hors de la plage de prévision : ${e} par rapport à la prévision.`,
    sWatch: (t) => `Revenue dans la plage. La dernière sortie de la plage date de ${t}.`,
    sOk: (e) => `Dernière heure complète : la demande ${e} la prévision de minuit.`,
    above: (x) => `dépasse de ${x}`, below: (x) => `est inférieure de ${x} à`,
    sNone: "Aucune prévision n'a été émise à temps aujourd'hui. La demande mesurée s'affiche seule.",
    hover: { f: "prévision", a: "mesurée", r: "plage", soon: "à venir" }, nodata: "aucune",
  },
  en: {
    where: "QUÉBEC DEMAND", recv: (m) => `received ${m} min ago`, auto: "updates every 15 min", late: "Hydro-Québec's feed is late",
    trial: (d) => `SHAKEDOWN · TRIAL COUNTS FROM ${d}`, live: "LIVE TRIAL", demo: "DEMO · A SEEN DAY OF 2025",
    modeTag: "EXPERIMENTAL MODE", scope: "This screen assesses the forecast, not the grid's capacity.",
    k1: "MEASURED DEMAND", k1s: (t) => `Reading at ${t}.`, k2: "FORECAST FOR THIS HOUR",
    k2s: (t) => `Issued at ${t} from data through midnight.`, k3: "CURRENT DEVIATION",
    k3s: "Last complete hour: measured minus forecast, as % of measured.", k4: "FORECAST PEAK TODAY",
    k4s: (t) => `Expected at ${t}.`, k5: "IN RANGE SINCE MIDNIGHT", k5s: (a, b) => `${a} of ${b} hours in range.`,
    target: "target 90%", today: "TODAY · MW", lAct: "measured", lFc: "forecast", lBand: "range (aims for 9 in 10)",
    lBandX: "range (exp.)", lOut: "out of range", mode: "FORECAST:", mOff: "official", mExp: "experimental",
    now: "NOW", r1: "MONTRÉAL · FORECAST TEMPERATURE", r1v: (t, c) => `at ${t}: ${c} °C (NBM)`,
    r1s: "The temperature the forecast used.", r2: "ERROR · TODAY", r2v: (x) => `mean absolute error ${x}`,
    r2s: (b) => `On average, the forecast was too ${b}.`, low: "low", high: "high", r3: "IN RANGE · LAST 7 DAYS",
    r3s: "Dashed: 90% target. Orange: below target. Light grey: no forecast.", r4: "THE FORECAST RANGE",
    r4off: "Drawn from past errors to hold 9 measurements in 10. Not a grid limit.",
    r4exp: "Untested: asymmetric by forecast temperature, centred on each temperature band's typical error.",
    strip: "DEVIATION BY HOUR (measured − forecast, %) · bar up: forecast too low", stripOut: "orange line: out of range",
    stripNext: "hours to come: forecast in MW", details: "Technical details →", stOut: "OUT OF RANGE",
    stWatch: "BACK IN RANGE", stOk: "SMALL DEVIATION", stNone: "NO FORECAST",
    sOut: (e) => `The last complete hour is outside the forecast range: ${e} against the forecast.`,
    sWatch: (t) => `Back in range. The last exit from the range was at ${t}.`,
    sOk: (e) => `Last complete hour: demand is ${e} the midnight forecast.`, above: (x) => `${x} above`,
    below: (x) => `${x} below`, sNone: "No forecast was issued in time today. Measured demand is shown alone.",
    hover: { f: "forecast", a: "measured", r: "range", soon: "to come" }, nodata: "none",
  },
};
const store = {
  get(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
};
const state = { lang: store.get("vw.lang", "fr"), mode: store.get("vw.mode", "official"), data: null, hover: null };
const DEMO = new URLSearchParams(location.search).has("demo");
const $ = (id) => document.getElementById(id);
const t = () => T[state.lang];
const loc = () => (state.lang === "en" ? "en-CA" : "fr-CA");
const fmt = (v) => (v == null || !isFinite(v) ? "—" : Math.round(v).toLocaleString(loc()));
const num1 = (v) => { const s = Math.abs(v).toFixed(1); return state.lang === "en" ? s : s.replace(".", ","); };
const pct = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + num1(v) + (state.lang === "en" ? "%" : " %");
const pctU = (v) => num1(v) + (state.lang === "en" ? "%" : " %");
const ESTms = -5 * 3600e3; // local standard time, as the forecast's days
const lst = (iso) => new Date(new Date(iso).getTime() + ESTms);
const hm = (iso) => { const d = lst(iso); const h = d.getUTCHours(), m = d.getUTCMinutes(); const mm = (m < 10 ? "0" : "") + m; return state.lang === "en" ? `${h}:${mm}` : `${h} h ${mm}`; };
const hourOf = (iso) => { const d = lst(iso); return d.getUTCHours() + d.getUTCMinutes() / 60; }; // 0..24 within the day
const dateLabel = (day) => { const d = new Date(day + "T12:00:00Z"); return d.toLocaleDateString(loc(), { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }); };

async function load() {
  const r = await fetch((DEMO ? "demo.json" : "data.json") + "?t=" + Date.now(), { cache: "no-store" });
  state.data = await r.json();
}
function setLang(l) { state.lang = l; store.set("vw.lang", l); render(); }
function setMode(m) { state.mode = m; store.set("vw.mode", m); render(); }
function hoursOf(issue) {
  if (!issue || issue.missed) return [];
  return issue.hours.map((h) => ({ ...h, x: hourOf(h.hour_ending_utc) === 0 ? 24 : hourOf(h.hour_ending_utc) }));
}
function hourlyActual(d) { // hour-ending x (1..24) -> the publisher's hourly mean (live.daily.hourly: fills blanked)
  const out = {}; for (const [ts, v] of d.hourly || []) { let x = Math.round(hourOf(ts)); if (x === 0) x = 24; out[x] = v; } return out;
}
function dayScore(d) { const s = d.score && d.score[state.mode]; return s && s.n ? Math.round((100 * s.in_range) / s.n) : null; }
function setText(id, v) { const el = $(id); if (el) el.textContent = v; }
function btn(el, on, accent) { el.style.background = on ? (accent || "#e8eef4") : "transparent"; el.style.color = on ? "#0a0e13" : "#9aa8b6"; el.setAttribute("aria-pressed", String(on)); }
function header() {
  const L = t(), d = state.data;
  btn($("fr"), state.lang === "fr"); btn($("en"), state.lang === "en");
  setText("where", L.where);
  setText("when", dateLabel(d.day) + (state.lang === "en" ? " · EST" : " · HNE"));
  const mins = d.latest_reading && !d.demo ? Math.max(0, Math.round((Date.now() - new Date(d.latest_reading)) / 60000)) : null;
  setText("fresh", (mins == null ? "" : L.recv(mins) + " · ") + (mins != null && mins > 45 ? L.late : L.auto));
  $("fresh").style.color = mins != null && mins > 45 ? "#ff9a70" : "";
  $("modeTag").style.display = state.mode === "experimental" ? "" : "none"; setText("modeTag", L.modeTag);
  const vf = new Date(d.verdict_from + "T05:00:00Z");
  setText("badge", DEMO ? L.demo : (new Date(d.day + "T12:00:00Z") >= vf ? L.live : L.trial(dateLabel(d.verdict_from))));
}
window.VW = { T, state, store, $, t, fmt, num1, pct, pctU, hm, hourOf, lst, dateLabel, load, setLang, setMode, hoursOf, hourlyActual, dayScore, setText, btn, header };
