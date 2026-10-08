#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${ROOT_DIR}/install.sh"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local output="$1" expected="$2"
  [[ "${output}" == *"${expected}"* ]] || fail "expected output to contain: ${expected}"
}

run_case() {
  local body="$1"
  bash -c 'source "$1"; '"${body}"'' test-installer "${INSTALLER}"
}

output="$(run_case '
  LOG_FILE="" ASSUME_YES=0 MODEL_CONFIGURED=0 MODEL_SETUP_UNAVAILABLE=0
  model_setup_is_available() { return 1; }
  configure_ai_model
  [[ "${MODEL_SETUP_UNAVAILABLE}" == "1" ]]
')"
assert_contains "${output}" "does not include the AI model setup wizard"

output="$(run_case '
  LOG_FILE="" ASSUME_YES=0 MODEL_CONFIGURED=0 MODEL_SETUP_UNAVAILABLE=0
  model_setup_is_available() { return 0; }
  model_is_configured() { return 0; }
  run_model_setup_wizard() { fail "wizard should not run"; }
  configure_ai_model
  [[ "${MODEL_CONFIGURED}" == "1" ]]
')"
assert_contains "${output}" "already configured"

output="$(run_case '
  LOG_FILE="" ASSUME_YES=1 MODEL_CONFIGURED=0 MODEL_SETUP_UNAVAILABLE=0
  model_setup_is_available() { return 0; }
  model_is_configured() { return 1; }
  run_model_setup_wizard() { fail "wizard should not run"; }
  configure_ai_model
')"
assert_contains "${output}" "skipped because --yes is enabled"

output="$(run_case '
  LOG_FILE="" ASSUME_YES=0 MODEL_CONFIGURED=0 MODEL_SETUP_UNAVAILABLE=0
  model_setup_is_available() { return 0; }
  model_is_configured() { [[ "${MODEL_CONFIGURED}" == "1" ]]; }
  model_setup_has_terminal() { return 0; }
  run_model_setup_wizard() { MODEL_CONFIGURED=1; }
  configure_ai_model
  [[ "${MODEL_CONFIGURED}" == "1" ]]
')"
assert_contains "${output}" "System AI model is ready"

printf 'configure_ai_model tests passed\n'
