// Measures the memory footprint of the PHOENIX web UI in headless Edge and of the Python backend. Not a pass/fail test.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";

const URL = process.env.PHOENIX_URL ?? "http://127.0.0.1:8000";
const ps = (cmd) => execFileSync("powershell", ["-NoProfile", "-Command", cmd], { encoding: "utf8" }).trim();
const mb = (b) => Math.round(Number(b) / 2 ** 20);

function edgeMem() {
  // only the browser we launched (user-data-dir under playwright's temp profile), never the user's own Edge windows
  const out = ps(`$p = Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Where-Object { $_.CommandLine -like '*playwright*' }; ` +
                 `"{0} {1} {2}" -f $p.Count, ($p | Measure-Object WorkingSetSize -Sum).Sum, ($p | Measure-Object PageFileUsage -Sum).Sum`).split(" ");
  return { processes: Number(out[0]), working_set_mb: mb(out[1]), private_commit_mb: Math.round(Number(out[2]) / 1024) };
}
function backendMem() {
  const out = ps(`$p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*run.py*' }; ` +
                 `"{0} {1}" -f $p.Count, ($p | Measure-Object WorkingSetSize -Sum).Sum`).split(" ");
  return { processes: Number(out[0]), working_set_mb: mb(out[1]) };
}

const result = { backend_idle: backendMem() };
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await (await browser.newContext()).newPage();
await page.goto(URL);
await page.getByLabel("Message").waitFor();
await new Promise((r) => setTimeout(r, 2000));
result.edge_chat_view = edgeMem();
await page.getByRole("tab", { name: "Code" }).click();
await new Promise((r) => setTimeout(r, 1500));
result.edge_code_view = edgeMem();
await page.getByRole("tab", { name: "Chat" }).click();
await page.getByPlaceholder(/Message PHOENIX/).fill("Reply with just the word OK.");
await page.getByRole("button", { name: "Send" }).click();
await page.getByText(/tok\/s/).waitFor({ timeout: 180000 });
await new Promise((r) => setTimeout(r, 1000));
result.edge_after_reply = edgeMem();
result.backend_after_reply = backendMem();
const jsHeap = await page.evaluate(() => performance.memory ? Math.round(performance.memory.usedJSHeapSize / 2 ** 20) : null);
result.page_js_heap_mb = jsHeap;
console.log(JSON.stringify(result, null, 1));
await browser.close();
