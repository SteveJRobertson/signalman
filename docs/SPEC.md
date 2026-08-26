# Signalman — What We're Building

## What Signalman is

Every morning, Signalman reads the email that arrived overnight, works out what actually matters, and sends you a short summary on Signal. Three lists: things that need you **today**, things you need to **do** at some point, and a **digest** of everything else you can safely skim.

It runs entirely on your own machine. Your email is never sent to a company's servers — the AI that reads it runs locally. Nothing leaves the house.

## Where it stands today

The skeleton is built and it works end-to-end. It signs into Gmail, reads unread mail, asks the local AI to sort it, and delivers a briefing to your phone. On a light inbox — a handful of short, plain messages — it does exactly what it says.

It has not been hardened for a real inbox. The gap between "works in a demo" and "works on a Tuesday morning" is what this document covers. The honest summary:

- It reads only the **first 100** emails and doesn't mention the rest
- Newsletters and marketing mail arrive at the AI as **a subject line and nothing else**
- On a busy day it quietly hands the AI **more than it can read**, and the AI answers confidently anyway
- If anything breaks, **your phone gets nothing** — which looks exactly like a quiet inbox
- Until you clear your inbox, it sends you **the same briefing every morning, forever**
- The 8am schedule points at a folder that **was never created**, so on a fresh machine it wouldn't run at all

None of these are hard to fix. They're just all still open.

It's also worth saying what *is* solid, because it shapes everything below: the way the app is put together — separate parts for reading mail, sorting it, and sending the briefing — is genuinely well done, and none of the work here requires tearing that up. Every phase slots into the existing shape.

## How to read this

Each phase says what you get, why it comes at that point, and how you know it's finished. Build them in order — the ordering isn't arbitrary, and the reasoning is at the end.

---

## Phase 0 — Protect the keys to your inbox ✅

**Status: done.**

**What you get.** Signing into Gmail creates a file on your machine that acts as a permanent key to your email. Anyone holding it can read your inbox without your password and without triggering a login alert.

This project is published publicly on the internet. The setup instructions told you that file was protected from being published. It wasn't — the protection had never actually been switched on.

Nothing leaked. The file didn't exist yet and had never been published. But it would have been created the first time you ran the app for real, sitting unprotected in a public folder.

**Done when.** The key file, the credentials file, and stray system clutter are all excluded from publication, and the setup instructions describe what genuinely happens rather than what was assumed.

---

## Phase 0a — Get the foundations straight

**What you get.** The setup instructions describe a machine that doesn't quite exist. They name a version of Python you don't have installed, and a workspace folder that was never created. The 8am scheduled job points into that missing folder — so **on a fresh machine, the schedule wouldn't run at all**.

None of this has bitten yet because the app has only been run by hand. It would bite the moment you relied on the schedule.

**Why now.** Everything below is built on top of it, and instructions describing an imaginary setup cost time every single time someone follows them.

**Done when.** Following the setup guide start to finish on a clean machine produces a scheduled job that actually fires.

---

## Phase 0b — One place for settings

**What you get.** Settings — which AI model to use, where to find things, which phone number to message — currently live in several places at once, with copies that can quietly disagree. Some are also locked in the instant the app starts, so changing them appears to do nothing. Consolidate to a single place, read once.

**Why now.** This was originally scheduled last, on the grounds that nothing depends on it. That was the wrong way round. **Every phase below adds new settings.** Doing this last means adding settings the awkward way nine times over, then going back and redoing all nine. It's cheaper first, and it makes every phase after it easier to check.

**Done when.** Every setting has exactly one home, and changing one has the effect you'd expect.

---

## Phase 0c — Preview mode

**What you get.** Run Signalman and see the briefing on screen, without messaging your phone.

**Why now.** The instructions telling the AI how to sort your mail are where this tool's quality actually lives, and they are tuned by trial and error — try a wording, read the result, adjust. Without preview mode every experiment fires a real message at your phone. So you don't experiment. So the instructions never improve.

This was also originally scheduled last, with a note to pull it forward if you started tuning the AI. That note was too weak: **every phase from 4 onward involves the AI**, so it isn't conditional. It needs to exist before that work starts, not after.

**Done when.** You can iterate on the AI's instructions freely without your phone buzzing once.

---

## Phase 1 — Read the whole inbox

**What you get.** Signalman currently asks Gmail for your unread mail and gets the first hundred back, along with a note saying "there's more". It ignores the note. On a heavy day, everything past the hundredth email is invisible — not summarised, not flagged, not mentioned.

Fix it to keep asking until it has everything.

**Why now.** It's the front door. Every later phase operates on whatever gets through here, so a briefing built on a partial inbox is wrong no matter how good the rest is.

**Done when.** An inbox with several hundred unread messages produces a briefing covering all of them.

---

## Phase 2 — Understand every email

**What you get.** Email arrives in two flavours: plain text, and the formatted kind with images and buttons — most newsletters, receipts, and marketing. Signalman only knows how to read the plain kind. For everything else it extracts nothing, and the AI is asked to judge the email from its subject line alone.

This is why the digest can feel thin or oddly wrong. A large slice of your mail is being triaged effectively blind.

**Why now.** Together with Phase 1 this is the other half of "can it actually see your email?". Both must land before there's any point tuning what the AI does with it.

**Done when.** A formatted newsletter produces a summary that reflects what the newsletter actually said.

---

## Phase 3 — Strip the clutter

**What you get.** Emails carry a lot of dead weight: signatures, legal disclaimers, unsubscribe footers, and the entire quoted history of a reply chain. All of it currently goes to the AI exactly as-is.

Two costs. It's slower, because the AI reads far more than it needs. And it's less accurate, because a ten-message quoted thread buries the two new sentences at the top.

Cut the boilerplate before the AI sees it.

**Why now.** This was in the original plan for the project and never got built. It's also the cheapest way to make Phase 4 easier — less to trim later if there's less junk to begin with.

**Done when.** A long reply chain is summarised on its newest message, not its oldest.

---

## Phase 4 — Never overload the AI

**What you get.** This is the most consequential item in the document.

The AI can only hold so much text at once. Signalman currently packs every email into one giant request and sends it. Past the limit, the AI doesn't refuse or warn — it **silently ignores the overflow** and answers using the part it managed to read.

So on a busy morning you get a briefing that looks completely normal, is written with total confidence, and is based on a fraction of your inbox. There's no error, no warning, nothing visibly wrong. **The failure is invisible, and it hits hardest exactly when you most need the summary to be right.**

The fix is to work in batches sized to what the AI can genuinely hold, then combine the results.

**Why now.** It must come after 1–3, because those three change how much text there is. Sizing the batches before then means sizing them against the wrong number.

**Done when.** A very heavy inbox produces a briefing demonstrably covering mail from the end of the list as well as the start.

---

## Phase 5 — Insist on a usable answer

**What you get.** Signalman asks the AI to reply in a strict format and relies on it complying. Local AI models are inconsistent about this — they'll add a friendly preamble, or wrap the answer in extra formatting.

When that happens, Signalman can't read the reply and gives up. One chatty response and **the whole day's briefing is lost**.

Two changes: ask the AI in a way that constrains its answer structurally rather than politely, and when the answer is still unreadable, try again instead of abandoning the run.

**Why now.** Directly after batching, because batching multiplies the exposure — ten requests a morning is ten chances to get an unusable reply instead of one.

**Done when.** A deliberately awkward AI response is recovered from rather than ending the run.

---

## Phase 6 — Tell you when it breaks

**What you get.** Today every failure writes to a log file on your machine and stops. Your phone gets nothing.

You cannot tell "quiet inbox" from "broken for six weeks". Both look like silence. For something you're meant to *rely* on each morning, that's the most dangerous state it can be in.

Signalman should message you when it fails, saying plainly what went wrong. It should also cope with the ordinary case of running at 8am after a restart, when the supporting services it needs haven't finished starting — wait briefly and retry rather than declaring failure.

**Why now.** After 4 and 5, so it reports genuine surprises rather than nagging about faults already on this list.

**Done when.** Switching off a service it depends on produces a message on your phone explaining that, not silence.

---

## Phase 7 — Stop repeating itself

**What you get.** Signalman asks for unread mail from the last day. It has no memory of what it already told you. Leave something unread — as you would with anything you're keeping to deal with later — and it appears in tomorrow's briefing. And the next. Indefinitely.

Signalman will keep a private note of what it has already reported, and skip those items next time.

**Deliberate choice: it remembers locally rather than marking your email as read.** The alternative — having it tick things off in Gmail — would mean granting it permission to *change* your mailbox, where today it can only look. A local memory keeps that permission read-only, so a bug in Signalman can never alter or lose your email. Worth the slightly less tidy approach.

**Why now.** It only becomes worth solving once the briefing is accurate. Reliably remembering a wrong summary isn't an improvement.

**Done when.** Running twice in a row produces a full briefing, then an empty one.

---

## Phase 8 — Make the briefing actionable

**What you get.** Right now an item reads "Interview invite — confirm availability today". Useful, but you then go hunting through Gmail to find it.

Each item should carry who it's from and a direct link that opens that email.

**Why now.** It's a genuine quality-of-life gain, but it makes a correct briefing nicer rather than making a wrong one right. It waits until the briefing is trustworthy.

**Done when.** Tapping an item in Signal opens that email in Gmail.

---

## Phase 9 — Automatic checks and tidy-up

*Preview mode and the settings consolidation were originally part of this phase. Both proved to be things everything else depends on, so they moved to 0b and 0c.*

**What you get.**

- **Automatic checks** — the project's tests run by themselves whenever the code changes, rather than only when somebody remembers. A consistent style is enforced too; there's currently no such check at all.
- **Tidy-up** — two instruction files that say the same thing and will drift apart, a settings template to copy rather than transcribing by hand, a leftover instruction referring to a tool the project stopped using, and some early history filed under a former work email address.

**Why now.** Genuinely last: nothing depends on any of it. It's the work that makes the project pleasant to return to in six months rather than work that makes it function.

**Done when.** A change that breaks something is caught automatically, without anyone having to remember to look.

---

## Why this order

> **Make it safe → make it buildable → make it see → make it think reliably → make it speak up when it fails → make it more useful → make it pleasant to return to.**

The principle: **fix things in the order the data flows.** Improving how the AI sorts your mail is pointless while it's only seeing part of it (Phases 1–2), and sizing what you send it is pointless while you don't yet know how much there'll be (Phase 3 before 4). Each phase makes the following ones meaningful.

**The 0-phases are groundwork, and they moved.** Settings (0b) and preview mode (0c) were both originally scheduled last, because nothing depends on them in the way one feature depends on another. That reasoning was wrong in a specific way: they aren't depended on, but they're *used* by everything. Every later phase adds settings, and every phase from 4 onward involves tuning the AI. Scheduled last, each would be done the awkward way eight or nine times before being fixed. Scheduled first, they make everything after them cheaper. Groundwork should be judged on how often it's used, not on what depends on it.

Failure reporting (6) sits deliberately in the middle — early enough to catch surprises for the rest of the build, late enough not to spend its first weeks reporting faults already scheduled for repair.

Everything from Phase 7 onward is improvement rather than repair. If you stop after Phase 6, you have something dependable. Everything after that makes it better.

---

## Known, accepted, not scheduled

Worth knowing about; none blocking.

**The tests count higher than they measure.** There are 72 automated checks and they pass in under a second, which sounds excellent. But a large share verify trivia — that a setting handed to the app is the setting it stored. Meanwhile the riskiest part, signing into Google, has **no test at all**, and nothing tests a formatted newsletter (the Phase 2 gap). Three checks are written so that they pass whichever way the code behaves, meaning they can't fail and don't test anything. Treat the number as much weaker evidence than it looks. Worth strengthening alongside each phase rather than as a project of its own.

**Instructions for AI assistants are duplicated.** Two files carry the same guidance, differing only in their title. Whoever edits one will forget the other. Merge them when convenient.

**Some settings have two homes.** Default addresses for the AI and the messaging service are written in more than one place. They agree today. They'll disagree eventually. Phase 0b resolves this.

**A leftover instruction in the scheduling file** refers to a tool the project stopped using when Signal delivery was rewritten. Harmless, but misleading to the next person reading it.

**There's no example settings file.** New setups copy the settings out of the README by hand. A template to copy would be less error-prone.

**Emails are trusted as instructions.** Signalman feeds email text to the AI, so a deliberately crafted email could try to talk the AI into writing something misleading into your briefing. For a personal tool reading your own mail this is a reasonable risk to accept — worth remembering rather than acting on, and worth revisiting if Signalman ever summarises mail for anyone else.

**The repo history carries a work email address.** Four early commits are attributed to a former employer's address on what is a personal, public project. Worth rewriting the history before it matters.
