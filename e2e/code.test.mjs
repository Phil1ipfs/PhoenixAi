// Real-browser test of PHOENIX Code + Benchmark tab (Edge via Playwright). Needs backend on :8000 and real Ollama.
import { chromium } from "playwright";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const URL = process.env.PHOENIX_URL ?? "http://127.0.0.1:8000";
const results = [], problems = [];
const check = (name, ok, detail = "") => { results.push({ name, ok }); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${detail}`); };
const info = (m) => console.log("INFO", m);

const root = fs.mkdtempSync(path.join(os.tmpdir(), "phx-proj-"));
fs.mkdirSync(path.join(root, "src"));
fs.mkdirSync(path.join(root, "node_modules"));
const CALC = "def add(a, b):\n    return a - b\n\n\ndef greet(name):\n    return 'Hello, ' + name\n";
fs.writeFileSync(path.join(root, "src", "calc.py"), CALC);
fs.writeFileSync(path.join(root, "README.md"), "# Demo\nA tiny calculator.\n");
fs.writeFileSync(path.join(root, ".env"), "API_KEY=supersecret123\n");
fs.writeFileSync(path.join(root, "node_modules", "junk.js"), "junk");
const disk = () => fs.readFileSync(path.join(root, "src", "calc.py"), "utf8");

const dropTestProjects = async () => {
  for (const p of await (await fetch(URL + "/api/projects")).json())
    if (p.path.includes("phx-proj-")) await fetch(`${URL}/api/projects/${p.id}`, { method: "DELETE" });
};
await dropTestProjects();
// --- leave the user's database as we found it: remember existing conversations, delete the ones this run created
const existingConvs = new Set((await (await fetch(URL + "/api/conversations")).json()).map((c) => c.id));
const cleanupConvs = async () => {
  for (const c of await (await fetch(URL + "/api/conversations")).json())
    if (!existingConvs.has(c.id)) await fetch(`${URL}/api/conversations/${c.id}`, { method: "DELETE" });
};
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await (await browser.newContext()).newPage();
page.on("console", (m) => { if (m.type() === "error" && !/status of 400/.test(m.text())) problems.push("console: " + m.text()); });
page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
let expected400 = 0;
page.on("response", (r) => { if (r.status() >= 400) { if (r.status() === 400 && r.url().endsWith("/api/projects") && expected400++ === 0) return; problems.push(`http ${r.status()} ${r.url().replace(URL, "")}`); } });
page.on("dialog", (d) => d.accept());
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
  await page.goto(URL);
  await page.getByRole("tab", { name: "Code" }).click();
  check("Code view opens (lazy chunk) with authorize prompt", await page.getByText(/Authorize a project folder/).waitFor({ timeout: 10000 }).then(() => true, () => false));

  // authorization + safety
  await page.getByLabel("Folder path").fill(path.join(root, "does-not-exist"));
  await page.getByRole("button", { name: "Authorize" }).click();
  await page.getByRole("alert").waitFor();
  check("nonexistent folder rejected with visible error", /does not exist/i.test(await page.getByRole("alert").innerText()));
  await page.getByLabel("Folder path").fill(root);
  await page.getByRole("button", { name: "Authorize" }).click();
  await page.getByTestId("tree").getByText("src").waitFor();
  const treeText = await page.getByTestId("tree").innerText();
  check("tree shows project files", treeText.includes("README.md") && treeText.includes("src"));
  check("secrets and node_modules are hidden from tree", !treeText.includes(".env") && !treeText.includes("node_modules"));

  // browse + read
  await page.getByTestId("tree").getByText("src", { exact: false }).first().click();
  await page.locator('[data-path="src/calc.py"]').click();
  await page.getByTestId("file-view").getByText("def add(a, b):").waitFor();
  check("file opens with line numbers", (await page.getByTestId("file-view").innerText()).includes("1") );

  // search + filter
  await page.getByLabel("Search code").fill("greet");
  await page.getByLabel("Search code").press("Enter");
  await page.getByText(/match\(es\)/).waitFor();
  check("code search finds the match", (await page.getByTestId("tree").innerText()).includes("src/calc.py:5"));
  await page.getByLabel("Search code").fill("supersecret123");
  await page.getByLabel("Search code").press("Enter");
  await page.getByText("0 match(es)").waitFor();
  check("search never returns contents of .env", true);
  await page.getByLabel("Search code").fill("");
  await page.getByLabel("File filter").fill("*.md");
  await page.getByLabel("File filter").press("Enter");
  await page.getByText(/1 file\(s\)/).waitFor();
  check("file filter (*.md) lists only matching files", (await page.getByTestId("tree").innerText()).includes("README.md") && !(await page.getByTestId("tree").innerText()).includes("calc.py"));
  await page.getByLabel("File filter").fill("");
  await page.getByLabel("File filter").press("Enter");
  await page.locator('[data-path="src"]').click();
  await page.locator('[data-path="src/calc.py"]').waitFor();

  // AI explain (real model)
  await page.getByRole("button", { name: "Explain" }).click();
  const t0 = Date.now(); const lens = new Set();
  while (Date.now() - t0 < 240000) {
    const t = await page.getByTestId("answer").innerText(); lens.add(t.length);
    if (await page.getByText(/Context used:/).isVisible().catch(() => false)) break;
    await sleep(400);
  }
  const ans = await page.getByTestId("answer").innerText();
  check("Explain streams a real answer", lens.size >= 4 && ans.length > 80, `len=${ans.length}, distinct=${lens.size}`);
  check("answer lists the context sources used", /src\/calc\.py/.test(ans));
  info("explain excerpt: " + ans.slice(0, 160).replace(/\n/g, " "));

  // AI bug analysis
  await page.getByRole("button", { name: "Find bugs" }).click();
  await page.waitForFunction(() => /Context used:/.test(document.querySelector('[data-testid="answer"]')?.textContent ?? ""), null, { timeout: 240000 });
  info("bug analysis excerpt: " + (await page.getByTestId("answer").innerText()).slice(0, 220).replace(/\n/g, " "));
  check("Find bugs completes", true);

  // patch via real model
  await page.getByRole("button", { name: /^Changes/ }).click();
  await page.getByLabel("Change instruction").fill("Fix add so it returns the sum of a and b");
  await page.getByRole("button", { name: "Propose" }).click();
  let proposed = false;
  try { await page.getByTestId("patch").waitFor({ timeout: 240000 }); proposed = true; } catch { /* model failed */ }
  if (proposed) {
    check("real-model patch proposal shows a pending diff", (await page.getByTestId("patch-status").innerText()) === "pending");
    check("nothing written before approval", disk() === CALC);
    const kinds = await page.locator('[data-testid="diff"] tr[data-kind]').evaluateAll((rs) => rs.map((r) => r.dataset.kind));
    check("side-by-side diff has changed rows", kinds.some((k) => k !== "equal"), kinds.join(","));
  } else {
    info("model did not produce a usable patch: " + (await page.getByRole("alert").innerText().catch(() => "?")).slice(0, 200));
    check("real-model patch proposal (3B reliability)", false, "model patch failed; falling back to API-created patch for UI flow checks");
    const pid = (await (await fetch(URL + "/api/projects")).json()).find((p) => p.path.toLowerCase() === root.toLowerCase())?.id;
    await fetch(`${URL}/api/projects/${pid}/patches`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: "src/calc.py", new_content: CALC.replace("a - b", "a + b"), title: "Fix add (manual)" }) });
    await page.getByRole("button", { name: /^Changes/ }).click();
    await page.getByRole("button", { name: /^Changes/ }).click();
    await page.reload(); await page.getByRole("tab", { name: "Code" }).click();
    await page.getByRole("button", { name: /^Changes/ }).click();
    await page.getByText("Fix add (manual)").click();
    await page.getByTestId("patch").waitFor();
  }
  // approve -> disk changes
  await page.getByRole("button", { name: /Approve/ }).click();
  await page.getByText("applied", { exact: true }).first().waitFor();
  check("Approve applies the patch to disk", disk() !== CALC && disk().includes("a + b"), JSON.stringify(disk().split("\n")[1]));
  // rollback
  await page.getByRole("button", { name: "Roll back" }).click();
  await page.getByText("rolled_back").first().waitFor();
  check("Rollback restores the original file exactly", disk() === CALC);
  check("history lists the change with its status", (await page.getByText("rolled_back").count()) > 0);

  // external edit => rollback must refuse? (apply again via manual patch, then edit file)
  const pid2 = (await (await fetch(URL + "/api/projects")).json()).find((p) => p.path.toLowerCase() === root.toLowerCase()).id;
  const mp = await (await fetch(`${URL}/api/projects/${pid2}/patches`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: "README.md", new_content: "# Demo\nChanged\n", title: "readme edit" }) })).json();
  await page.reload(); await page.getByRole("tab", { name: "Code" }).click();
  await page.getByRole("button", { name: /^Changes/ }).click();
  await page.getByText("readme edit").click();
  fs.writeFileSync(path.join(root, "README.md"), "# Demo\nsomeone edited this meanwhile\n");
  await page.getByRole("button", { name: /Approve/ }).click();
  await page.getByRole("alert").waitFor();
  check("conflict detected: stale patch not applied over user's edit", /changed since/.test(await page.getByRole("alert").innerText()) && fs.readFileSync(path.join(root, "README.md"), "utf8").includes("meanwhile"));

  // benchmark tab gating + history
  await page.getByRole("tab", { name: "Benchmark" }).click();
  check("benchmark Run disabled until execution is confirmed", await page.getByRole("button", { name: "Run benchmark" }).isDisabled());
  await page.getByLabel(/I allow PHOENIX to execute/).check();
  check("benchmark Run enabled after confirming", await page.getByRole("button", { name: "Run benchmark" }).isEnabled());
} catch (e) {
  check("test run completed without exception", false, String(e).split("\n")[0]);
  await page.screenshot({ path: "failure-code.png" }).catch(() => {});
} finally {
  console.log("\nFrontend problems observed:", problems.length ? "\n  " + [...new Set(problems)].join("\n  ") : "none");
  console.log(`\n${results.filter((r) => r.ok).length}/${results.length} checks passed`);
  await browser.close();
  await dropTestProjects();
  await cleanupConvs();
  fs.rmSync(root, { recursive: true, force: true });
  process.exit(results.every((r) => r.ok) ? 0 : 1);
}
