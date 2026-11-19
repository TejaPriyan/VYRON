#!/usr/bin/env bash
# Run every VYRON check that needs no installed packages (no pip, no npm).
#
#   scripts/verify_stdlib.sh            run what can run here, report what was skipped
#   scripts/verify_stdlib.sh --strict   a missing tool (node, tsc, docker) is a failure, not a skip (use in CI)
#
# This is NOT the full toolchain. ruff, mypy, pytest, the Next.js build, the database adapters and the
# Docker image builds still need installed dependencies or a Docker daemon and are UNVERIFIED until they
# have run; see docs/IMPLEMENTATION_STATUS.md. Passing this script does not mean the application is
# production ready.

set -uo pipefail
cd "$(dirname "$0")/.."

strict=0
[[ "${1:-}" == "--strict" ]] && strict=1

passed=()
failed=()
skipped=()

have() { command -v "$1" >/dev/null 2>&1; }

# step NAME REQUIRED_TOOL COMMAND...   (REQUIRED_TOOL "-" means none)
step() {
  local name=$1 tool=$2
  shift 2
  printf '\n== %s ==\n' "$name"
  if [[ "$tool" != "-" ]] && ! have "$tool"; then
    printf 'SKIP  %s (%s not found)\n' "$name" "$tool"
    skipped+=("$name")
    return
  fi
  if "$@"; then
    printf 'PASS  %s\n' "$name"
    passed+=("$name")
  else
    printf 'FAIL  %s\n' "$name"
    failed+=("$name")
  fi
}

node_supports_type_stripping() {
  # Node runs .ts files without flags from 22.18.
  have node || return 1
  local version
  version=$(node -p 'process.versions.node')
  [[ "$(printf '%s\n22.18.0\n' "$version" | sort -V | head -n1)" == "22.18.0" ]]
}

api_tests()  { (cd apps/api && PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests); }
root_tests() { python3 -W error::ResourceWarning -m unittest discover -s tests; }
node_tests() {
  if ! node_supports_type_stripping; then
    echo "Node >= 22.18 is required to run TypeScript tests without a flag (found: $(node -v))"
    return 1
  fi
  node --test packages/*/src/*.test.ts apps/web/prototype/src/*.test.ts
}
compiles()   { python3 -m compileall -q apps/api/src apps/api/tests apps/web/design apps/web/prototype infrastructure scripts tests packages/types/tools; }
prototype_builds() { python3 scripts/build_prototype.py; }
generators() {
  local status=0 name
  for name in generate_openapi generate_seed_sql generate_api_docs generate_design_tokens generate_ts_types; do
    PYTHONPATH=apps/api/src python3 "scripts/${name}.py" --check || status=1
  done
  return $status
}
compose_resolves() {
  local status=0 profile
  export POSTGRES_PASSWORD=verify-only NEO4J_PASSWORD=verify-only-1
  docker compose config --quiet || status=1
  for profile in seed app; do
    docker compose --profile "$profile" config --quiet || status=1
  done
  return $status
}

step "Python compiles"                    -      compiles
step "API unit tests (unittest)"          -      api_tests
step "Root tests (design, TS, Docker, hygiene, cross-language)" - root_tests
step "TypeScript unit tests (node --test)" node  node_tests
step "TypeScript strict type check (tsc)"  tsc   tsc -p tsconfig.check.json
step "Browser prototype compiles (tsc emit)" tsc  prototype_builds
step "Generated files are current"        -      generators
step "docker compose config (no daemon)"  docker compose_resolves

printf '\n== summary ==\n'
printf 'passed:  %d\n' "${#passed[@]}"
printf 'failed:  %d\n' "${#failed[@]}"
printf 'skipped: %d\n' "${#skipped[@]}"
for name in "${failed[@]+"${failed[@]}"}"; do printf '  FAIL  %s\n' "$name"; done
for name in "${skipped[@]+"${skipped[@]}"}"; do printf '  SKIP  %s\n' "$name"; done

if ((${#failed[@]} > 0)); then exit 1; fi
if ((strict == 1 && ${#skipped[@]} > 0)); then
  echo "strict mode: skipped steps count as failures"
  exit 1
fi
exit 0
