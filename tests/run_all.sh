#!/bin/bash
# Run all tests for 5MP Conservation Globe
#
# Usage: ./tests/run_all.sh [db|api|ui|docs|all]
#
# Examples:
#   ./tests/run_all.sh          # Run all tests
#   ./tests/run_all.sh db       # Run only database tests
#   ./tests/run_all.sh api      # Run only API tests
#   ./tests/run_all.sh ui       # Run only UI URL tests
#   ./tests/run_all.sh docs     # Run only docs budget tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

red() { echo -e "\033[31m$1\033[0m"; }
green() { echo -e "\033[32m$1\033[0m"; }
yellow() { echo -e "\033[33m$1\033[0m"; }

TEST_TYPE="${1:-all}"
EXIT_CODE=0

echo "========================================"
echo "5MP Conservation Globe - Test Suite"
echo "========================================"
echo

run_db_tests() {
    yellow "\n>>> Running Database Tests...\n"
    if ./tests/db_tests.sh; then
        green "Database tests: PASSED"
    else
        red "Database tests: FAILED"
        EXIT_CODE=1
    fi
}

run_api_tests() {
    yellow "\n>>> Running API Tests...\n"
    if ./tests/api_tests.sh; then
        green "API tests: PASSED"
    else
        red "API tests: FAILED"
        EXIT_CODE=1
    fi
}

run_docs_tests() {
    yellow "\n>>> Running Docs Budget Tests...\n"
    if ./tests/docs_tests.sh; then
        green "Docs tests: PASSED"
    else
        red "Docs tests: FAILED"
        EXIT_CODE=1
    fi
}

run_ui_tests() {
    yellow "\n>>> Running UI URL Tests...\n"
    if ./tests/run_ui_tests.sh; then
        green "UI URL tests: PASSED"
    else
        red "UI URL tests: FAILED"
        EXIT_CODE=1
    fi
}

case "$TEST_TYPE" in
    db)
        run_db_tests
        ;;
    api)
        run_api_tests
        ;;
    ui)
        run_ui_tests
        ;;
    docs)
        run_docs_tests
        ;;
    all)
        run_db_tests
        run_api_tests
        run_ui_tests
        run_docs_tests
        ;;
    *)
        echo "Usage: $0 [db|api|ui|docs|all]"
        exit 1
        ;;
esac

echo
echo "========================================"
if [[ $EXIT_CODE -eq 0 ]]; then
    green "All requested tests passed!"
else
    red "Some tests failed."
fi
echo "========================================"

# Print instructions for full UI testing
if [[ "$TEST_TYPE" == "all" || "$TEST_TYPE" == "ui" ]]; then
    echo
    yellow "Note: For full UI DOM testing, use one of:"
    echo "  1. Browser: http://localhost:8000/?pwd=test2026&test=1"
    echo "     Then run: await runUITests() in console"
    echo "  2. Playwright: npx playwright test tests/playwright/"
fi

exit $EXIT_CODE
