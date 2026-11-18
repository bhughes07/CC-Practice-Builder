#!/bin/sh
# Pull the studio branch and list briefs and notes from Duet waiting in studio/briefs/inbox/.
cd "$(dirname "$0")/../.." || exit 1
git pull --ff-only --quiet || echo "check_inbox: could not pull (offline, or local changes diverge)"
found=0
for f in studio/briefs/inbox/*.md; do
  [ -e "$f" ] || continue
  found=1
  printf '%s  %s  %s\n' "$(git log -1 --format='%ad %an' --date=short -- "$f")" "$f" "$(head -1 "$f")"
done
[ "$found" = 1 ] || echo "Inbox empty."
