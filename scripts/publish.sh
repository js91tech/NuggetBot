#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-js91tech/palworld-companion}"
BRANCH="${2:-main}"

echo "Publishing Palworld Companion to https://github.com/${REPO}"

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  echo "Repository ${REPO} not found. Creating..."
  gh repo create "$REPO" --public --description "Palworld companion — search resources and learn how & where to get them" --source=. --remote=origin --push
  exit 0
fi

git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/${REPO}.git"
git push -u origin "${BRANCH}"

echo "Done! Connect the repo in Vercel to deploy: https://vercel.com/new"
