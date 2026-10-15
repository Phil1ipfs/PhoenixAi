// Runs a QUICK benchmark (1 task/category) through the real UI against the real model.
import { chromium } from "playwright";
const URL = process.env.PHOENIX_URL ?? "http://127.0.0.1:8000";
const results = [], problems = [];
const check = (n, ok, d = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"} ${n} ${d}`); };
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await (await browser.newContext()).newPage();
page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
page.on("pageerror", (e) => problems.push(e.message));
try {
  await page.goto(URL);
  await page.getByRole("tab", { name: "Benchmark" }).click();
  await page.getByLabel(/I allow PHOENIX to execute/).check();
  await page.getByRole("button", { name: "Run benchmark" }).click();
  await page.getByTestId("bench-progress").waitFor();
  check("progress indicator shown while running", true);
  await page.getByTestId("bench-summary").waitFor({ timeout: 420000 });
  const rows = await page.locator('[data-testid="bench-results"] tbody tr').count();
  check("quick run produced 5 result rows (one per category)", rows === 5, `rows=${rows}`);
  const sum = await page.getByTestId("bench-summary").innerText();
  check("summary shows tok/s, latency and RAM", /tok\/s/.test(sum) && /latency/.test(sum) && /RAM/.test(sum));
  console.log(sum.replace(/\n+/g, " | "));
} catch (e) { check("completed", false, String(e).split("\n")[0]); await page.screenshot({ path: "failure-bench.png" }).catch(() => {}); }
finally {
  console.log("Frontend problems:", problems.length ? problems.join("; ") : "none");
  console.log(`${results.filter(Boolean).length}/${results.length} checks passed`);
  await browser.close(); process.exit(results.every(Boolean) ? 0 : 1);
}
