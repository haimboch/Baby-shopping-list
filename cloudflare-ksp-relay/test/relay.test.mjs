import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/index.js";

test("health is public", async () => {
  const response = await worker.fetch(new Request("https://relay.example/health"), {});
  assert.equal(response.status, 200);
  assert.equal((await response.json()).version, "0.49");
});

test("KSP routes require the relay token", async () => {
  const response = await worker.fetch(
    new Request("https://relay.example/ksp/category?search=7290000191225"),
    { RELAY_TOKEN: "test-secret" },
  );
  assert.equal(response.status, 401);
});

test("authorized barcode search forwards only to the fixed KSP API", async () => {
  const originalFetch = globalThis.fetch;
  let upstreamUrl = "";
  globalThis.fetch = async (url) => {
    upstreamUrl = String(url);
    return new Response('{"result":{"items":[]}}', {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    const response = await worker.fetch(
      new Request("https://relay.example/ksp/category?search=7290000191225", {
        headers: { authorization: "Bearer test-secret" },
      }),
      { RELAY_TOKEN: "test-secret" },
    );
    assert.equal(response.status, 200);
    assert.equal(
      upstreamUrl,
      "https://ksp.co.il/m_action/api/category/?search=7290000191225",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("free-form upstream URLs are rejected", async () => {
  const response = await worker.fetch(
    new Request("https://relay.example/ksp/proxy?url=https://example.com", {
      headers: { authorization: "Bearer test-secret" },
    }),
    { RELAY_TOKEN: "test-secret" },
  );
  assert.equal(response.status, 404);
});
