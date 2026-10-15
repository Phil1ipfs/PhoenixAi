// Real-browser test of PHOENIX UI (Edge via Playwright). Requires backend running on :8000 with real Ollama.
import { chromium } from "playwright";

const URL = process.env.PHOENIX_URL ?? "http://127.0.0.1:8000";
const results = [];
const problems = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${detail}`); };

// --- leave the user's database as we found it: remember existing conversations, delete the ones this run created
const existingConvs = new Set((await (await fetch(URL + "/api/conversations")).json()).map((c) => c.id));
const cleanupConvs = async () => {
  for (const c of await (await fetch(URL + "/api/conversations")).json())
    if (!existingConvs.has(c.id)) await fetch(`${URL}/api/conversations/${c.id}`, { method: "DELETE" });
};
const browser = await chromium.launch({ channel: "msedge", headless: true });
const ctx = await browser.newContext({ permissions: ["clipboard-read", "clipboard-write"] });
const page = await ctx.newPage();
page.on("console", (m) => { if (m.type() === "error") problems.push("console: " + m.text()); });
page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
page.on("response", (r) => { if (r.status() >= 400) problems.push(`http ${r.status()} ${r.url()}`); });
page.on("requestfailed", (r) => { if (!r.failure()?.errorText.includes("ABORTED")) problems.push(`reqfailed ${r.url()} ${r.failure()?.errorText}`); });
page.on("dialog", (d) => d.accept());

const assistantText = () => page.locator(".md").last().innerText().catch(() => "");
const msgBox = page.getByPlaceholder(/Message PHOENIX/);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
  await page.goto(URL);
  await page.getByText("PHOENIX AI").first().waitFor();
  await page.getByText(/ready/).waitFor({ timeout: 20000 }).then(() => check("status shows model ready", true), () => check("status shows model ready", false));

  // 1-2. send + streaming
  await page.getByText("+ New conversation").click();
  await msgBox.fill("Write a short Python function that reverses a string, in a fenced code block, then one sentence of explanation.");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: /Stop/ }).waitFor({ timeout: 10000 });
  check("Stop button appears while generating", true);
  const lens = new Set();
  const t0 = Date.now();
  while (Date.now() - t0 < 180000) {
    const t = await assistantText(); if (t.length) lens.add(t.length);
    if (await page.getByRole("button", { name: "Send" }).isVisible().catch(() => false)) break;
    await sleep(300);
  }
  check("tokens render progressively", lens.size >= 4, `distinct text lengths observed: ${lens.size}`);
  await page.getByText(/tok\/s/).waitFor({ timeout: 10000 });
  check("metrics footer shown after reply", true, await page.getByText(/tok\/s/).last().innerText());

  // 3. markdown + highlight + copy
  const hasCode = await page.locator(".md pre code").count();
  check("markdown code block rendered", hasCode > 0);
  check("syntax highlighting applied", (await page.locator(".md pre code .hljs-keyword, .md pre code.hljs").count()) > 0);
  if (hasCode) {
    const norm = (x) => x.split(String.fromCharCode(13)).join("").trim();
    const expected = norm(await page.locator(".md pre code").first().innerText());
    await page.getByRole("button", { name: "Copy" }).first().click();
    const clip = norm(await page.evaluate(() => navigator.clipboard.readText()));
    check("Copy code copies the code", clip === expected && clip.length > 0, `copied ${clip.length} chars`);
    check("Copy button shows feedback", await page.getByText("Copied!").waitFor({ timeout: 3000 }).then(() => true, () => false));
  }

  // 7. persistence + switching + deletion
  const title1 = (await page.locator("nav > div").first().innerText()).replace("✕", "").trim();
  await page.getByText("+ New conversation").click();
  await sleep(500);
  check("new conversation clears chat", (await page.locator(".md").count()) === 0);
  await msgBox.fill("Say hi in one word.");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText(/tok\/s/).first().waitFor({ timeout: 120000 });
  await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 120000 });
  const convCount = await page.locator("nav > div").count();
  check("two conversations listed", convCount >= 2, `count=${convCount}`);
  await page.locator("nav > div", { hasText: title1.slice(0, 20) }).first().click();
  await page.locator(".md pre code").first().waitFor({ timeout: 10000 });
  check("switching conversations loads its messages", true);
  await page.reload();
  await page.locator("nav > div", { hasText: title1.slice(0, 20) }).first().click();
  await page.locator(".md pre code").first().waitFor({ timeout: 10000 }).then(() => check("conversation persists after reload", true), () => check("conversation persists after reload", false));
  const before = await page.locator("nav > div").count();
  await page.locator("nav > div").first().hover();
  await page.locator("nav > div").first().getByTitle("Delete").click();
  await sleep(800);
  check("delete removes a conversation", (await page.locator("nav > div").count()) === before - 1);

  // 5. Stop generation
  await page.getByText("+ New conversation").click();
  await msgBox.fill("Write a very long, detailed essay about the history of sorting algorithms with code for each.");
  await page.getByRole("button", { name: "Send" }).click();
  const t1 = Date.now();
  while (Date.now() - t1 < 120000 && (await assistantText()).length < 60) await sleep(300);
  const partial = (await assistantText()).length;
  await page.getByRole("button", { name: /Stop/ }).click();
  await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 15000 });
  check("Stop ends generation and restores Send", partial >= 60, `partial chars at stop: ${partial}`);
  await page.getByText(/stopped/).waitFor({ timeout: 10000 }).then(() => check("stopped reply saved and labelled", true), () => check("stopped reply saved and labelled", false));

  // 8. model switching in UI
  await page.getByText("⚙ Settings").click();
  const sel = page.getByLabel(/Model \(installed/);
  await page.waitForFunction(() => document.querySelectorAll("#s-model option").length >= 2); const opts = await sel.locator("option").allInnerTexts();
  check("settings lists installed models", opts.length >= 2, opts.join(" | "));
  await sel.selectOption({ label: opts.find((o) => o.startsWith("llama3.2:3b")) });
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("+ New conversation").click();
  await msgBox.fill("Reply with just: PONG");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText(/llama3\.2:3b · /).waitFor({ timeout: 360000 }).then(() => check("reply attributed to switched model (llama3.2:3b)", true), () => check("reply attributed to switched model", false));
  await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 60000 });
  await page.getByText("⚙ Settings").click();
  await page.getByLabel(/Model \(installed/).selectOption({ label: (await page.locator("#s-model option").allInnerTexts()).find((o) => o.startsWith("qwen2.5-coder:3b")) });
  await page.getByRole("button", { name: "Save" }).click();
  check("model restored to qwen2.5-coder:3b", true);
} catch (e) {
  check("test run completed without exception", false, String(e).split("\n")[0]);
  await page.screenshot({ path: "failure.png" }).catch(() => {});
} finally {
  console.log("\nFrontend problems observed:", problems.length ? "\n  " + [...new Set(problems)].join("\n  ") : "none");
  console.log(`\n${results.filter((r) => r.ok).length}/${results.length} checks passed`);
  await browser.close();
  await cleanupConvs();
  process.exit(results.every((r) => r.ok) ? 0 : 1);
}
