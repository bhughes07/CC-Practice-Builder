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
- 2026-11-18: **Posting route: Meta's own web interfaces, driven in Brad's Chrome** (Claude in Chrome). Facebook Page via Meta Business Suite (business.facebook.com, Page "Cheat Code Coaching", signed in); Instagram via instagram.com (signed in as `cheatcodeteams`, Create → Post takes video as a Reel). No API, no third-party tool, free, native upload. Every post still needs Brad's yes on that exact post in chat; then Claude uploads, pastes caption.txt, and publishes or schedules (Business Suite Planner schedules natively). Never Brad's personal Facebook. (Brad asked, Claude verified sign-ins)
- 2026-11-18: Handoff from the marketing work: this producer is the video agent from here on. Video style: Brad's own spoken voice and stories, no corporate speak, dry self-deprecating humor, about 60s vertical for Instagram and the Cheat Code Facebook Page. Nothing goes on Brad's personal Facebook. Free tools only. (Brad, via handoff)
- 2026-11-18: A 3D logo intro (`intro3d.blend`, built once, reused on every video) and a 12.5s 9:16 sample exist from the cloud work. Both are attachments in Brad's cheat-code-marketing channel, **not in this repo yet**. (handoff)
- 2026-11-18: Duet and the producer talk through `studio/briefs/` in git (`inbox/` in, `outbox/` back). Protocol in `studio/briefs/README.md`. (Brad asked, Claude built)
- 2026-11-18: Visual work runs on Fable 5.2 medium (`cc-visual`), written work on Opus 5.5 medium (`cc-copy`). Rule in CLAUDE.md. (Brad)
- 2026-11-18: Studio scripts run on Brad's Mac with Blender 5.3 (`cc_blender.py` handles both the 4.5 and 5.x APIs). (Claude)
- 2026-11-18: Claude (this repo) is the **content producer**. Duet is the strategist/boss. Brad has final say and approves every public post. (Brad)
- 2026-11-18: Promo end card switched to the fixed CTA, and "football coaches" changed to youth coaches generally. (Claude, per BRAND.md)
- 2026-10-13: Instagram API publishing **parked**: Meta won't let Brad's account link the FB Page and IG. Posting is manual via post kits (`scripts/social/post-kit.js` in cheat-code-coaching → `social/ready/<slug>/`). (engineering log)
- 2026-10-07: "The Question" weekly carousel replaced the "Tuesday Practice" card. (Brad)

## In flight
| Item | Owner | Status |
|---|---|---|
| Launch set, 20 image posts (`ready/launch/`) | Claude | Reworked by Fable 2026-11-18. Brad approved the first nine (order in `ready/launch/README.md`) for IG + FB Page, then paused before anything posted. **Nothing posted yet.** Re-confirm with Brad before posting; the other 11 go out spread over the following days |
| Practice Builder promo (16:9 + 9:16) | Claude | v2 with fixed CTA rendering. Needs Brad's eye: the footage is the standalone Practice Builder page, which may not match the current in-app screen |

## Integrations
| Tool | Status | Notes |
|---|---|---|
| Blender + ffmpeg (cloud) | working | reinstall line in CLAUDE.md |
| Duet briefs | channel built 2026-11-18, **untested**: no brief from Duet has arrived yet | Duet needs push access to this repo and must be pointed at `studio/briefs/README.md` |
| MCP for Blender add-on | not installed, Brad to decide | Lets Claude drive an open Blender window. Not needed for renders: the headless scripts already work. Needs uv plus a third-party add-on on an IT-managed Mac |
| Penpot | account + connector added 2026-11-18 | loads in **new** sessions only |
| Posting via browser (FB Business Suite + instagram.com) | **ready**, verified 2026-11-18 | Browser upload tool caps at 10 MB per call: kits over that need a smaller upload encode or Brad drops the file into the picker. Story link stickers and trending audio stay phone-only. Linking IG to the Page in Business Suite (Brad must do it: needs his IG login) would put both accounts and scheduling in one place |
| Meta (FB Page "Cheat Code Coaching" + IG) API | publishing code built in cheat-code-coaching (`bobcat-site/functions/api/ig/`), **parked** | Meta account-link restriction. Until it lifts, Claude delivers post kits and Brad posts by hand. Credentials live on Brad's Mac (`~/.config/cheatcode/`) and in Cloudflare, never here |
| Brad's Mac (Video Files) | working (local session, 2026-11-18) | Blender 5.3 at `/Applications/Blender.app`; ffmpeg via Homebrew. Repo cloned to `~/Video Files/CC-Practice-Builder`, cheat-code-coaching read-only next to it. Scripts patched for the Blender 5 VSE/animation API (still 4.5-compatible). Don't commit `.blend` files saved by 5.x: 4.5 in the cloud can't open them |
