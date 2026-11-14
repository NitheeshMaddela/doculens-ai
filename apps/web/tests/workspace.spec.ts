import { test, expect, type Page } from "@playwright/test";
import path from "node:path";

const root = path.resolve(__dirname, "../../..");
const asset = (name: string) => path.join(root, "sample_data", name);
const media = (name: string) => path.join(root, "portfolio", "media", name);

async function connect(page: Page) {
  await page.goto("/");
  await page.getByLabel("Workspace access key").fill(process.env.DEMO_ACCESS_KEY ?? "doculens-local");
  await page.getByRole("button", { name: "Connect workspace" }).click();
  await expect(page.getByRole("heading", { name: "Document library" })).toBeVisible();
}

async function question(page: Page, value: string) {
  await page.getByLabel("Question", { exact: true }).fill(value);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

test("workspace workflow: upload, cite exact page, abstain and delete", async ({ page }) => {
  await connect(page);
  const upload = page.getByRole("button", { name: /Drop a PDF/ });
  await upload.focus();
  await expect(upload).toBeFocused();
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), upload.press("Enter")]);
  await chooser.setFiles(asset("cancellation-policy.pdf"));
  await expect(page.getByText("indexed", { exact: true })).toBeVisible({ timeout: 30000 });
  await question(page, "What is the cancellation notice period?");
  await expect(page.getByText("The standard cancellation notice period is 14 days before renewal.", { exact: false }).first()).toBeVisible();
  const citation = page.getByRole("button", { name: /cancellation-policy.pdf · p. 2/ });
  await citation.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("Page 2 of 2", { exact: true })).toBeVisible();
  const image = page.getByRole("img", { name: "Rendered page 2 of cancellation-policy.pdf" });
  await expect(image).toBeVisible();
  expect(await image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await question(page, "What is the payroll tax ID?");
  await expect(page.getByText("Insufficient evidence", { exact: true })).toBeVisible();
  await expect(page.locator(".answer").first().locator(".chip")).toHaveCount(0);
  await page.getByLabel("Response mode").selectOption("search");
  await page.getByLabel("Question", {exact:true}).fill("cancellation notice period");
  await page.getByRole("button", {name:"Search", exact:true}).click();
  await expect(page.getByText("Search results", {exact:true})).toBeVisible();
  await page.getByRole("button", { name: "Delete cancellation-policy.pdf" }).click();
  await page.getByRole("button", { name: "Delete PDF", exact: true }).click();
  await expect(page.getByText("Your library is empty.", { exact: false })).toBeVisible();
  const token = await page.evaluate(() => localStorage.getItem("doculens.workspace.v1"));
  const response = await page.request.post("/api/v1/questions", {
    headers: { Authorization: `Bearer ${token}` }, data: { question: "What is the cancellation notice period?" },
  });
  expect(await response.json()).toMatchObject({ abstained: true, retrieved_count: 0, citations: [] });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("portfolio capture from running product", async ({ page }) => {
  await connect(page);
  await page.getByRole("button", { name: "Load synthetic samples" }).click();
  await expect(page.getByText("indexed", { exact: true })).toHaveCount(5, { timeout: 30000 });
  await page.screenshot({ path: media("workspace.png"), animations: "disabled" });
  await page.getByLabel("Document scope").selectOption({ label: "cancellation-policy.pdf" });
  await question(page, "What is the cancellation notice period?");
  const citation = page.getByRole("button", { name: /cancellation-policy.pdf · p. 2/ });
  await expect(citation).toBeVisible();
  await page.screenshot({ path: media("cited-answer.png"), animations: "disabled" });
  await citation.click();
  const source = page.getByRole("img", { name: "Rendered page 2 of cancellation-policy.pdf" });
  await expect(source).toBeVisible();
  await source.evaluate((image: HTMLImageElement) => image.decode());
  await page.screenshot({ path: media("source-page.png"), animations: "disabled" });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Evaluation", exact: true }).click();
  await expect(page.getByText(/synthetic questions ·/)).toBeVisible();
  await page.screenshot({ path: media("evaluation.png"), animations: "disabled" });
  const token = await page.evaluate(() => localStorage.getItem("doculens.workspace.v1"));
  const headers = { Authorization: `Bearer ${token}` };
  const list = await page.request.get("/api/v1/documents", { headers });
  for (const doc of (await list.json()).items as { id: string }[]) {
    await page.request.delete(`/api/v1/documents/${doc.id}`, { headers });
  }
});
