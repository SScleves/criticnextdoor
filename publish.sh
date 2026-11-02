#!/usr/bin/env bash
# Rebuild the online edition (docs/) from films.db + static/index.html and push it to GitHub Pages.
#
#   ./publish.sh              # build, commit docs/ on the current branch, push, mirror docs/ to main, push
#   ./publish.sh --posters    # first run enrich_tmdb.py (needs TMDB_API_KEY in .env), then the above
#
# Pages serves main/docs. The UI source lives on the working branch (phase1), so this copies the
# built docs/ (and the build/sync scripts) onto main without merging anything else.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }
PY=.venv/bin/python
branch=$(git rev-parse --abbrev-ref HEAD)

if [ "${1:-}" = "--posters" ]; then
  [ -n "${TMDB_API_KEY:-}" ] || { echo "put TMDB_API_KEY=... in .env first (free key: https://www.themoviedb.org/settings/api)"; exit 1; }
  $PY enrich_tmdb.py
fi

$PY build_static.py
git add docs
if git diff --cached --quiet; then echo "docs/ unchanged, nothing to publish"; exit 0; fi
git commit -q -m "docs: rebuild ($(date +%F))"
git push -q origin "$branch"

if [ "$branch" != "main" ]; then
  git checkout -q main
  git checkout "$branch" -- docs build_static.py sync_state.py publish.sh .gitignore
  git commit -q -m "docs: rebuild from $branch ($(date +%F))" || true
  git push -q origin main
  git checkout -q "$branch"
fi
echo "published: https://sscleves.github.io/criticnextdoor/ (Pages takes a minute or two)"
