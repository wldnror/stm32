#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export GIT_TERMINAL_PROMPT=0

# Never stash or overwrite local work, or hide a failed fetch.
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "로컬 수정이 있어 업데이트를 중단합니다." >&2
    exit 1
fi
git -c http.connectTimeout=10 -c http.lowSpeedLimit=100 -c http.lowSpeedTime=15 fetch origin
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}')
if [ "$(git rev-parse HEAD)" = "$(git rev-parse "$upstream")" ]; then
    echo "이미 최신 상태입니다."
    exit 0
fi
git merge --ff-only "$upstream"
