# 5MP Conservation Globe - Test Suite

## Quick Start

```bash
# Run all tests
./tests/run_all.sh

# Run specific test suites
./tests/run_all.sh db    # Database tests
./tests/run_all.sh api   # API tests  
./tests/run_all.sh ui    # UI URL tests
```

## Test Architecture

### 1. Database Tests (`db_tests.sh`)

Verifies data integrity and query performance:
- Table existence
- Data counts and ranges
- Classification data
- Polygon ID links
- Index usage

### 2. API Tests (`api_tests.sh`)

Tests all REST endpoints:
- Authentication (password validation)
- Parks API (`/api/parks`, `/api/parks/:id/stats`)
- Fire API (narratives, realtime, alerts)
- Features API (trajectories, settlements, deforestation)
- Climate & Species
- Grid API with filters
- Admin endpoints

### 3. UI Tests

Three levels of UI testing:

#### Level 1: URL Tests (`run_ui_tests.sh`)
Basic validation that URL state params don't break page load.

#### Level 2: Browser Console Tests
Manual testing with the `window.TEST` helper:

```javascript
// Navigate to test mode
// http://localhost:8000/?pwd=test2026&test=1

// Run all tests
await runUITests()

// Or run specific tests
await runUITests(['page_loads', 'filter_panel_opens'])

// Manual assertions
TEST.assertVisible('#map', 'Map visible')
TEST.assertExists('.pa-popup', 'Popup exists')
TEST.isPanelOpen('admin')  // Returns true/false
TEST.done()  // Print results
```

#### Level 3: Playwright Tests (`playwright/globe.spec.js`)
Full browser automation for CI:

```bash
npm install -D @playwright/test
npx playwright test tests/playwright/
```

## Share Link State Encoding

The app's share link encodes full UI state, making testing trivial:

| Parameter | Example | Description |
|-----------|---------|-------------|
| `pwd` | `test2026` | Password |
| `test` | `1` | Enable TEST helper |
| `lat`, `lng`, `z` | `0,25,5` | Map position |
| `from`, `to` | `2024-01-01` | Time range |
| `bbox` | `20,-5,30,5` | Bounding box filter |
| `parks` | `CAF_Chinko,COD_Virunga` | Selected parks |
| `country` | `COD` | Country filter |
| `types` | `foot,vehicle` | Movement types |
| `q` | `virunga` | Search query |
| `popup` | `CAF_Chinko` | Open park popup |
| `sections` | `fire,deforestation` | Open accordion sections |
| `pinned` | `CAF_Chinko:fire_trajectory` | Pinned layers |
| `starred_parks` | `CAF_Chinko,COD_Virunga` | Starred parks |
| `starred_narratives` | `CAF_Chinko:fire` | Starred narratives |
| `notif` | `1` | Notification dropdown open |
| `keystones` | `0` | Keystones toggle |
| `panel` | `filter,star,admin,upload` | Open panel/modal |
| `admin_tab` | `learning,features` | Active admin tab |
| `map_sheet` | `car,sudan,histmap` | Highlight one card in Map Settings |

## TEST Helper API

When `?test=1` is in the URL, `window.TEST` provides:

### State Queries
```javascript
TEST.isPanelOpen('admin')      // Check if panel is open
TEST.isPopupOpen('CAF_Chinko') // Check if popup is open for park
TEST.isAccordionOpen('CAF_Chinko', 'fire')  // Check accordion state
TEST.isPinned('CAF_Chinko', 'fire_trajectory')  // Check pinned state
TEST.isStarred('parks', 'CAF_Chinko')  // Check starred state
TEST.getMapCenter()            // {lat, lng, zoom}
TEST.getAdminTab()             // Current admin tab name
TEST.getVisibleParkIds()       // Array of selected park IDs
TEST.getStarredCount()         // Total starred items
TEST.getPinnedCount()          // Total pinned layers
```

### Element Queries
```javascript
TEST.exists(selector)          // Check element exists
TEST.isVisible(selector)       // Check element is visible
TEST.getText(selector)         // Get element text
TEST.getCount(selector)        // Count matching elements
TEST.hasClass(selector, class) // Check element has class
```

### Assertions
```javascript
TEST.assert(condition, msg)           // Basic assertion
TEST.assertEqual(actual, expected, msg)
TEST.assertVisible(selector, msg)
TEST.assertNotVisible(selector, msg)
TEST.assertExists(selector, msg)
TEST.assertText(selector, text, msg)
TEST.assertCount(selector, n, msg)
TEST.assertCountMin(selector, n, msg)
```

### Wait Helpers
```javascript
await TEST.waitFor(() => condition, timeoutMs)
await TEST.waitForSelector(selector, timeoutMs)
await TEST.waitForVisible(selector, timeoutMs)
```

### Test Suite
```javascript
await TEST.runSuite([
  { name: 'test1', fn: async () => { TEST.assertExists('#map') } },
  { name: 'test2', fn: async () => { TEST.assertVisible('.popup') } },
])
```

## Example Test URLs

```bash
# Basic page load
http://localhost:8000/?pwd=test2026&test=1

# Filter panel open
http://localhost:8000/?pwd=test2026&test=1&panel=filter

# Admin panel with learning tab
http://localhost:8000/?pwd=test2026&test=1&panel=admin&admin_tab=learning

# Park popup with fire accordion
http://localhost:8000/?pwd=test2026&test=1&popup=CAF_Chinko&sections=fire

# Complex state: popup + starred + map position
http://localhost:8000/?pwd=test2026&test=1&popup=TZA_Serengeti&sections=fire,species&starred_parks=TZA_Serengeti&lat=-2&lng=35&z=7
```

## CI Integration

For continuous integration, use Playwright:

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup
        run: |
          make build
          ./server &
          sleep 5
      - name: DB Tests
        run: ./tests/db_tests.sh
      - name: API Tests
        run: ./tests/api_tests.sh
      - name: UI Tests
        run: |
          npm install -D @playwright/test
          npx playwright install chromium
          npx playwright test tests/playwright/
```
