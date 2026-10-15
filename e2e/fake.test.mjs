// Deterministic UI regression tests against tests/fake_server.py (scripted model + mocked web). No real model/network.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const PORT = 8003, URL = `http://127.0.0.1:${PORT}`;
const PY = path.resolve("..", "backend", ".venv", "Scripts", "python.exe");
const results = [], problems = [];
const check = (n, ok, d = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"} ${n} ${d}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function startServer(env = {}) {
  const p = spawn(PY, ["tests/fake_server.py"], { cwd: path.resolve("..", "backend"), env: { ...process.env, FAKE_PORT: String(PORT), ...env }, stdio: "ignore" });
  for (let i = 0; i < 40; i++) { try { if ((await fetch(URL + "/api/health")).ok) return p; } catch { /* wait */ } await sleep(250); }
  throw new Error("fake server did not start");
}
const stop = (p) => new Promise((r) => { p.once("exit", r); p.kill(); setTimeout(r, 2000); });

const browser = await chromium.launch({ channel: "msedge", headless: true });
const shots = path.resolve("shots"); fs.mkdirSync(shots, { recursive: true });
let server = await startServer();
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ["clipboard-read", "clipboard-write"] });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") problems.push("console: " + m.text()); });
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("response", (r) => { if (r.status() >= 400) problems.push(`http ${r.status()} ${r.url().replace(URL, "")}`); });
  page.on("dialog", (d) => d.accept());
  const box = page.getByPlaceholder(/Message PHOENIX/);
  const ask = async (q) => {
    await box.fill(q);
    await page.getByRole("button", { name: "Send" }).click();
    await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 20000 });
  };
  await page.goto(URL);
  await page.getByLabel("Message").waitFor();

  // ---- web OFF: time-sensitive question must be flagged unverified (deterministic banner, not model text)
  await page.getByRole("button", { name: "New chat" }).click();
  await ask("Who is the current prime minister of Japan?");
  check("web off: unverified banner shown for time-sensitive question", await page.getByText(/internet access is off/).isVisible());
  check("web off: no sources list", (await page.getByTestId("sources").count()) === 0);
  await page.screenshot({ path: path.join(shots, "web-off.png") });

  // ---- web AUTO: sources, clickable citation, status
  await page.getByLabel("Web mode").selectOption("auto");
  await page.getByRole("button", { name: "New chat" }).click();
  await ask("Who is the current prime minister of Japan?");
  const link = page.getByTestId("sources").getByRole("link", { name: /Prime Minister of Japan/ });
  check("web auto: source link displayed with real URL", (await link.getAttribute("href")) === "https://news.test/pm");
  check("source link opens safely in new tab", (await link.getAttribute("target")) === "_blank" && /noopener/.test(await link.getAttribute("rel")));
  check("source labelled 'page read'", await page.getByTestId("sources").getByText(/page read/).isVisible());
  const cite = page.locator(".md a.cite", { hasText: "news.test" });
  check("citation [1] in the answer is an inline source chip linking to the source", (await cite.first().getAttribute("href")) === "https://news.test/pm" && !(await page.locator(".md").last().innerText()).includes("[1]"));
  check("grounded status shown", await page.getByText(/Cited from 1 live source/).isVisible());
  check("search line says a live search ran, from the request log", /Live search completed · DuckDuckGo/.test(await page.getByTestId("search-line").last().innerText()));
  await page.getByTestId("search-details").last().locator("summary").click();
  check("search details list the real query and provider", /query: Who is the current prime minister of Japan\?/.test(await page.getByTestId("search-details").last().innerText())
    && /search · DuckDuckGo · ok/.test(await page.getByTestId("search-details").last().innerText()));
  // ---- same question again: cached results are labelled, and "Search again" bypasses the cache
  await ask("Who is the current prime minister of Japan?");
  check("repeat question: cached results labelled with retrieval time", /Cached results used: retrieved at \d\d:\d\d/.test(await page.getByTestId("search-line").last().innerText()));
  await page.getByRole("button", { name: "Search again" }).click();
  await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 20000 });
  check("'Search again' runs a fresh live search", /Live search completed/.test(await page.getByTestId("search-line").last().innerText()));
  await page.screenshot({ path: path.join(shots, "web-auto.png") });

  // ---- fabricated citation is stripped and flagged
  await page.getByRole("button", { name: "New chat" }).click();
  await ask("Who is the current prime minister of Japan? fabricate-test");
  const body = await page.locator(".md").last().innerText();
  check("fabricated [9] citation removed from displayed answer", !body.includes("[9]") && /unverified citation removed/.test(body));
  check("warning shown for invalid citation", await page.getByText(/couldn.t be matched|invalid citation/).isVisible());

  // ---- weather: the reported "I mean USA" conversation (deterministic, no model, location-aware)
  await page.getByRole("button", { name: "New chat" }).click();
  await ask("What's the weather tomorrow?");
  check("weather: asks for a city when none is given", await page.getByText(/Which city or town would you like the weather tomorrow for\?/).isVisible());
  await ask("I mean USA");
  check("weather: a country never gets one temperature", await page.getByText(/far too large for one forecast/).isVisible());
  await ask("How about Los Angeles?");
  const wx = page.locator(".md").last();
  await wx.getByText(/Los Angeles, California, United States/).first().waitFor({ timeout: 10000 }).catch(() => {});
  const wxText = await wx.innerText();
  check("weather: forecast for the resolved city, for the day asked earlier", /Los Angeles, California, United States/.test(wxText) && /Forecast for Saturday 17 Oct/.test(wxText), wxText.slice(0, 120));
  check("weather: says it does not describe the whole country", /does not describe the United States as a whole/.test(wxText));
  check("weather: Open-Meteo source chip + live-data status", (await wx.locator("a.cite", { hasText: "open-meteo.com" }).count()) === 1 && await page.getByText(/Live data, fetched just now and validated/).isVisible());
  await page.screenshot({ path: path.join(shots, "weather.png") });

  // ---- offline fallback
  await page.getByRole("button", { name: "New chat" }).click();
  await ask("Who is the current prime minister of Japan? offline-test");
  check("offline: 'Live search unavailable' banner names the offline fallback", await page.getByText(/Live search unavailable.*offline fallback/).first().isVisible());
  check("offline: chat still answered", (await page.locator(".md").last().innerText()).includes("could not verify"));

  // ---- timeless question in auto mode: no web UI at all
  await page.getByRole("button", { name: "New chat" }).click();
  await ask("Explain quicksort");
  check("timeless question: no sources and no banner", (await page.getByTestId("sources").count()) === 0 && (await page.getByText(/Cited from|Live search unavailable|internet access is off/).count()) === 0);

  // ---- settings panel
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByLabel("Internet access").waitFor();
  check("settings: model select present", await page.getByLabel(/Model \(installed/).isVisible());
  check("settings: shows web mode auto", (await page.getByLabel("Internet access").inputValue()) === "auto");
  await page.getByLabel(/Web contact/).fill("tester@example.com");
  await page.getByRole("button", { name: "Test connection" }).click();
  await page.getByTestId("web-test").getByText(/Reachable/).waitFor({ timeout: 10000 }).then(() => check("settings: web connection test reports reachable", true), () => check("settings: web connection test reports reachable", false));
  await page.getByLabel("Keep model in RAM after a reply").selectOption("0");
  await page.getByLabel("CPU threads (0 = automatic)").fill("4");
  await page.screenshot({ path: path.join(shots, "settings.png") });
  await page.getByRole("button", { name: "Save" }).click();
  await page.reload();
  const s = await (await fetch(URL + "/api/settings")).json();
  check("settings persisted (keep_alive, threads, contact)", s.keep_alive === "0" && s.num_thread === 4 && s.web_contact === "tester@example.com");
  check("web mode survives reload", (await page.getByLabel("Web mode").inputValue()) === "auto");

  // ---- code view: patch with validation info (symbol strategy) and approval flow
  const proj = fs.mkdtempSync(path.join(os.tmpdir(), "phx-fake-proj-"));
  fs.writeFileSync(path.join(proj, "calc.py"), "def add(a, b):\n    return a - b\n");
  await page.getByRole("tab", { name: "Code" }).click();
  await page.getByLabel("Folder path").fill(proj);
  await page.getByRole("button", { name: "Authorize" }).click();
  await page.locator('[data-path="calc.py"]').click();
  await page.getByRole("button", { name: /^Changes/ }).click();
  await page.getByLabel("Change instruction").fill("Fix add to return the sum");
  await page.getByRole("button", { name: "Propose" }).click();
  await page.getByTestId("patch-meta").waitFor();
  const meta = await page.getByTestId("patch-meta").innerText();
  check("patch shows strategy, attempts and validation checks", /symbol/.test(meta) && /python syntax/.test(meta) && /requested symbol changed/.test(meta) && /diff roundtrip/.test(meta), meta.replace(/\n/g, " | "));
  check("patch not written before approval", fs.readFileSync(path.join(proj, "calc.py"), "utf8").includes("a - b"));
  await page.getByRole("button", { name: /Approve/ }).click();
  await page.getByText("applied", { exact: true }).first().waitFor();
  check("approve applies the validated patch", fs.readFileSync(path.join(proj, "calc.py"), "utf8").includes("a + b"));
  await page.screenshot({ path: path.join(shots, "code-patch.png") });
  fs.rmSync(proj, { recursive: true, force: true });

  // ---- responsive layout
  await page.getByRole("tab", { name: "Chat" }).click();
  await page.setViewportSize({ width: 390, height: 800 });
  await sleep(300);
  check("mobile: no horizontal overflow", await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  check("mobile: sidebar collapsed, menu button visible", (await page.getByLabel("Open menu").isVisible()) && !(await page.getByRole("button", { name: "New chat" }).isVisible()));
  await page.getByLabel("Open menu").click();
  await sleep(350); // sidebar slide-in transition
  check("mobile: menu opens", await page.getByRole("button", { name: "New chat" }).isVisible());
  await page.screenshot({ path: path.join(shots, "mobile.png") });
  await ctx.close();

  // ---- low-memory UI (separate server with simulated 500 MB free)
  await stop(server);
  server = await startServer({ FAKE_RAM: "500" });
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const p2 = await ctx2.newPage();
  await p2.goto(URL);
  await p2.getByText(/Low free memory/).waitFor({ timeout: 10000 }).then(() => check("low RAM: header banner shown", true), () => check("low RAM: header banner shown", false));
  await p2.getByPlaceholder(/Message PHOENIX/).fill("hello");
  await p2.getByRole("button", { name: "Send" }).click();
  await p2.getByText(/Low memory \(500 MB free\)/).waitFor({ timeout: 10000 }).then(() => check("low RAM: pre-flight warning shown before the reply", true), () => check("low RAM: pre-flight warning shown before the reply", false));
  await p2.getByRole("button", { name: "Send" }).waitFor({ timeout: 20000 });
  check("low RAM: reply still delivered", (await p2.locator(".md").last().innerText()).includes("fake model"));
  await ctx2.close();
} catch (e) {
  check("test run completed without exception", false, String(e).split("\n")[0]);
} finally {
  console.log("\nFrontend problems observed:", problems.length ? "\n  " + [...new Set(problems)].join("\n  ") : "none");
  console.log(`${results.filter(Boolean).length}/${results.length} checks passed`);
  await browser.close();
  await stop(server);
  process.exit(results.every(Boolean) ? 0 : 1);
}
