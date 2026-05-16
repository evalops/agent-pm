#!/usr/bin/env bash
set -euo pipefail

project_id="${BAZEL_RBE_PROJECT_ID:-evalops-dev}"
zone="${BAZEL_RBE_ZONE:-us-central1-a}"
vm_name="${BAZEL_RBE_VM_NAME:-bazel-rbe-dev-buildfarm}"
local_port="${BAZEL_RBE_LOCAL_PORT:-8980}"
remote_port="${BAZEL_RBE_REMOTE_PORT:-8980}"
use_existing_tunnel="${BAZEL_RBE_USE_EXISTING_TUNNEL:-false}"
tunnel_through_iap="${BAZEL_RBE_TUNNEL_THROUGH_IAP:-true}"

if [[ "${1:-}" == "--" ]]; then
  shift
fi

if (($# == 0)); then
  set -- make bazel-test-remote
fi

tunnel_pid=""
tunnel_listener_pids=""

cleanup() {
  if [[ -n "$tunnel_pid" ]] && kill -0 "$tunnel_pid" >/dev/null 2>&1; then
    kill "$tunnel_pid" >/dev/null 2>&1 || true
    wait "$tunnel_pid" >/dev/null 2>&1 || true
  fi

  if [[ -n "$tunnel_listener_pids" ]]; then
    while IFS= read -r listener_pid; do
      [[ -z "$listener_pid" ]] && continue
      if kill -0 "$listener_pid" >/dev/null 2>&1; then
        kill "$listener_pid" >/dev/null 2>&1 || true
        wait "$listener_pid" >/dev/null 2>&1 || true
      fi
    done <<<"$tunnel_listener_pids"
  fi
}

port_is_open() {
  (echo >/dev/tcp/127.0.0.1/"$local_port") >/dev/null 2>&1
}

capture_tunnel_listener() {
  if command -v lsof >/dev/null 2>&1; then
    tunnel_listener_pids="$(lsof -nP -tiTCP:"$local_port" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    tunnel_listener_pids="$(ss -H -ltnp "sport = :${local_port}" 2>/dev/null | sed -nE 's/.*pid=([0-9]+).*/\1/p' | sort -u)"
  else
    echo "lsof or ss is required to validate the Bazel RBE tunnel listener process" >&2
    return 1
  fi

  [[ -n "$tunnel_listener_pids" ]]
}

wait_for_tunnel() {
  local attempts="${BAZEL_RBE_TUNNEL_ATTEMPTS:-30}"
  local sleep_seconds="${BAZEL_RBE_TUNNEL_SLEEP_SECONDS:-2}"

  for _ in $(seq 1 "$attempts"); do
    if port_is_open; then
      if [[ -n "$tunnel_pid" ]] && ! capture_tunnel_listener; then
        echo "127.0.0.1:${local_port} accepted connections, but no tunnel listener process was found" >&2
        return 1
      fi
      return 0
    fi

    sleep "$sleep_seconds"
  done

  echo "Timed out waiting for Bazel RBE tunnel on 127.0.0.1:${local_port}" >&2
  if [[ -n "$tunnel_pid" ]] && ! kill -0 "$tunnel_pid" >/dev/null 2>&1; then
    wait "$tunnel_pid" || true
  fi
  return 1
}

if [[ "$use_existing_tunnel" != "true" ]]; then
  if port_is_open; then
    echo "Local port 127.0.0.1:${local_port} is already open; refusing to start a Bazel RBE tunnel over it." >&2
    echo "Set BAZEL_RBE_USE_EXISTING_TUNNEL=true to reuse an existing tunnel intentionally." >&2
    exit 1
  fi

  gcloud_args=(
    compute ssh "$vm_name"
    "--project=${project_id}"
    "--zone=${zone}"
    --quiet
    "--ssh-flag=-o ExitOnForwardFailure=yes"
    "--ssh-flag=-o ServerAliveInterval=30"
    "--ssh-flag=-o ServerAliveCountMax=3"
  )

  if [[ "$tunnel_through_iap" == "true" ]]; then
    gcloud_args+=(--tunnel-through-iap)
  fi

  gcloud "${gcloud_args[@]}" -- -N -L "${local_port}:localhost:${remote_port}" &
  tunnel_pid="$!"
  trap cleanup EXIT INT TERM
fi

wait_for_tunnel
"$@"
