import test from "node:test";
import assert from "node:assert/strict";
import { resolveApiBaseUrl, buildApiUrl } from "../api/config.js";

test("resolveApiBaseUrl defaults to http://localhost:8000 when unset", () => {
  assert.equal(resolveApiBaseUrl(undefined), "http://localhost:8000");
  assert.equal(resolveApiBaseUrl(""), "http://localhost:8000");
  assert.equal(resolveApiBaseUrl("   "), "http://localhost:8000");
});

test("resolveApiBaseUrl strips trailing slashes from configured VITE_API_BASE_URL", () => {
  assert.equal(
    resolveApiBaseUrl("https://api.smartpick.example.com/"),
    "https://api.smartpick.example.com"
  );
  assert.equal(
    resolveApiBaseUrl("https://api.smartpick.example.com///"),
    "https://api.smartpick.example.com"
  );
});

test("resolveApiBaseUrl supports same-origin '/' deployment", () => {
  assert.equal(resolveApiBaseUrl("/"), "");
});

test("buildApiUrl normalizes leading slash on endpoint path", () => {
  const url = buildApiUrl("recommend");
  assert.ok(url.endsWith("/recommend"));
});
