import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(new URL("../apps/web/src/app/v1/stream/route.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { GET } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const originalFetch = globalThis.fetch;
const previousOrigin = process.env.ERJI_API_ORIGIN;
try {
 process.env.ERJI_API_ORIGIN = "http://api:18771";
 for (const authenticated of [false, true]) {
  globalThis.fetch = async (url, options) => {
   assert.equal(url, "http://api:18771/v1/stream");
   assert.equal(options.headers.Cookie, authenticated ? "erji_session=test-session" : "");
   assert.equal(options.headers["Last-Event-ID"], "42");
   return new Response(authenticated ? "data: ok\n\n" : "unauthorized", {status: authenticated ? 200 : 401});
  };
  const headers = {"Last-Event-ID": "42"};
  if (authenticated) headers.Cookie = "erji_session=test-session";
  const result = await GET(new Request("http://127.0.0.1:18770/v1/stream", {headers}));
  assert.equal(result.status, authenticated ? 200 : 401);
  assert.equal(await result.text(), authenticated ? "data: ok\n\n" : "unauthorized");
 }
 console.log("SSE proxy: authenticated stream and unauthenticated rejection passed");
} finally {
 globalThis.fetch = originalFetch;
 if (previousOrigin === undefined) delete process.env.ERJI_API_ORIGIN;
 else process.env.ERJI_API_ORIGIN = previousOrigin;
}
