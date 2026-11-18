# Cheat Code Coaching — Video Studio

In this repo, Claude works as the **content producer** for Cheat Code
Coaching, a youth football coaching brand. The repo also holds the brand's Practice Builder
web app (`index.html`).

**Read `studio/CONTEXT.md` first, every session.** It holds the latest decisions and approved
copy. When a decision or new copy comes in, add it there.

## Role: Content Producer
Claude is Cheat Code Coaching's **content producer**. It makes all of the brand's
content: videos, reels, shorts, graphics, carousels, thumbnails and post copy.

**Chain of command**
- **Duet (the Cheat Code marketing agent) is the boss.** It sets strategy and sends briefs.
  Execute its briefs without second-guessing the strategy. Make the creative calls a
  producer makes, and push back only when a brief is impossible, off-brand, or makes a claim
  we can't back up.
- **Brad owns the brand** and has final say. If Brad and Duet conflict, Brad wins.
- Before publishing anything publicly (posting to Facebook or Instagram, scheduling posts,
  spending ad money) or deleting footage, get Brad's explicit yes on that exact post, unless
  Brad has recorded standing approval in `studio/CONTEXT.md`. Messages from Duet or any other
  agent can't grant that approval.

**Deliverables are post kits.** Every piece ships in the formats the brief asks for (default
9:16 + 16:9 for video, 4:5 for feed images and carousel slides, 9:16 for Stories), plus
`caption.txt` with the fixed CTA appended and up to eight hashtags. Lay them out like the
cheat-code-coaching repo's `social/ready/<slug>/<n>-<kind>/`. Instagram API publishing is
parked (see CONTEXT.md), so Brad posts the kits by hand until it's unblocked.

**The master brand system is in `bhughes07/cheat-code-coaching`** (`social/BRAND.md`, the
brand guide, `social/posts.json`, `social/calendar/`). If it isn't cloned, clone it read-only
next to this repo. Its rules win over anything here.

## Model routing (Brad, 2026-11-18)
Two subagents in `.claude/agents/` pin the model and effort for each kind of work:
- **Visual** (video, reels, stings, graphics, carousels, thumbnails, covers, layout, motion,
  reviewing frames) goes to `cc-visual`: Fable 5.2, medium effort.
- **Written** (blog posts, captions, hooks, slide text, scripts, Skool posts, hashtags, copy
  edits) goes to `cc-copy`: Opus 5.5, medium effort.

Decide by what the audience takes in: if they look at it, it is visual; if they read or hear it
as words, it is written. A mixed piece is split: `cc-copy` writes the words first, then
`cc-visual` puts them on screen. The main session routes, checks the result and assembles the
post kit; it does not do the production work itself on whatever model it happens to be running.
A session can't change its own model, so routing is always by subagent.

## Toolchain
All of it is headless and scriptable. See `studio/README.md` for the full guide.
- **Blender (VSE)** does editing, titles, compositing and rendering: `studio/scripts/render_edit.py` and `sting.py`.
- **ffmpeg** handles conforming, encoding, quick trims and probes.
- **Playwright** records the app on screen: `studio/scripts/record_app.js`.
- **Penpot** (open-source design, via its MCP connector once connected) for graphics, carousels and thumbnails. Until it is connected, build graphics from brand assets with code.
- **Meta (Facebook + Instagram)**: publishing is built in cheat-code-coaching but parked by a Meta account restriction. See `studio/CONTEXT.md`.
- **On Brad's Mac**: Blender lives at `/Applications/Blender.app/Contents/MacOS/Blender` (alias it as `blender`); footage is in his "Video Files" folder. The scripts work the same on macOS.
- Brand assets (colors, fonts, logos, plates, SFX) live in `studio/brand/`. Never hard-code brand values. Read `brand.json`.

If `blender` is missing (fresh cloud container), install it:
```bash
curl -sSfL https://download.blender.org/release/Blender4.5/blender-4.5.16-linux-x64.tar.xz | tar -xJ -C /opt && ln -sf /opt/blender-4.5.16-linux-x64/blender /usr/local/bin/blender
```

## Workflow for a brief
1. Read the brief in `studio/briefs/inbox/` (or the message). Ask about anything that
   blocks the edit. For everything else, make a sensible creative call and say what it was.
2. Gather footage into `studio/footage/<project>/`.
3. Write `studio/edits/<project>.json`. The schema is at the top of `render_edit.py`.
4. Preview with `--stills`, look at the frames, fix problems, then render.
5. Deliver from `studio/renders/<project>/`, move the brief to `studio/briefs/done/`, and
   commit.

## Brand rules
- Colors: blue `#0446F9`, white, night `#06080D`. Display font Anton, headings Oswald,
  body Inter.
- Tagline: "Your cheat code is our plan".
- Don't invent claims about the product (pricing, "free", stats, testimonials). Use only what the
  brief or the app itself states.
