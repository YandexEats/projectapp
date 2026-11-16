// Вход: до ввода пароля на странице нет ничего, кроме формы.
// Данные (data/*.json.enc, testdata/*.json.enc) и сам дашборд (ui.json.enc: разметка + код)
// приходят зашифрованными и расшифровываются в браузере.
// Формат совместим с monitor/vault.py: AES-256-GCM, ключ PBKDF2-SHA256(пароль NFC, salt, iter), AAD = "p/v2/<имя>".
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const TEST = params.get("test") === "1";
  const DATA = TEST ? "testdata/" : "data/";
  const KEY_STORE = "panel-key-v2";          // в localStorage — только производный ключ, не пароль
  const MIN_ITER = 300000;
  const AAD_PREFIX = "p/v2/";
  const $ = (id) => document.getElementById(id);
  const enc = new TextEncoder(), dec = new TextDecoder();
  const b64d = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64e = (u8) => { let s = ""; u8.forEach((b) => (s += String.fromCharCode(b))); return btoa(s); };

  function checkEnvelope(e) {
    if (!e || e.v !== 2 || e.alg !== "AES-256-GCM" || e.kdf !== "PBKDF2-SHA256" || !(e.iter >= MIN_ITER)) throw new Error("bad-envelope");
  }
  async function deriveKey(password, env, extractable) {
    const base = await crypto.subtle.importKey("raw", enc.encode(password.normalize("NFC")), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: b64d(env.salt), iterations: env.iter },
      base, { name: "AES-GCM", length: 256 }, extractable, ["decrypt"]);
  }
  async function decryptEnv(key, env, name) {
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64d(env.iv), additionalData: enc.encode(AAD_PREFIX + name), tagLength: 128 }, key, b64d(env.ct));
    return JSON.parse(dec.decode(pt));
  }

  function storage() { try { return window.localStorage; } catch (e) { return null; } }
  function storedKey() { try { return JSON.parse(storage().getItem(KEY_STORE) || "null"); } catch (e) { return null; } }
  function forgetKey() {
    const s = storage();
    if (!s) return;
    try {
      s.removeItem(KEY_STORE);
      Object.keys(s).filter((k) => /-key-v\d+$/.test(k)).forEach((k) => s.removeItem(k)); // ключи старых версий
    } catch (e) { /* нет доступа к хранилищу */ }
  }
  async function rememberKey(key, env) {
    const raw = new Uint8Array(await crypto.subtle.exportKey("raw", key));
    try { storage().setItem(KEY_STORE, JSON.stringify({ v: 2, salt: env.salt, iter: env.iter, k: b64e(raw) })); } catch (e) { /* приватный режим */ }
  }

  async function fetchEnv(path) {
    const r = await fetch(path + ".enc?t=" + Date.now(), { cache: "no-store" });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error("HTTP " + r.status);
    const e = await r.json();
    checkEnvelope(e);
    return e;
  }

  const FILES = [["last_check.json", {}], ["history.json", []], ["changes.json", []]];

  // Расшифровать интерфейс и данные. Неверный ключ → исключение (AES-GCM проверяет целостность).
  async function decryptAll(key, envs) {
    const out = { ui: await decryptEnv(key, envs.ui, "ui.json"), data: {} };
    for (const [name, fallback] of FILES) out.data[name] = envs[name] ? await decryptEnv(key, envs[name], name) : fallback;
    return out;
  }

  function gateStatus(text) { const s = $("gate-status"); s.textContent = text || ""; s.hidden = !text; }

  function showApp(all) {
    $("gate").hidden = true;
    $("app").innerHTML = all.ui.html;
    const start = new Function(all.ui.js + "\nreturn startDashboard;")();
    start({ data: all.data, params, test: TEST, onLogout: () => { forgetKey(); location.reload(); } });
  }

  async function main() {
    if (!window.crypto || !crypto.subtle) { gateStatus("Браузер не поддерживает расшифровку (нужен HTTPS и современный браузер)."); return; }
    let envs;
    try {
      const list = await Promise.all([fetchEnv("ui.json")].concat(FILES.map(([n]) => fetchEnv(DATA + n))));
      envs = { ui: list[0] };
      FILES.forEach(([n], i) => (envs[n] = list[i + 1]));
    } catch (e) { gateStatus("Не удалось загрузить страницу. Обновите её."); return; }
    if (!envs.ui) { gateStatus("Страница ещё не готова."); return; }
    const ref = envs.ui;

    // 1) ключ, запомненный на этом устройстве
    const st = storedKey();
    if (st && st.salt === ref.salt && st.iter === ref.iter) {
      try {
        const key = await crypto.subtle.importKey("raw", b64d(st.k), { name: "AES-GCM" }, false, ["decrypt"]);
        return showApp(await decryptAll(key, envs));
      } catch (e) { forgetKey(); }
    } else forgetKey(); // нет ключа, пароль сменили или ключ старой версии

    // 2) форма пароля
    gateStatus("");
    const form = $("login-form"), btn = $("login-btn"), err = $("login-error"), pw = $("pw");
    form.hidden = false;
    pw.focus();
    pw.addEventListener("input", () => (err.hidden = true));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (btn.disabled) return;
      btn.disabled = true; btn.textContent = "Проверяю…"; err.hidden = true;
      const remember = $("remember").checked;
      let all;
      try {
        const key = await deriveKey(pw.value, ref, remember);
        all = await decryptAll(key, envs);
        if (remember) await rememberKey(key, ref);
      } catch (e) {
        err.textContent = "Неверный пароль"; err.hidden = false;
        pw.select();
        btn.disabled = false; btn.textContent = "Войти";
        return;
      }
      pw.value = "";
      showApp(all);
    });
  }
  main();
})();
