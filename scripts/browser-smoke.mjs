// Real gateway + reused BFF + Chromium. Disposable accounts/databases; no cloud calls.
import { chromium } from "../dashboard/node_modules/playwright-core/index.mjs";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const temp = await mkdtemp(resolve(tmpdir(), "agentport-browser-"));
const children = [];
let browser;
async function port() {
  const server = createServer();
  await new Promise(r => server.listen(0, "127.0.0.1", r));
  const value = server.address().port;
  await new Promise(r => server.close(r));
  return value;
}
async function ready(url) {
  for (let i = 0; i < 160; i++) {
    try { if ((await fetch(url)).ok) return; } catch { /* process still starting */ }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`Not ready: ${url}`);
}
function start(command, args, env, cwd = temp) {
  const child = spawn(command, args, { env: { ...process.env, ...env }, cwd, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stdout.on("data", b => { log = (log + b).slice(-10000); });
  child.stderr.on("data", b => { log = (log + b).slice(-10000); });
  child.diagnostics = () => log;
  children.push(child);
  return child;
}

try {
  const [gp, sp, bp] = await Promise.all([port(), port(), port()]);
  const gateway = `http://127.0.0.1:${gp}`, base = `http://127.0.0.1:${bp}`;
  const password = "Aa1!" + randomBytes(24).toString("base64url");
  const env = { PYTHONPATH: `${root}/src:${root}`, JWT_SECRET_KEY: randomBytes(48).toString("base64url"),
    AUTH_ENCRYPTION_SECRET: randomBytes(48).toString("base64url"), AOG_DELEGATION_SECRET: randomBytes(48).toString("base64url"),
    PLATFORM_ADMIN_EMAIL: "admin@example.com", PLATFORM_ADMIN_PASSWORD: password, DEFAULT_USER_PASSWORD: password,
    BASIC_AUTH_PASSWORD: password, DATABASE_URL: `sqlite:///${temp}/core.db`, AOG_DATABASE_URL: `sqlite:///${temp}/aog.db`,
    DBOS_SYSTEM_DATABASE_URL: `sqlite:///${temp}/dbos.db`, AUTH_REQUIRED: "true", EMAIL_AUTH_ENABLED: "true",
    MCPGATEWAY_UI_ENABLED: "true", MCPGATEWAY_ADMIN_API_ENABLED: "true", MCP_CLIENT_AUTH_ENABLED: "true",
    PASSWORD_CHANGE_ENFORCEMENT_ENABLED: "false", LOG_LEVEL: "ERROR" };
  const began = performance.now();
  const gatewayProcess = start(`${root}/.venv/bin/python`, ["-m", "uvicorn", "aog.app:app", "--port", String(gp), "--log-level", "warning"], env);
  start(`${root}/.venv/bin/python`, ["-m", "uvicorn", "examples.converter:app", "--port", String(sp), "--log-level", "warning"], env);
  start(process.execPath, ["dist/index.js"], { NODE_ENV: "development", COOKIE_SECURE: "false", REDIS_URL: "memory://",
    HOST: "127.0.0.1", PORT: String(bp), PUBLIC_ORIGIN: base, CONTEXTFORGE_URL: gateway, LOG_LEVEL: "error" }, `${root}/dashboard/server`);
  await ready(gateway + "/health");
  const startupSeconds = (performance.now() - began) / 1000;
  await ready(`http://127.0.0.1:${sp}/health`); await ready(base + "/healthz");
  const login = await fetch(gateway + "/auth/email/login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "admin@example.com", password }) });
  assert.equal(login.status, 200);
  const token = (await login.json()).access_token;
  const service = JSON.parse(await readFile(`${root}/examples/converter.service.json`, "utf8"));
  service.endpoint = `http://127.0.0.1:${sp}/convert`; service.health_url = `http://127.0.0.1:${sp}/health`;
  const registered = await fetch(gateway + "/orchestration/v1/services", { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify(service) });
  assert.equal(registered.status, 201);
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto(base + "/app/login");
  await page.locator('input[type="email"]').fill("admin@example.com");
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: /sign in/i, exact: true }).click();
  await page.waitForURL(url => !url.pathname.includes("login"));
  await page.goto(base + "/app/orchestration");
  await page.getByRole("button", { name: "Use service" }).waitFor();
  const shots = resolve(root, "docs/screenshots"); await mkdir(shots, { recursive: true });
  await page.screenshot({ path: resolve(shots, "desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Use service" }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "example.txt", mimeType: "text/plain", buffer: Buffer.from("hello\nagentport") });
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await page.getByRole("button", { name: "Download result", exact: true }).waitFor({ timeout: 30000 });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Download .*\.csv/ }).click();
  const file = await download;
  assert.match(await readFile(await file.path(), "utf8"), /AGENTPORT/);
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const second = await mobile.newPage();
  await second.goto(base + "/app/login");
  await second.locator('input[type="email"]').fill("admin@example.com");
  await second.locator('input[type="password"]').fill(password);
  await second.getByRole("button", { name: /sign in/i, exact: true }).click();
  await second.waitForURL(url => !url.pathname.includes("login"));
  await second.goto(base + "/app/orchestration");
  await second.getByRole("button", { name: "Activity", exact: true }).click();
  await second.getByRole("button", { name: /completed/ }).click();
  await second.getByRole("button", { name: "Download result", exact: true }).waitFor();
  assert.equal(await second.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await second.screenshot({ path: resolve(shots, "mobile.png"), fullPage: true });
  assert.deepEqual(errors, []);
  const status = await readFile(`/proc/${gatewayProcess.pid}/status`, "utf8");
  const metrics = { environment: "Linux amd64, Python 3.14, Node 24, SQLite dev", startup_seconds: Number(startupSeconds.toFixed(2)),
    gateway_peak_rss_kib: Number(status.match(/VmHWM:\s+(\d+)/)?.[1]), note: "Single cold-start sample; excludes BFF, browser, DB services and GPU models. Not a capacity benchmark." };
  await writeFile(resolve(shots, "smoke-metrics.json"), JSON.stringify(metrics, null, 2) + "\n");
  console.log("PASS: browser login, text-file upload, async conversion, CSV download, independent mobile login/result, no horizontal overflow", metrics);
} catch (error) {
  console.error(error.message);
  for (const child of children) console.error(child.diagnostics());
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  for (const child of children) child.kill("SIGTERM");
  await Promise.all(children.map(child => new Promise(r => {
    if (child.exitCode !== null) return r();
    const timer = setTimeout(() => child.kill("SIGKILL"), 8000);
    child.once("exit", () => { clearTimeout(timer); r(); });
  })));
  await rm(temp, { recursive: true, force: true });
}
