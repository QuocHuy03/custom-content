/*
 * solver.js — self-contained Node port of t_n() from chatgpt.com bundle.
 *
 * Input  (stdin, line-delimited JSON): {"action": "turnstile", "proof": "...", "dx": "..."}
 * Output (stdout, line-delimited JSON): {"token": "..."} or {"error": "..."}
 *
 * Functions r_n / e_n / t_n / n_n / $gn / C1 are pasted verbatim from
 * chatgpt.com /cdn/assets/4813494d-XXX.js (search anchor: "function r_n()").
 * Only the surrounding glue (slot constants, browser-global mocks, scope wiring)
 * is added by us — keep this script easy to refresh when bundle rotates.
 */

'use strict';

// ── Slot constants (mined from same bundle, near r_n definition) ─────────
const i_n = 0,  a_n = 1,  o_n = 2,  s_n = 3,  c_n = 4,  l_n = 5,
      u_n = 6,  f_n = 7,  p_n = 8,  w1  = 9,  m_n = 10, h_n = 11,
      g_n = 12, __n = 13, v_n = 14, y_n = 15, T1  = 16, b_n = 17,
      x_n = 18, S_n = 19, w_n = 20, T_n = 21, E_n = 22, C_n = 23,
      d_n = 24, D_n = 25, O_n = 26, k_n = 27, A_n = 28, j_n = 29,
      M_n = 30, /* 31,32 reserved */     N_n = 33, P_n = 34, F_n = 35;

// ── Browser-global mocks (refilled per request via setupEnv) ─────────────
let fakeDocument, fakeWindow;

function setupEnv(opts = {}) {
  const bootstrap = opts.bootstrap || {};
  const userAgent = opts.userAgent || `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36`;

  fakeDocument = {
    scripts: [],
    cookie: ``,
    title: ``,
    referrer: ``,
    documentElement: { lang: `en` },
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => {
      const lower = (`` + tag).toLowerCase();
      if (lower === `canvas`) {
        return {
          tagName: `CANVAS`, width: 300, height: 150, style: {},
          getContext: () => null,  // Return null cleanly so VM's null-check branches activate
          toDataURL: () => `data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=`,
          getBoundingClientRect: () => ({ x: 0, y: 0, width: 300, height: 150, top: 0, left: 0, right: 300, bottom: 150 }),
          setAttribute: () => {}, getAttribute: () => null,
          appendChild: () => {}, removeChild: () => {},
          addEventListener: () => {}, removeEventListener: () => {},
        };
      }
      return mockElement(lower);
    },
  };

  // Now that mockElement is defined, set body/head to full mocks
  function mockElement(tag) {
    const el = {
      tagName: (`` + tag).toUpperCase(),
      style: new Proxy({}, { get: (t, k) => t[k] ?? ``, set: (t, k, v) => { t[k] = v; return true; } }),
      innerHTML: ``, innerText: ``, textContent: ``,
      offsetWidth: 100, offsetHeight: 20, clientWidth: 100, clientHeight: 20,
      scrollWidth: 100, scrollHeight: 20,
      children: [], childNodes: [], firstChild: null, lastChild: null,
      parentNode: null,
      setAttribute: () => {}, getAttribute: () => null, removeAttribute: () => {},
      appendChild: () => {}, removeChild: () => {}, insertBefore: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      cloneNode: () => mockElement(tag),
      getBoundingClientRect: () => ({ x: 0, y: 0, width: 100, height: 20, top: 0, left: 0, right: 100, bottom: 20 }),
      getClientRects: () => [{ x: 0, y: 0, width: 100, height: 20, top: 0, left: 0, right: 100, bottom: 20 }],
      focus: () => {}, blur: () => {}, click: () => {},
    };
    return el;
  }
  fakeDocument._mockElement = mockElement;
  fakeDocument.body = mockElement(`body`);
  fakeDocument.head = mockElement(`head`);
  fakeDocument.documentElement = mockElement(`html`);
  fakeDocument.documentElement.lang = `en`;

  fakeWindow = {
    document: fakeDocument,
    Reflect, Object, Array, String, Number, Math, JSON, Date,
    Promise, Symbol, BigInt, parseInt, parseFloat, isNaN, isFinite,
    encodeURIComponent, decodeURIComponent,
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: {
      userAgent,
      hardwareConcurrency: 12,
      language: `en-US`,
      languages: [`en-US`, `en`, `vi`],
      platform: `Win32`,
      onLine: true,
      doNotTrack: null,
      cookieEnabled: true,
      maxTouchPoints: 0,
      vendor: `Google Inc.`,
      webdriver: false,
    },
    performance: { now: () => Date.now() % 1e6, timing: {}, timeOrigin: Date.now() },
    crypto: {
      getRandomValues: a => { for (let i = 0; i < a.length; i++) a[i] = Math.floor(Math.random() * 256); return a; },
      randomUUID: () => `${Date.now()}-${Math.random()}`,
    },
    location: { href: `https://chatgpt.com/`, origin: `https://chatgpt.com`, pathname: `/`, hostname: `chatgpt.com`, protocol: `https:` },
    screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24 },
    history: { length: 1, state: null, pushState: () => {}, replaceState: () => {} },
    localStorage: {
      _data: {},
      getItem(k) { return this._data[k] ?? null; },
      setItem(k, v) { this._data[k] = `` + v; },
      removeItem(k) { delete this._data[k]; },
      clear() { this._data = {}; },
    },
    sessionStorage: {
      _data: {},
      getItem(k) { return this._data[k] ?? null; },
      setItem(k, v) { this._data[k] = `` + v; },
      removeItem(k) { delete this._data[k]; },
      clear() { this._data = {}; },
    },
    innerWidth: 1106, innerHeight: 945, outerWidth: 1920, outerHeight: 1080,
    devicePixelRatio: 1,
    __reactRouterContext: {
      basename: `/`,
      future: {},
      isSpaMode: false,
      ssr: true,
      state: {
        loaderData: {
          root: { clientBootstrap: bootstrap },
        },
        actionData: null,
        errors: null,
      },
    },
  };
  fakeWindow.self = fakeWindow;
  fakeWindow.window = fakeWindow;
  fakeWindow.globalThis = fakeWindow;
  fakeWindow.top = fakeWindow;
  fakeWindow.parent = fakeWindow;
  global.window = fakeWindow;
  global.document = fakeDocument;
  // global.navigator is a built-in getter in Node 22+, skip assignment
}

global.btoa = s => Buffer.from(s, `binary`).toString(`base64`);
global.atob = s => Buffer.from(s, `base64`).toString(`binary`);
setupEnv();  // defaults until first request

// ── VM state (module-level — mirrors bundle's module closure) ────────────
let E1 = new Map();
let D1 = 0;
let Zgn = Promise.resolve();

// ── Pasted verbatim from bundle ──────────────────────────────────────────
function n_n(e, t) {
  let n = ``;
  for (let r = 0; r < e.length; r++)
    n += String.fromCharCode(e.charCodeAt(r) ^ t.charCodeAt(r % t.length));
  return n;
}

function $gn(e) {
  let t = Zgn.then(e, e);
  Zgn = t.then(() => void 0, () => void 0);
  return t;
}

const TRACE = process.env.SOLVER_TRACE === `1`;
async function C1() {
  for (; E1.get(w1).length > 0; ) {
    let op = E1.get(w1).shift();
    if (TRACE) process.stderr.write(`[${D1}] ` + JSON.stringify(op).slice(0, 200) + `\n`);
    let [e, ...t] = op;
    let handler = E1.get(e);
    if (typeof handler !== `function`) {
      if (TRACE) process.stderr.write(`  !! no handler for slot ${e}\n`);
      D1++;
      continue;
    }
    try {
      let n = handler(...t);
      if (n && typeof n.then === `function`) await n;
    } catch (err) {
      if (TRACE) process.stderr.write(`  !! handler threw: ${err?.message}\n`);
      throw err;
    }
    D1++;
  }
}

function r_n() {
  E1.clear();
  E1.set(i_n, e_n);
  E1.set(a_n, (e, t) => E1.set(e, n_n(`` + E1.get(e), `` + E1.get(t))));
  E1.set(o_n, (e, t) => E1.set(e, t));
  E1.set(l_n, (e, t) => {
    let n = E1.get(e);
    Array.isArray(n) ? n.push(E1.get(t)) : E1.set(e, n + E1.get(t));
  });
  E1.set(k_n, (e, t) => {
    let n = E1.get(e);
    Array.isArray(n) ? n.splice(n.indexOf(E1.get(t)), 1) : E1.set(e, n - E1.get(t));
  });
  E1.set(j_n, (e, t, n) => E1.set(e, E1.get(t) < E1.get(n)));
  E1.set(N_n, (e, t, n) => { let r = Number(E1.get(t)), i = Number(E1.get(n)); E1.set(e, r * i); });
  E1.set(F_n, (e, t, n) => { let r = Number(E1.get(t)), i = Number(E1.get(n)); E1.set(e, i === 0 ? 0 : r / i); });
  E1.set(u_n, (e, t, n) => E1.set(e, E1.get(t)[E1.get(n)]));
  E1.set(f_n, (e, ...t) => E1.get(e)(...t.map(x => E1.get(x))));
  E1.set(b_n, (e, t, ...n) => {
    try {
      let r = E1.get(t)(...n.map(x => E1.get(x)));
      if (r && typeof r.then === `function`)
        return r.then(v => { E1.set(e, v); }).catch(v => { E1.set(e, `` + v); });
      E1.set(e, r);
    } catch (err) { E1.set(e, `` + err); }
  });
  E1.set(__n, (e, t, ...n) => { try { E1.get(t)(...n); } catch (err) { E1.set(e, `` + err); } });
  E1.set(p_n, (e, t) => E1.set(e, E1.get(t)));
  E1.set(m_n, fakeWindow);  // refilled per request by setupEnv()
  E1.set(h_n, (e, t) => E1.set(
    e,
    (Array.from(fakeDocument?.scripts || []).map(x => x?.src?.match(E1.get(t))).filter(x => x?.length)[0] ?? [])[0] ?? null,
  ));
  E1.set(g_n, e => E1.set(e, E1));
  E1.set(v_n, (e, t) => E1.set(e, JSON.parse(`` + E1.get(t))));
  E1.set(y_n, (e, t) => E1.set(e, JSON.stringify(E1.get(t))));
  E1.set(x_n, e => E1.set(e, atob(`` + E1.get(e))));
  E1.set(S_n, e => E1.set(e, btoa(`` + E1.get(e))));
  E1.set(w_n, (e, t, n, ...r) => (E1.get(e) === E1.get(t) ? E1.get(n)(...r) : null));
  E1.set(T_n, (e, t, n, r, ...i) => (Math.abs(E1.get(e) - E1.get(t)) > E1.get(n) ? E1.get(r)(...i) : null));
  E1.set(C_n, (e, t, ...n) => (E1.get(e) === void 0 ? null : E1.get(t)(...n)));
  E1.set(d_n, (e, t, n) => E1.set(e, E1.get(t)[E1.get(n)].bind(E1.get(t))));
  E1.set(P_n, (e, t) => { try { let n = E1.get(t); return Promise.resolve(n).then(v => { E1.set(e, v); }); } catch { return; } });
  E1.set(E_n, (e, t) => {
    let n = [...E1.get(w1)];
    return E1.set(w1, [...t]), C1().catch(err => { E1.set(e, `` + err); }).finally(() => { E1.set(w1, n); });
  });
  E1.set(A_n, () => {});
  E1.set(O_n, () => {});
  E1.set(D_n, () => {});
}

function e_n(e) {
  return $gn(() => new Promise((t, n) => {
    let r = false;
    setTimeout(() => { r = true; t(`` + D1); }, 500);
    E1.set(s_n, e => { r || (r = true, t(btoa(`` + e))); });
    E1.set(c_n, e => { r || (r = true, n(btoa(`` + e))); });
    E1.set(M_n, (e, t, n, i) => {
      let a = Array.isArray(i), o = a ? n : [], s = (a ? i : n) || [];
      E1.set(e, (...e) => {
        if (r) return;
        let n = [...E1.get(w1)];
        if (a) for (let t = 0; t < o.length; t++) { let n = o[t], r = e[t]; E1.set(n, r); }
        return E1.set(w1, [...s]), C1().then(() => E1.get(t)).catch(err => `` + err).finally(() => { E1.set(w1, n); });
      });
    });
    try {
      E1.set(w1, JSON.parse(n_n(atob(e), `` + E1.get(T1))));
      C1().catch(err => { t(btoa(D1 + `: ` + err)); });
    } catch (err) { t(btoa(D1 + `: ` + err)); }
  }));
}

function t_n(key, t) {
  return $gn(() => new Promise((n, rej) => {
    let i = key;
    r_n();
    D1 = 0;
    E1.set(T1, i);
    let a = false;
    setTimeout(() => { a = true; n(`` + D1); }, 5000);
    E1.set(s_n, e => { a || (a = true, n(btoa(`` + e))); });
    E1.set(c_n, e => { a || (a = true, rej(btoa(`` + e))); });
    E1.set(M_n, (e, t, n, i) => {
      let aa = Array.isArray(i), o = aa ? n : [], s = (aa ? i : n) || [];
      E1.set(e, (...e) => {
        if (a) return;
        let n = [...E1.get(w1)];
        if (aa) for (let t = 0; t < o.length; t++) { let n = o[t], r = e[t]; E1.set(n, r); }
        return E1.set(w1, [...s]), C1().then(() => E1.get(t)).catch(err => `` + err).finally(() => { E1.set(w1, n); });
      });
    });
    try {
      E1.set(w1, JSON.parse(n_n(atob(t), `` + E1.get(T1))));
      C1().catch(err => { n(btoa(D1 + `: ` + err)); });
    } catch (err) { n(btoa(D1 + `: ` + err)); }
  }));
}

// ── stdin/stdout JSON RPC loop ────────────────────────────────────────────
const readline = require(`readline`);
const rl = readline.createInterface({ input: process.stdin });

rl.on(`line`, async (line) => {
  if (!line.trim()) return;
  let req;
  try { req = JSON.parse(line); }
  catch (e) { console.log(JSON.stringify({ error: `bad json: ${e.message}` })); return; }
  try {
    if (req.action === `turnstile`) {
      setupEnv({ bootstrap: req.bootstrap || {}, userAgent: req.userAgent });
      const token = await t_n(req.proof, req.dx);
      console.log(JSON.stringify({ token }));
    } else {
      console.log(JSON.stringify({ error: `unknown action: ${req.action}` }));
    }
  } catch (e) {
    console.log(JSON.stringify({ error: `${e?.message || e}`, stack: e?.stack?.split(`\n`).slice(0, 3) }));
  }
});

rl.on(`close`, () => process.exit(0));
