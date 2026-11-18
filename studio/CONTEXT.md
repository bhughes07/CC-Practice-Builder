# Cheat Code Coaching: Source of Truth

Claude reads this file before every video or post. Newest decisions go at the top of the log.

## The master brand system lives in `bhughes07/cheat-code-coaching`
Those files win over anything here. Read them before producing:
- `social/BRAND.md`: fixed CTA, weekly rhythm, sport roster, **hard rules**
- `Cheat Code Coaching Brand Guide.md`: colors, type, logo use
- `social/posts.json`: post library (captions never contain the CTA, it's appended at render)
- `social/questions.json`: question bank. Every post answers a real coach or league question.
- `social/calendar/<YYYY-Www>.md`: the week's plan (The Question carousel Tue, Reel Thu, quiet post Sat, Stories)
- `docs/engineering-log.md`: what's built and what's parked

## Fixed call to action (verbatim, every post)
- **Caption closer:** Nobody gets skipped. Sign up as a coach at cheatcodecoaching.app, then download Cheat Code Teams on the App Store.
- **End card / last slide:** `NOBODY GETS SKIPPED` (Anton) / `Sign up as a coach · cheatcodecoaching.app · Cheat Code Teams on the App Store` (Oswald)
- **Stories:** link sticker `Sign up as a coach`, text `Nobody gets skipped.`
- Product is **Cheat Code Teams** (app) and Practice Builder is a feature inside it. Multi-sport, not football-only.

## Hard rules (summary, full list in BRAND.md)
- No real child connected to Cheat Code: no face, name, number, stats or record. People on screen are licensed stock models.
- Brad's own face stays off camera for now (hands and voice are fine).
- No fake proof (testimonials, download counts, claimed results). Any stat needs a named source.
- No prices, "free", or trial terms. No competitor comparisons. Never show something that isn't built.
- App screens only from the demo org (fictional teams), and only when the screen is the point.
- Every slide or line is something Brad would say to a coach in a parking lot.
- Trending audio can't be attached via API, so Reels that need it are posted from the phone.

## Decisions log
- 2026-11-18: Visual work runs on Fable 5.2 medium (`cc-visual`), written work on Opus 5.5 medium (`cc-copy`). Rule in CLAUDE.md. (Brad)
- 2026-11-18: Studio scripts run on Brad's Mac with Blender 5.3 (`cc_blender.py` handles both the 4.5 and 5.x APIs). (Claude)
- 2026-11-18: Claude (this repo) is the **content producer**. Duet is the strategist/boss. Brad has final say and approves every public post. (Brad)
- 2026-11-18: Promo end card switched to the fixed CTA, and "football coaches" changed to youth coaches generally. (Claude, per BRAND.md)
- 2026-10-13: Instagram API publishing **parked**: Meta won't let Brad's account link the FB Page and IG. Posting is manual via post kits (`scripts/social/post-kit.js` in cheat-code-coaching → `social/ready/<slug>/`). (engineering log)
- 2026-10-07: "The Question" weekly carousel replaced the "Tuesday Practice" card. (Brad)

## In flight
| Item | Owner | Status |
|---|---|---|
| Practice Builder promo (16:9 + 9:16) | Claude | v2 with fixed CTA rendering. Needs Brad's eye: the footage is the standalone Practice Builder page, which may not match the current in-app screen |

## Integrations
| Tool | Status | Notes |
|---|---|---|
| Blender + ffmpeg (cloud) | working | reinstall line in CLAUDE.md |
| Penpot | account + connector added 2026-11-18 | loads in **new** sessions only |
| Meta (FB Page "Cheat Code Coaching" + IG) | publishing code built in cheat-code-coaching (`bobcat-site/functions/api/ig/`), **parked** | Meta account-link restriction. Until it lifts, Claude delivers post kits and Brad posts by hand. Credentials live on Brad's Mac (`~/.config/cheatcode/`) and in Cloudflare, never here |
| Brad's Mac (Video Files) | working (local session, 2026-11-18) | Blender 5.3 at `/Applications/Blender.app`; ffmpeg via Homebrew. Repo cloned to `~/Video Files/CC-Practice-Builder`, cheat-code-coaching read-only next to it. Scripts patched for the Blender 5 VSE/animation API (still 4.5-compatible). Don't commit `.blend` files saved by 5.x: 4.5 in the cloud can't open them |
