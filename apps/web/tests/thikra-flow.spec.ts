import { expect, test } from '@playwright/test';

const waitForHydration = (page: import('@playwright/test').Page) =>
  expect(page.locator('.app-shell[data-hydrated="true"]')).toBeVisible({ timeout: 20_000 });

test('Noura Glow verify-then-pay demo reaches evidence-backed approval', async ({ page }) => {
  await page.goto('/');
  await waitForHydration(page);
  await expect(page.getByText('Backend evidence online')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('heading', { name: 'Creative commerce, with receipts.' })).toBeVisible();
  await page.getByRole('link', { name: /Start a creative brief/ }).click();
  await page.waitForURL('**/briefs/new');
  await waitForHydration(page);
  await expect(page.getByRole('heading', { name: 'Creative objective' })).toBeVisible();
  await page.getByRole('button', { name: /Continue/ }).click();
  await expect(page.getByRole('heading', { name: 'Deliverables' })).toBeVisible();
  await page.getByRole('button', { name: /Continue/ }).click();
  await expect(page.getByRole('heading', { name: 'Commercial and creative constraints' })).toBeVisible();
  await page.getByRole('button', { name: /Compile mandate/ }).click();
  await expect(page.getByRole('heading', { name: 'Review the compiled mandate' })).toBeVisible();
  await page.getByRole('button', { name: /Save version & confirm/ }).click();
  await expect(page.getByRole('heading', { name: 'Provider strategy' })).toBeVisible();
  await page.getByRole('button', { name: /Review authorization/ }).click();
  await page.getByRole('button', { name: /Authorize bounded amount/ }).click();
  await expect(page.getByText(/Simulated authorization recorded/)).toBeVisible();
  await page.getByRole('button', { name: /Continue to launch/ }).click();
  await page.getByRole('button', { name: /Launch accountable generation/ }).click();
  await expect(page.getByRole('heading', { name: /Noura Glow/ })).toBeVisible();
  await page.getByRole('button', { name: /Confirm storyboard & start/ }).click();
  await expect(page.getByText('Arabic narration present')).toBeVisible({ timeout: 15_000 });
  await page.getByRole('button', { name: /Retry failed component/ }).click();
  await expect(page.getByText(/missing Arabic narration was regenerated/i)).toBeVisible();
  await page.getByRole('button', { name: /Approve final delivery/ }).click();
  await expect(page.getByText(/Complete/).first()).toBeVisible();
  await page.getByRole('link', { name: /View asset record/ }).click();
  await waitForHydration(page);
  await expect(page.getByRole('heading', { name: 'Asset Library' })).toBeVisible();
  await page.goto('/evidence');
  await waitForHydration(page);
  await page.getByRole('button', { name: /Graph/ }).click();
  await expect(page.getByLabel('Evidence relationship graph')).toBeVisible();
  await page.goto('/cases');
  await waitForHydration(page);
  const caseHref = await page.getByRole('link', { name: /^Case [a-f0-9]{8}$/i }).first().getAttribute('href');
  expect(caseHref).toMatch(/^\/cases\/[^/]+$/);
  await page.goto(caseHref!);
  await waitForHydration(page);
  await expect(page.getByRole('heading', { name: /Case [a-f0-9]{8}/i })).toBeVisible();
  await page.getByLabel('Status').selectOption('RESOLVED');
  await page.getByLabel('Resolution').fill('Narration was retried, reverified, and approved by the principal.');
  await page.getByRole('button', { name: /Save case/ }).click();
  await expect(page.getByText(/Resolved/).first()).toBeVisible();
});

test('human marketplace order reaches signed verified delivery', async ({ page }) => {
  await page.goto('/services');
  await waitForHydration(page);
  await expect(page.getByRole('heading', { name: 'Outcomes an agent can actually buy.' })).toBeVisible();
  await expect(page.locator('.service-card')).toHaveCount(6);
  const flagship = page.locator('.service-card').filter({ hasText: 'Complete Vertical Advertisement' });
  await expect(flagship.getByText('$5.00')).toBeVisible();
  await flagship.getByRole('link', { name: /Inspect & request quote/ }).click();
  await page.waitForURL('**/services/verified-vertical-ad');
  await expect(page.getByRole('heading', { name: 'Complete Vertical Advertisement' })).toBeVisible();
  await page.getByRole('button', { name: 'Request quote' }).click();
  await expect(page.getByText('Quoted total')).toBeVisible();
  await page.getByRole('button', { name: 'Accept quote & create order' }).click();
  await page.getByRole('link', { name: 'Continue to payment' }).click();
  await expect(page.getByRole('button', { name: 'Create bounded authorization' })).toBeVisible();
  await page.getByRole('button', { name: 'Create bounded authorization' }).click();
  await page.getByRole('button', { name: 'Approve simulated demo payment' }).click();
  await page.getByRole('button', { name: 'Start paid fulfillment' }).click();
  await page.getByRole('button', { name: /Retry failed component/ }).click();
  await expect(page.getByText('Verified deliverables')).toBeVisible();
  await expect(page.getByText('Signed payment-to-delivery receipt')).toBeVisible();
});

test('marketplace remains usable at a mobile viewport', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/services');
  await waitForHydration(page);
  await expect(page.getByRole('heading', { name: 'Outcomes an agent can actually buy.' })).toBeVisible();
  await expect(page.locator('.service-card')).toHaveCount(6);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await expect(page.getByRole('navigation').getByRole('link', { name: 'Marketplace' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('marketplace-mobile.png'), fullPage: true });
});
