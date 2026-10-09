// The extension reads only the pages the user opens, and never acts on their Vinted account
// (decision Q1, Vinted terms from 8 Oct 2026). These checks read the sources, so a feature that
// brings automation back has to change this file on purpose.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

const dir = new URL("../src/", import.meta.url);
const files = readdirSync(dir).filter((f) => /\.(js|html)$/.test(f));
const source = (f) => readFileSync(new URL(f, dir), "utf8");
const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));

test("the files of the removed automation are gone", () => {
  for (const f of ["scan.js", "scan.html", "app-bridge.js"]) assert.equal(files.includes(f), false, f);
});

test("no permission for background pages, notifications or script injection", () => {
  for (const p of ["scripting", "offscreen", "notifications", "tabs", "webRequest", "cookies", "history"]) {
    assert.equal(manifest.permissions.includes(p), false, p);
  }
  assert.equal(manifest.host_permissions, undefined);
  // The only optional host permission is FlipFinder's own address, asked at pairing.
  assert.deepEqual(manifest.optional_host_permissions, ["http://*/*", "https://*/*"]);
});

test("the only network requests are to FlipFinder's own API and, if the user allowed it, the photo files of the page they opened", () => {
  const calls = files.flatMap((f) => (source(f).match(/\bfetch\(/g) || []).map(() => f));
  assert.deepEqual(calls, ["background.js", "background.js"]); // the API, and the photo files
  const bg = source("background.js");
  assert.match(bg, /fetch\(`\$\{base\}\/api\/v1\$\{path\}`/);
  assert.equal(/XMLHttpRequest|sendBeacon|WebSocket|EventSource/.test(files.map(source).join("\n")), false);

  // Decision Q3-B: photos reach the server only from this browser, never fetched by the server. The one
  // request for them is opt-in, limited to jobs derived from the page the user opened, without cookies.
  const photoFetch = bg.match(/fetch\(job\.url, \{([^}]*)\}\)/);
  assert.ok(photoFetch, "the photo request is made from a job, not from a free address");
  assert.match(photoFetch[1], /credentials: "omit"/);
  assert.match(photoFetch[1], /referrerPolicy: "no-referrer"/);
  assert.match(photoFetch[1], /redirect: "error"/);
  const sender = bg.slice(bg.indexOf("async function sendPhotos"), bg.indexOf("// ------------------------------------------------------------------ tabs"));
  assert.match(sender, /opts\.uploadPhotos/); // off unless the user switched it on
  assert.match(sender, /K\.photoJobs\(wanted, pageImages\)/); // only photos the page itself shows
  assert.match(sender, /permissions\.contains\(\{ origins: \[K\.PHOTO_ORIGIN\] \}\)/); // and the permission was granted
  // The photo host is the only extra address, asked as an optional permission, never at install time.
  const core = source("core.js");
  assert.match(core, /PHOTO_ORIGIN = "https:\/\/\*\.vinted\.net\/\*"/);
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(JSON.stringify(manifest.permissions).includes("vinted"), false);
});

test("photos are sent only when the option is on, which is off by default", () => {
  assert.match(source("core.js"), /uploadPhotos: false/);
  assert.match(source("options.js"), /permissions\.request\(\{ origins: \[K\.PHOTO_ORIGIN\] \}\)/);
});

test("nothing clicks on Vinted: no programmatic click in the content script", () => {
  assert.equal(/\.click\(\)|dispatchEvent\(\s*new (Mouse|Pointer)Event/.test(source("content.js")), false);
});

test("no message type of the removed features is handled", () => {
  const all = files.map(source).join("\n");
  for (const t of ["ff:scan", "ff:deep", "ff:read-page", "ff:vinted-", "ff:purchase-done", "ff:favourite-seen", "ff:bridge"]) {
    assert.equal(all.includes(t), false, t);
  }
});

test("alarms are only for syncing, parser configuration and the market summary", () => {
  const names = [...source("background.js").matchAll(/alarms\.create\("([\w-]+)"/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(names)].sort(), ["ff-config", "ff-flush", "ff-market"]);
});
