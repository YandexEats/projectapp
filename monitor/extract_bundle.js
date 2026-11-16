#!/usr/bin/env node
// Извлекает данные реферальной программы из минифицированного JS-бандла сайта.
// Использование: node extract_bundle.js <путь к index-*.js>
// Печатает JSON: {cities, offices, transport_types, city_list, hero_text, notes}
// Минифицированные имена переменных (ZA, sl, du ...) НЕ захардкожены:
// объекты ищутся по известным ключам городов, ссылки на переменные
// разрешаются динамически (поиск определения `ИМЯ={...}` в бандле).
"use strict";
const fs = require("fs");
const vm = require("vm");

const KNOWN_SLUGS = ["tashkent", "samarkand", "namangan", "andijan", "fergana", "bukhara"];
const KNOWN_TRANSPORTS = ["moto", "car", "ebike", "bike", "walking"];

const src = fs.readFileSync(process.argv[2], "utf8");

// --- утилиты сканирования с учётом строк -----------------------------------
function skipStringForward(s, i) {
  // s[i] — открывающая кавычка; возвращает индекс закрывающей
  const q = s[i];
  for (let j = i + 1; j < s.length; j++) {
    if (s[j] === "\\") { j++; continue; }
    if (s[j] === q) return j;
  }
  return -1;
}
function skipStringBackward(s, i) {
  // s[i] — закрывающая кавычка; возвращает индекс открывающей
  const q = s[i];
  for (let j = i - 1; j >= 0; j--) {
    if (s[j] === q) {
      let bs = 0, k = j - 1;
      while (k >= 0 && s[k] === "\\") { bs++; k--; }
      if (bs % 2 === 0) return j;
    }
  }
  return -1;
}
const QUOTES = new Set(['"', "'", "`"]);
const OPEN = { "{": "}", "[": "]", "(": ")" };
const CLOSE = { "}": "{", "]": "[", ")": "(" };

function matchForward(s, start) {
  // s[start] — открывающая скобка; ищем парную
  let depth = 0;
  for (let i = start; i < s.length; i++) {
    const c = s[i];
    if (QUOTES.has(c)) { i = skipStringForward(s, i); if (i < 0) return -1; continue; }
    if (OPEN[c]) depth++;
    else if (CLOSE[c]) { depth--; if (depth === 0) return i; }
  }
  return -1;
}
function enclosingOpenBrace(s, pos) {
  // Идём влево от pos и ищем незакрытую "{"
  let depth = 0;
  for (let i = pos - 1; i >= 0; i--) {
    const c = s[i];
    if (QUOTES.has(c)) { i = skipStringBackward(s, i); if (i < 0) return -1; continue; }
    if (CLOSE[c]) depth++;
    else if (OPEN[c]) { if (depth === 0) return c === "{" ? i : -1; depth--; }
  }
  return -1;
}

// --- вычисление литералов с разрешением ссылок -----------------------------
const defCache = new Map();
function findDefinition(name) {
  if (defCache.has(name)) return defCache.get(name);
  defCache.set(name, null); // защита от рекурсии
  const esc = name.replace(/[$]/g, "\\$");
  const re = new RegExp("(?:^|[,;{}\\s(])(?:const |let |var )?" + esc + "=([{\\[])", "g");
  let m, val = null;
  while ((m = re.exec(src))) {
    const open = m.index + m[0].length - 1;
    const close = matchForward(src, open);
    if (close < 0) continue;
    try { val = evalLiteral(src.slice(open, close + 1)); break; } catch (e) { /* пробуем следующее */ }
  }
  defCache.set(name, val);
  return val;
}
function evalLiteral(code) {
  const scope = new Proxy({}, {
    has: (_t, k) => typeof k === "string" && !["Math", "Number", "String", "Infinity", "NaN"].includes(k),
    get: (_t, k) => {
      if (k === Symbol.unscopables || typeof k !== "string") return undefined;
      if (k === "undefined") return undefined;
      return findDefinition(k); // null, если это не объект/массив (иконки, компоненты ...)
    },
  });
  const ctx = vm.createContext({ __scope: scope }, { codeGeneration: { strings: false, wasm: false } });
  return vm.runInContext("with(__scope){(" + code + ")}", ctx, { timeout: 2000 });
}

// --- поиск объектов по ключу ------------------------------------------------
function objectsContainingKey(key) {
  const out = new Map();
  const re = new RegExp("[{,]" + key + ":\\{", "g");
  let m;
  while ((m = re.exec(src))) {
    const open = enclosingOpenBrace(src, m.index + 1);
    if (open < 0 || out.has(open)) continue;
    const close = matchForward(src, open);
    if (close < 0) continue;
    try { out.set(open, evalLiteral(src.slice(open, close + 1))); } catch (e) { /* не литерал */ }
  }
  return [...out.values()];
}
const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);

let citiesObj = null, officesObj = null, transportTypes = null;
const candidates = [];
for (const slug of KNOWN_SLUGS) candidates.push(...objectsContainingKey(slug));
for (const obj of candidates) {
  const vals = Object.values(obj).filter(isObj);
  if (!vals.length) continue;
  if (!citiesObj && vals.some((v) => isObj(v.bonuses))) citiesObj = obj;
  else if (!officesObj && vals.every((v) => "address" in v && ("hours" in v || "days" in v) && !("bonuses" in v))) officesObj = obj;
}
for (const t of KNOWN_TRANSPORTS) {
  for (const obj of objectsContainingKey(t)) {
    const vals = Object.values(obj).filter(isObj);
    if (vals.length && vals.every((v) => typeof v.title === "string")) { transportTypes = obj; break; }
  }
  if (transportTypes) break;
}

// --- нормализация -----------------------------------------------------------
const num = (x) => (typeof x === "number" && isFinite(x) ? x : (typeof x === "string" && x.trim() !== "" && isFinite(+x) ? +x : null));
const cities = {};
if (citiesObj) {
  for (const [slug, c] of Object.entries(citiesObj)) {
    if (!isObj(c)) continue;
    const transports = {};
    const b = isObj(c.bonuses) ? c.bonuses : {};
    for (const [t, tiers] of Object.entries(b)) {
      transports[t] = (Array.isArray(tiers) ? tiers : []).map((x) => ({
        days: num(x && x.days), orders: num(x && x.orders), bonus: num(x && x.bonus),
      }));
    }
    cities[slug] = { name_ru: c.name || null, name_uz: c.nameUz || null, transports };
  }
}
const offices = {};
if (officesObj) {
  for (const [slug, o] of Object.entries(officesObj)) {
    if (!isObj(o)) continue;
    offices[slug] = { address: o.address || null, address_uz: o.addressUz || null, hours: o.hours || null, days: o.days || null, map: o.map || null };
  }
}
const transport_types = {};
if (transportTypes) {
  for (const [k, v] of Object.entries(transportTypes)) if (isObj(v)) transport_types[k] = { ru: v.title, uz: v.titleUz || null };
}

// Список городов (массив slug'ов)
let city_list = null;
const arrRe = /\[("[a-z]+",)+"[a-z]+"\]/g;
let am;
while ((am = arrRe.exec(src))) {
  if (am[0].includes('"tashkent"')) { city_list = JSON.parse(am[0]); break; }
}

// Текст героя
const heroRu = (src.match(/"(до [\d\s ]+сум)"/) || [])[1] || null;
let heroUz = null;
const iUz = src.indexOf("so'mgacha bonus");
if (iUz >= 0) {
  const before = src.slice(Math.max(0, iUz - 600), iUz);
  const nums = [...before.matchAll(/children:"([\d\s ]{3,})"/g)];
  if (nums.length) heroUz = nums[nums.length - 1][1].trim() + " so'mgacha";
}

// Примечание по Ташкенту (ru + uz)
function stringLiteralsMatching(re) {
  const out = [];
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c !== '"') continue;
    const j = skipStringForward(src, i);
    if (j < 0) break;
    const lit = src.slice(i + 1, j);
    if (re.test(lit)) out.push(lit);
    i = j;
  }
  return out;
}
const noteUz = stringLiteralsMatching(/^Toshkentda avval ro'yxatdan/)[0] || null;
const noteRu = stringLiteralsMatching(/в Ташкенте, с момента последнего заказа/)[0] || null;

process.stdout.write(JSON.stringify({
  cities, offices, transport_types, city_list,
  hero_text: { ru: heroRu, uz: heroUz },
  notes: { tashkent_ru: noteRu, tashkent_uz: noteUz },
}, null, 1));
