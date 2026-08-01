import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests', timeout: 60_000, fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:43191', trace: 'retain-on-failure' },
  webServer: { command: 'node ../../scripts/e2e-server.mjs', url: 'http://127.0.0.1:43191', reuseExistingServer: false, timeout: 120_000 },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
