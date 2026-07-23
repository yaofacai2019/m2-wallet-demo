import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${path}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
      headers: { accept: "text/html" },
      redirect: "manual",
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("redirects the hosted root to the M2 Wallet demo", async () => {
  const response = await render();
  assert.equal(response.status, 307);
  assert.equal(new URL(response.headers.get("location")).pathname, "/demo/index.html");
});

test("ships the installable English M2 Wallet PWA assets", async () => {
  const [page, layout, manifestText, serviceWorker, indexHtml, demoScript, demoStore, demoStyles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/demo/manifest.webmanifest", import.meta.url), "utf8"),
    readFile(new URL("../public/demo/service-worker.js", import.meta.url), "utf8"),
    readFile(new URL("../public/demo/index.html", import.meta.url), "utf8"),
    readFile(new URL("../public/demo/cregis.js", import.meta.url), "utf8"),
    readFile(new URL("../public/demo/demo-store.js", import.meta.url), "utf8"),
    readFile(new URL("../public/demo/wallet-detail.css", import.meta.url), "utf8"),
  ]);
  const manifest = JSON.parse(manifestText);

  assert.match(page, /redirect\("\/demo\/index\.html"\)/);
  assert.match(layout, /M2 Wallet — Stablecoin Operations Demo/);
  assert.match(layout, /manifest:\s*"\/demo\/manifest\.webmanifest"/);
  assert.equal(manifest.name, "M2 Wallet Demo");
  assert.equal(manifest.short_name, "M2 Wallet");
  assert.equal(manifest.display, "standalone");
  assert.ok(manifest.icons.some((icon) => icon.sizes === "192x192"));
  assert.ok(manifest.icons.some((icon) => icon.sizes === "512x512"));
  assert.match(serviceWorker, /m2-wallet-demo-v6/);
  assert.match(indexHtml, /<html lang="en">/);
  assert.match(indexHtml, /operations\.css/);
  assert.match(indexHtml, /navigator\.serviceWorker\.register/);
  assert.match(demoScript, /data-action="approve-withdrawal"/);
  assert.match(demoScript, /dataset\.action==='approve-withdrawal'/);
  assert.match(demoScript, /\/api\/v1\/network-reserves/);
  assert.match(demoScript, /network-reserve-card/);
  assert.match(demoScript, /fee reserve check blocked/);
  assert.match(demoStore, /projected_transactions/);
  assert.match(demoStore, /administrator role required/);
  assert.match(demoStyles, /\.approval-evidence\.blocked/);
  assert.match(demoStyles, /\.network-reserve-grid/);
  assert.doesNotMatch(demoScript, /data-approve=/);

  await Promise.all([
    access(new URL("../public/demo/icon-192.png", import.meta.url)),
    access(new URL("../public/demo/icon-512.png", import.meta.url)),
    access(new URL("../public/og.png", import.meta.url)),
  ]);
});
