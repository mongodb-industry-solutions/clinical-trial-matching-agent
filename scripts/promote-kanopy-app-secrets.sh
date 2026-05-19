#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-industrysolutions}"
ENV_FILE="${ENV_FILE:-backend/.env}"
SECRET_GROUP="${SECRET_GROUP:-}"
STAGING_SECRET_GROUP="${STAGING_SECRET_GROUP:-clinical-trial-matching-agent-staging}"
PROD_SECRET_GROUP="${PROD_SECRET_GROUP:-clinical-trial-matching-agent-production}"
TARGETS="${TARGETS:-staging production}"
DRY_RUN="${DRY_RUN:-false}"
DEFAULT_KUBECONFIG="${HOME}/.kube/config.staging:${HOME}/.kube/config.prod"

usage() {
  cat <<'EOF'
Promote Clinical Trial Matching app secrets into Kanopy via helm ksec.

Usage:
  scripts/promote-kanopy-app-secrets.sh [--secret-group NAME] [--env-file PATH] [--targets "staging production"] [--dry-run]

Examples:
  scripts/promote-kanopy-app-secrets.sh
  scripts/promote-kanopy-app-secrets.sh --targets staging
  scripts/promote-kanopy-app-secrets.sh --secret-group ctm-shared
  DRY_RUN=true scripts/promote-kanopy-app-secrets.sh

Notes:
  - By default this script writes to:
      staging    -> clinical-trial-matching-agent-staging
      production -> clinical-trial-matching-agent-production
  - --secret-group overrides both targets to use one shared Kanopy secret group.
  - It expects kubectl, helm, and the ksec plugin to already be installed and configured.
  - Required values are loaded from ENV_FILE first, then from the current shell environment.
  - Per-environment keys are supported with STAGING_* and PROT_* prefixes.
  - CHATCOMPLETIONS_MODEL_ID and EMBEDDINGS_MODEL_ID may be provided directly, or derived from
    BEDROCK_DEFAULT_MODEL / AGENTIC_BEDROCK_MODEL and the default Cohere embedding model.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --secret-group)
      SECRET_GROUP="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --targets)
      TARGETS="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    case "$1" in
      kubectl)
        echo "Install it with: brew install kubectl" >&2
        ;;
      helm)
        echo "Install it with: brew install helm" >&2
        echo "Then install the ksec plugin with: helm plugin install https://github.com/kanopy-platform/ksec --verify=false" >&2
        ;;
    esac
    exit 1
  }
}

if [[ "$DRY_RUN" != "true" ]]; then
  if [[ -z "${KUBECONFIG:-}" && -f "${HOME}/.kube/config.staging" && -f "${HOME}/.kube/config.prod" ]]; then
    export KUBECONFIG="$DEFAULT_KUBECONFIG"
  fi
  require_cmd kubectl
  require_cmd helm
  helm plugin list | grep -q '\bksec\b' || {
    echo "Helm ksec plugin is not installed." >&2
    exit 1
  }
fi

[[ -f "$ENV_FILE" ]] || {
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
}

eval "$(
  python3 - "$ENV_FILE" <<'PY'
import json
import shlex
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
raw = {}

for line in env_path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    raw[key.strip()] = value.strip()

def resolve(name, *fallbacks, default=""):
    for key in (name, *fallbacks):
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return default

resolved = {
    "MONGODB_URI": resolve("MONGODB_URI"),
    "DATABASE_NAME": resolve("DATABASE_NAME"),
    "CLINICAL_TRIALS_MONGODB_URI": resolve("CLINICAL_TRIALS_MONGODB_URI", "MONGODB_URI"),
    "CLINICAL_TRIALS_DATABASE_NAME": resolve("CLINICAL_TRIALS_DATABASE_NAME", "DATABASE_NAME"),
    "LEAFY_HOSPITAL_MONGODB_URI": resolve(
        "LEAFY_HOSPITAL_MONGODB_URI",
        "CLINICAL_TRIALS_MONGODB_URI",
        "MONGODB_URI",
    ),
    "LEAFY_HOSPITAL_DATABASE_NAME": resolve(
        "LEAFY_HOSPITAL_DATABASE_NAME",
        "CLINICAL_TRIALS_DATABASE_NAME",
        "DATABASE_NAME",
    ),
    "VOYAGE_API_KEY": resolve("VOYAGE_API_KEY"),
    "CHATCOMPLETIONS_MODEL_ID": resolve("CHATCOMPLETIONS_MODEL_ID", "BEDROCK_DEFAULT_MODEL", "AGENTIC_BEDROCK_MODEL"),
    "EMBEDDINGS_MODEL_ID": resolve("EMBEDDINGS_MODEL_ID", default="cohere.embed-english-v3"),
    "STAGING_MONGODB_URI": resolve("STAGING_MONGODB_URI", "MONGODB_URI"),
    "STAGING_DATABASE_NAME": resolve("STAGING_DATABASE_NAME", "DATABASE_NAME"),
    "STAGING_CLINICAL_TRIALS_MONGODB_URI": resolve(
        "STAGING_CLINICAL_TRIALS_MONGODB_URI",
        "STAGING_MONGODB_URI",
        "CLINICAL_TRIALS_MONGODB_URI",
        "MONGODB_URI",
    ),
    "STAGING_CLINICAL_TRIALS_DATABASE_NAME": resolve(
        "STAGING_CLINICAL_TRIALS_DATABASE_NAME",
        "STAGING_DATABASE_NAME",
        "CLINICAL_TRIALS_DATABASE_NAME",
        "DATABASE_NAME",
    ),
    "STAGING_LEAFY_HOSPITAL_MONGODB_URI": resolve(
        "STAGING_LEAFY_HOSPITAL_MONGODB_URI",
        "STAGING_CLINICAL_TRIALS_MONGODB_URI",
        "STAGING_MONGODB_URI",
        "LEAFY_HOSPITAL_MONGODB_URI",
        "CLINICAL_TRIALS_MONGODB_URI",
        "MONGODB_URI",
    ),
    "STAGING_LEAFY_HOSPITAL_DATABASE_NAME": resolve(
        "STAGING_LEAFY_HOSPITAL_DATABASE_NAME",
        "STAGING_CLINICAL_TRIALS_DATABASE_NAME",
        "STAGING_DATABASE_NAME",
        "LEAFY_HOSPITAL_DATABASE_NAME",
        "CLINICAL_TRIALS_DATABASE_NAME",
        "DATABASE_NAME",
    ),
    "STAGING_VOYAGE_API_KEY": resolve("STAGING_VOYAGE_API_KEY", "VOYAGE_API_KEY"),
    "STAGING_CHATCOMPLETIONS_MODEL_ID": resolve("STAGING_CHATCOMPLETIONS_MODEL_ID", "CHATCOMPLETIONS_MODEL_ID", "BEDROCK_DEFAULT_MODEL", "AGENTIC_BEDROCK_MODEL"),
    "STAGING_EMBEDDINGS_MODEL_ID": resolve("STAGING_EMBEDDINGS_MODEL_ID", "EMBEDDINGS_MODEL_ID", default="cohere.embed-english-v3"),
    "PROT_MONGODB_URI": resolve("PROT_MONGODB_URI", "MONGODB_URI"),
    "PROT_DATABASE_NAME": resolve("PROT_DATABASE_NAME", "DATABASE_NAME"),
    "PROT_CLINICAL_TRIALS_MONGODB_URI": resolve(
        "PROT_CLINICAL_TRIALS_MONGODB_URI",
        "PROT_MONGODB_URI",
        "CLINICAL_TRIALS_MONGODB_URI",
        "MONGODB_URI",
    ),
    "PROT_CLINICAL_TRIALS_DATABASE_NAME": resolve(
        "PROT_CLINICAL_TRIALS_DATABASE_NAME",
        "PROT_DATABASE_NAME",
        "CLINICAL_TRIALS_DATABASE_NAME",
        "DATABASE_NAME",
    ),
    "PROT_LEAFY_HOSPITAL_MONGODB_URI": resolve(
        "PROT_LEAFY_HOSPITAL_MONGODB_URI",
        "PROT_CLINICAL_TRIALS_MONGODB_URI",
        "PROT_MONGODB_URI",
        "LEAFY_HOSPITAL_MONGODB_URI",
        "CLINICAL_TRIALS_MONGODB_URI",
        "MONGODB_URI",
    ),
    "PROT_LEAFY_HOSPITAL_DATABASE_NAME": resolve(
        "PROT_LEAFY_HOSPITAL_DATABASE_NAME",
        "PROT_CLINICAL_TRIALS_DATABASE_NAME",
        "PROT_DATABASE_NAME",
        "LEAFY_HOSPITAL_DATABASE_NAME",
        "CLINICAL_TRIALS_DATABASE_NAME",
        "DATABASE_NAME",
    ),
    "PROT_VOYAGE_API_KEY": resolve("PROT_VOYAGE_API_KEY", "VOYAGE_API_KEY"),
    "PROT_CHATCOMPLETIONS_MODEL_ID": resolve("PROT_CHATCOMPLETIONS_MODEL_ID", "CHATCOMPLETIONS_MODEL_ID", "BEDROCK_DEFAULT_MODEL", "AGENTIC_BEDROCK_MODEL"),
    "PROT_EMBEDDINGS_MODEL_ID": resolve("PROT_EMBEDDINGS_MODEL_ID", "EMBEDDINGS_MODEL_ID", default="cohere.embed-english-v3"),
}

for key, value in resolved.items():
    print(f'{key}={shlex.quote(value)}')
PY
)"

switch_context() {
  local target="$1"
  local context=""
  case "$target" in
    staging) context="api.staging.corp.mongodb.com" ;;
    production|prod) context="api.prod.corp.mongodb.com" ;;
    *)
      echo "Unsupported target environment: $target" >&2
      exit 1
      ;;
  esac

  if ! kubectl config get-contexts -o name 2>/dev/null | grep -Fxq "$context"; then
    echo "Missing Kubernetes context: $context" >&2
    echo "Generate it first with kanopy-oidc kube setup and kanopy-oidc kube login." >&2
    exit 1
  fi

  kubectl config use-context "$context" >/dev/null
  kubectl config set-context --current --namespace="$NAMESPACE" >/dev/null
}

resolve_secret_group() {
  local target="$1"
  if [[ -n "$SECRET_GROUP" ]]; then
    printf '%s' "$SECRET_GROUP"
    return 0
  fi

  case "$target" in
    staging) printf '%s' "$STAGING_SECRET_GROUP" ;;
    production|prod) printf '%s' "$PROD_SECRET_GROUP" ;;
    *)
      echo "Unsupported target environment: $target" >&2
      exit 1
      ;;
  esac
}

resolve_payload() {
  local target="$1"
  case "$target" in
    staging)
      CLINICAL_TRIALS_MONGODB_URI_VALUE="${STAGING_CLINICAL_TRIALS_MONGODB_URI:-${CLINICAL_TRIALS_MONGODB_URI:-${MONGODB_URI:-}}}"
      CLINICAL_TRIALS_DATABASE_NAME_VALUE="${STAGING_CLINICAL_TRIALS_DATABASE_NAME:-${CLINICAL_TRIALS_DATABASE_NAME:-${DATABASE_NAME:-}}}"
      LEAFY_HOSPITAL_MONGODB_URI_VALUE="${STAGING_LEAFY_HOSPITAL_MONGODB_URI:-${LEAFY_HOSPITAL_MONGODB_URI:-${CLINICAL_TRIALS_MONGODB_URI:-${MONGODB_URI:-}}}}"
      LEAFY_HOSPITAL_DATABASE_NAME_VALUE="${STAGING_LEAFY_HOSPITAL_DATABASE_NAME:-${LEAFY_HOSPITAL_DATABASE_NAME:-${CLINICAL_TRIALS_DATABASE_NAME:-${DATABASE_NAME:-}}}}"
      MONGODB_URI_VALUE="${STAGING_MONGODB_URI:-${CLINICAL_TRIALS_MONGODB_URI_VALUE:-${MONGODB_URI:-}}}"
      DATABASE_NAME_VALUE="${STAGING_DATABASE_NAME:-${CLINICAL_TRIALS_DATABASE_NAME_VALUE:-${DATABASE_NAME:-}}}"
      VOYAGE_API_KEY_VALUE="${STAGING_VOYAGE_API_KEY:-${VOYAGE_API_KEY:-}}"
      CHATCOMPLETIONS_MODEL_ID_VALUE="${STAGING_CHATCOMPLETIONS_MODEL_ID:-${CHATCOMPLETIONS_MODEL_ID:-${BEDROCK_DEFAULT_MODEL:-${AGENTIC_BEDROCK_MODEL:-}}}}"
      EMBEDDINGS_MODEL_ID_VALUE="${STAGING_EMBEDDINGS_MODEL_ID:-${EMBEDDINGS_MODEL_ID:-cohere.embed-english-v3}}"
      ;;
    production|prod)
      CLINICAL_TRIALS_MONGODB_URI_VALUE="${PROT_CLINICAL_TRIALS_MONGODB_URI:-${CLINICAL_TRIALS_MONGODB_URI:-${MONGODB_URI:-}}}"
      CLINICAL_TRIALS_DATABASE_NAME_VALUE="${PROT_CLINICAL_TRIALS_DATABASE_NAME:-${CLINICAL_TRIALS_DATABASE_NAME:-${DATABASE_NAME:-}}}"
      LEAFY_HOSPITAL_MONGODB_URI_VALUE="${PROT_LEAFY_HOSPITAL_MONGODB_URI:-${LEAFY_HOSPITAL_MONGODB_URI:-${CLINICAL_TRIALS_MONGODB_URI:-${MONGODB_URI:-}}}}"
      LEAFY_HOSPITAL_DATABASE_NAME_VALUE="${PROT_LEAFY_HOSPITAL_DATABASE_NAME:-${LEAFY_HOSPITAL_DATABASE_NAME:-${CLINICAL_TRIALS_DATABASE_NAME:-${DATABASE_NAME:-}}}}"
      MONGODB_URI_VALUE="${PROT_MONGODB_URI:-${CLINICAL_TRIALS_MONGODB_URI_VALUE:-${MONGODB_URI:-}}}"
      DATABASE_NAME_VALUE="${PROT_DATABASE_NAME:-${CLINICAL_TRIALS_DATABASE_NAME_VALUE:-${DATABASE_NAME:-}}}"
      VOYAGE_API_KEY_VALUE="${PROT_VOYAGE_API_KEY:-${VOYAGE_API_KEY:-}}"
      CHATCOMPLETIONS_MODEL_ID_VALUE="${PROT_CHATCOMPLETIONS_MODEL_ID:-${CHATCOMPLETIONS_MODEL_ID:-${BEDROCK_DEFAULT_MODEL:-${AGENTIC_BEDROCK_MODEL:-}}}}"
      EMBEDDINGS_MODEL_ID_VALUE="${PROT_EMBEDDINGS_MODEL_ID:-${EMBEDDINGS_MODEL_ID:-cohere.embed-english-v3}}"
      ;;
    *)
      echo "Unsupported target environment: $target" >&2
      exit 1
      ;;
  esac
}

validate_payload() {
  local target="$1"
  local missing=()
  [[ -n "${MONGODB_URI_VALUE:-}" ]] || missing+=("${target}:MONGODB_URI")
  [[ -n "${DATABASE_NAME_VALUE:-}" ]] || missing+=("${target}:DATABASE_NAME")
  [[ -n "${CLINICAL_TRIALS_MONGODB_URI_VALUE:-}" ]] || missing+=("${target}:CLINICAL_TRIALS_MONGODB_URI")
  [[ -n "${CLINICAL_TRIALS_DATABASE_NAME_VALUE:-}" ]] || missing+=("${target}:CLINICAL_TRIALS_DATABASE_NAME")
  [[ -n "${LEAFY_HOSPITAL_MONGODB_URI_VALUE:-}" ]] || missing+=("${target}:LEAFY_HOSPITAL_MONGODB_URI")
  [[ -n "${LEAFY_HOSPITAL_DATABASE_NAME_VALUE:-}" ]] || missing+=("${target}:LEAFY_HOSPITAL_DATABASE_NAME")
  [[ -n "${VOYAGE_API_KEY_VALUE:-}" ]] || missing+=("${target}:VOYAGE_API_KEY")
  [[ -n "${CHATCOMPLETIONS_MODEL_ID_VALUE:-}" ]] || missing+=("${target}:CHATCOMPLETIONS_MODEL_ID")
  [[ -n "${EMBEDDINGS_MODEL_ID_VALUE:-}" ]] || missing+=("${target}:EMBEDDINGS_MODEL_ID")

  if [[ ${#missing[@]} -gt 0 ]]; then
    printf 'Missing required secret values: %s\n' "${missing[*]}" >&2
    exit 1
  fi
}

apply_secret_group() {
  local target="$1"
  local secret_group
  secret_group="$(resolve_secret_group "$target")"
  resolve_payload "$target"
  validate_payload "$target"

  if [[ "$DRY_RUN" == "true" ]]; then
    printf '[dry-run] target=%s namespace=%s secret-group=%s keys=%s\n' \
      "$target" "$NAMESPACE" "$secret_group" \
      "MONGODB_URI DATABASE_NAME CLINICAL_TRIALS_MONGODB_URI CLINICAL_TRIALS_DATABASE_NAME LEAFY_HOSPITAL_MONGODB_URI LEAFY_HOSPITAL_DATABASE_NAME VOYAGE_API_KEY CHATCOMPLETIONS_MODEL_ID EMBEDDINGS_MODEL_ID"
    return
  fi

  switch_context "$target"
  helm ksec set "$secret_group" \
    MONGODB_URI="$MONGODB_URI_VALUE" \
    DATABASE_NAME="$DATABASE_NAME_VALUE" \
    CLINICAL_TRIALS_MONGODB_URI="$CLINICAL_TRIALS_MONGODB_URI_VALUE" \
    CLINICAL_TRIALS_DATABASE_NAME="$CLINICAL_TRIALS_DATABASE_NAME_VALUE" \
    LEAFY_HOSPITAL_MONGODB_URI="$LEAFY_HOSPITAL_MONGODB_URI_VALUE" \
    LEAFY_HOSPITAL_DATABASE_NAME="$LEAFY_HOSPITAL_DATABASE_NAME_VALUE" \
    VOYAGE_API_KEY="$VOYAGE_API_KEY_VALUE" \
    CHATCOMPLETIONS_MODEL_ID="$CHATCOMPLETIONS_MODEL_ID_VALUE" \
    EMBEDDINGS_MODEL_ID="$EMBEDDINGS_MODEL_ID_VALUE"
  echo "Updated secret group '$secret_group' in $target"
}

for target in $TARGETS; do
  apply_secret_group "$target"
done
