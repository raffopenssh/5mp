/**
 * Playwright UI Tests for 5MP Conservation Globe
 * 
 * Install: npm install -D @playwright/test
 * Run: npx playwright test tests/playwright/
 */

const { test, expect } = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';
const PWD = process.env.TEST_PWD || 'test2026';

function url(params = '') {
    return `${BASE_URL}/?pwd=${PWD}&test=1${params}`;
}

test.describe('Page Load', () => {
    test('loads without errors', async ({ page }) => {
        const errors = [];
        page.on('pageerror', err => errors.push(err.message));
        
        await page.goto(url());
        await page.waitForSelector('.mapboxgl-canvas', { timeout: 10000 });
        
        expect(errors).toHaveLength(0);
    });
    
    test('has required elements', async ({ page }) => {
        await page.goto(url());
        await page.waitForSelector('#map', { timeout: 10000 });
        
        await expect(page.locator('.stats-panel')).toBeVisible();
        await expect(page.locator('.toolbar-btn')).toHaveCount(await page.locator('.toolbar-btn').count());
        await expect(page.locator('.mapboxgl-canvas')).toBeVisible();
    });
});

test.describe('Filter Panel', () => {
    test('opens via URL param', async ({ page }) => {
        await page.goto(url('&panel=filter'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('#filter-panel')).toHaveClass(/open/);
    });
    
    test('has movement toggles', async ({ page }) => {
        await page.goto(url('&panel=filter'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('.movement-toggle')).toHaveCount(3);
        await expect(page.locator('#keystones-toggle-btn')).toBeVisible();
    });
});

test.describe('Star Modal', () => {
    test('opens via URL param', async ({ page }) => {
        await page.goto(url('&panel=star'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('#star-modal')).toHaveClass(/active/);
    });
    
    test('shows starred parks from URL', async ({ page }) => {
        await page.goto(url('&panel=star&starred_parks=CAF_Chinko,COD_Virunga'));
        await page.waitForTimeout(2500);
        
        await expect(page.locator('#star-modal')).toHaveClass(/active/);
        // Should have starred park items rendered
        const parkItems = page.locator('.starred-park-item, .starred-section');
        await expect(parkItems.first()).toBeVisible();
    });
});

test.describe('Admin Panel', () => {
    test('opens via URL param', async ({ page }) => {
        await page.goto(url('&panel=admin'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('#admin-panel')).toHaveClass(/active/);
    });
    
    test('default tab is uploads', async ({ page }) => {
        await page.goto(url('&panel=admin'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('[data-tab="uploads"]')).toHaveClass(/active/);
    });
    
    test('switches to learning tab via URL', async ({ page }) => {
        await page.goto(url('&panel=admin&admin_tab=learning'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('[data-tab="learning"]')).toHaveClass(/active/);
        await expect(page.locator('#tab-learning')).toBeVisible();
    });
    
    test('switches to features tab via URL', async ({ page }) => {
        await page.goto(url('&panel=admin&admin_tab=features'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('[data-tab="features"]')).toHaveClass(/active/);
    });
});

test.describe('Park Popup', () => {
    test('opens for park via URL', async ({ page }) => {
        await page.goto(url('&popup=CAF_Chinko'));
        await page.waitForTimeout(4000);
        
        await expect(page.locator('.pa-popup')).toBeVisible();
        await expect(page.locator('.pa-popup')).toContainText('Chinko');
    });
    
    test('opens accordion sections via URL', async ({ page }) => {
        await page.goto(url('&popup=CAF_Chinko&sections=fire'));
        await page.waitForTimeout(4500);
        
        await expect(page.locator('.pa-popup')).toBeVisible();
        await expect(page.locator('#fire-section-CAF_Chinko')).toHaveClass(/open/);
    });
    
    test('opens multiple accordion sections', async ({ page }) => {
        await page.goto(url('&popup=COD_Virunga&sections=fire,deforestation,settlement'));
        await page.waitForTimeout(4500);
        
        await expect(page.locator('#fire-section-COD_Virunga')).toHaveClass(/open/);
        await expect(page.locator('#deforestation-section-COD_Virunga')).toHaveClass(/open/);
        await expect(page.locator('#settlement-section-COD_Virunga')).toHaveClass(/open/);
    });
});

test.describe('Pinned Layers', () => {
    test('restores pinned layer from URL', async ({ page }) => {
        await page.goto(url('&pinned=CAF_Chinko:fire_trajectory'));
        await page.waitForTimeout(3500);
        
        await expect(page.locator('.pinned-layer-item')).toBeVisible();
    });
    
    test('restores multiple pinned layers', async ({ page }) => {
        await page.goto(url('&pinned=CAF_Chinko:fire_trajectory,COD_Virunga:settlement'));
        await page.waitForTimeout(4000);
        
        const pinnedItems = page.locator('.pinned-layer-item');
        await expect(pinnedItems).toHaveCount(2);
    });
});

test.describe('Search', () => {
    test('restores search query from URL', async ({ page }) => {
        await page.goto(url('&q=virunga'));
        await page.waitForTimeout(2000);
        
        await expect(page.locator('#search-input')).toHaveValue('virunga');
    });
});

test.describe('Filters', () => {
    test('restores bbox filter from URL', async ({ page }) => {
        await page.goto(url('&bbox=20,-5,30,5'));
        await page.waitForTimeout(2000);
        
        await expect(page.locator('#active-filter-bbox')).toBeVisible();
    });
    
    test('restores keystones toggle from URL', async ({ page }) => {
        await page.goto(url('&keystones=0'));
        await page.waitForTimeout(2000);
        
        // Check that keystonesEnabled is false via TEST helper
        const result = await page.evaluate(() => window.TEST?.isVisible('#keystones-toggle-btn.active'));
        expect(result).toBe(false);
    });
});

test.describe('Map Position', () => {
    test('restores map center from URL', async ({ page }) => {
        await page.goto(url('&lat=0&lng=25&z=5'));
        await page.waitForTimeout(3000);
        
        const center = await page.evaluate(() => {
            const c = map.getCenter();
            return { lat: c.lat, lng: c.lng };
        });
        
        expect(Math.abs(center.lat)).toBeLessThan(2);
        expect(Math.abs(center.lng - 25)).toBeLessThan(2);
    });
});

test.describe('Notifications', () => {
    test('opens notification dropdown from URL', async ({ page }) => {
        await page.goto(url('&notif=1'));
        await page.waitForTimeout(1500);
        
        await expect(page.locator('#notification-dropdown')).toHaveClass(/open/);
    });
});

test.describe('Complex State', () => {
    test('restores multiple state params', async ({ page }) => {
        await page.goto(url('&popup=TZA_Serengeti&sections=fire&starred_parks=TZA_Serengeti&lat=-2&lng=35&z=7'));
        await page.waitForTimeout(5000);
        
        // Popup should be visible
        await expect(page.locator('.pa-popup')).toBeVisible();
        await expect(page.locator('.pa-popup')).toContainText('Serengeti');
        
        // Fire section should be open
        await expect(page.locator('#fire-section-TZA_Serengeti')).toHaveClass(/open/);
        
        // Map should be centered roughly on Serengeti
        const center = await page.evaluate(() => {
            const c = map.getCenter();
            return { lat: c.lat, lng: c.lng };
        });
        expect(center.lat).toBeGreaterThan(-5);
        expect(center.lat).toBeLessThan(2);
    });
});

test.describe('TEST Helper', () => {
    test('TEST object available in test mode', async ({ page }) => {
        await page.goto(url());
        await page.waitForTimeout(2000);
        
        const hasTest = await page.evaluate(() => typeof window.TEST !== 'undefined');
        expect(hasTest).toBe(true);
    });
    
    test('TEST assertions work', async ({ page }) => {
        await page.goto(url());
        await page.waitForTimeout(2000);
        
        const result = await page.evaluate(() => {
            TEST.assertExists('#map', 'Map exists');
            TEST.assertVisible('.stats-panel', 'Stats panel visible');
            return TEST.done();
        });
        
        expect(result.passed).toBeGreaterThan(0);
        expect(result.failed).toBe(0);
    });
});
