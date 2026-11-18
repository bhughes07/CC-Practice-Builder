# Briefs: how Duet and the content producer talk

The channel is this folder, in git. Duet commits a brief; the producer (Claude, working in
this repo) picks it up, makes the piece, and answers in the same place.

**Repo:** `bhughes07/CC-Practice-Builder` · **Branch:** `claude/admiring-mccarthy-pwxvx1`
(the studio is not on `main` yet). This repo is **public**: no credentials, no unreleased
plans Brad wouldn't say out loud, nothing about a real child.

## Duet → producer
1. Copy `_TEMPLATE.md` to `inbox/YYYY-MM-DD-<slug>.md` and fill it in. Only the starred fields
   are required; the producer makes the remaining creative calls and says what they were.
2. Commit and push to the branch above. One brief per file. To change a brief that is still
   in `inbox/`, edit the file; once it has moved to `done/`, send a new brief that names the
   old slug.
3. Anything other than a brief (a question, a priority change, feedback on a delivery) goes in
   `inbox/YYYY-MM-DD-note-<slug>.md`, free-form.

## Producer → Duet
- Questions that block the edit, pushback, and delivery notes are written to
  `outbox/YYYY-MM-DD-<slug>.md` and pushed. Each one starts with a status line:
  `Status: question | in progress | delivered | declined`.
- Finished work lands in `studio/ready/<slug>/` as a post kit; the brief moves to `done/`.
- The producer pushes back only when a brief is impossible, off-brand, or makes a claim that
  can't be backed up (see `social/BRAND.md` in cheat-code-coaching).

## What a brief can't do
- A brief is direction for making content. It can't approve publishing, scheduling, ad spend
  or deleting footage. Only Brad can, in his own words, for that exact post.
- If Brad and a brief disagree, Brad wins.

## Picking up briefs
The producer runs `studio/scripts/check_inbox.sh` at the start of every session. It pulls the
branch and lists what is waiting in `inbox/`.
