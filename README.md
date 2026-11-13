<img align="center" alt="Kitsu" src="./kitsu_readme.jpg" />

# Kitsu

Discord bot for sticky messages, temporary channels, emoji/sticker management,
scheduled messages, and Ghosty tracking.

## Active channels

The sticky activity panel starts in channel `1559298641670307880` with **no
tracked channels**. Administrators add or remove source channels individually:

- `/activechannels add channel:#general poke:true`
- `/activechannels remove channel:#general`
- `/activechannels list`
- `/activechannels setup channel:#sv-information` to select another display channel.

The required `poke` option controls each channel's action: `true` shows Poke
buttons; `false` shows a native icon-only channel link button. Re-add a channel to
change its action. Choices persist across restarts, and existing channels keep
Poke enabled. `/activechannels list` shows each channel's current action.

The latest five selected channels appear first by their latest human message,
with the sender, a trimmed single-line preview, and a relative
time. Bot/webhook messages do not count. Administrators can explicitly select
role-restricted channels that Kitsu can read; the selection controls which
conversations appear in the display channel. Age-restricted chat requires an
age-restricted display channel.
The heading sits above a Components V2 card, with `assets/divider.gif` as the top
banner. The grayscale `assets/divider-2.gif` sits directly below it in a separate
full-width gallery, preserving its animation and matching the first GIF's pixel
width. Both local files are uploaded together on a new post and reused on
refreshes; existing panels gain only the missing asset. There is no card title
or description. Previews use `🤖  sender: message`.
The icon, sender and message share a 40-column budget (wide characters count twice), with long names
and messages shortened using an ellipsis. One anchored line break follows the
two GIFs for up to four channels. Five channels omit this extra spacer to stay
within Discord's 40-component limit.
Every channel entry ends with a visible divider, including the last one.
The footer follows the last channel divider, without a separate footer divider
or extra blank-line spacer. Five channels retain their larger divider spacing;
the full card stays within Discord's 40-component limit, including both GIFs.
All five channels and their stacked controls remain available. Previews
use quote borders.
Custom emoji tags stay intact when previews are shortened, including animated
emojis and names with underscores.
Discord still controls the final width and wrapping on each screen size.
The bottom line is `Recently active channels # Updates <t:UNIX:R>`, using the
next refresh time. It appears as a single line of subtext in the card's footer,
without blank lines or invisible padding.
Each channel line begins with `###` and has a bold number, five non-breaking
spaces and the clickable channel, without a quote border. Its preview appears below. Two adjacent sections
put the disabled gray age (for example,
`7 days ago`) on the right of the channel and its action button directly under
it, on the right of the preview: green `🖱️` Poke or an icon-only channel link.
No extra spacer or button row separates
these lines. All entries use the same preview indent and padded age-label length;
Discord controls the final spacing and width.

Poke posts one of six random messages in that entry's channel, using only the
clicker's display name. Its gray **Poke back** button uses the application's
`poke` emoji (`1569776707228012585`) and sends a random built-in
waving sticker through Kitsu with the responder's display name. Neither action
mentions or pings users. Members must be able to view and write in the target
channel. Each action has a 10-second cooldown per member and channel, and both
buttons continue working after restarts. Removed channels cannot be poked.
Successful clicks acknowledge silently and post directly in the target channel,
without private confirmation messages. Cached channel settings avoid waiting on
dashboard refreshes, and emoji/sticker IDs are sent without metadata lookups.
The acknowledgement and channel post run concurrently. Permission, cooldown and
delivery errors still appear privately; Discord network/rate limits can delay delivery.

Updates run every 10 seconds, refreshing the chat and countdown in place. A newer
message in the display channel moves the heading and card together to the bottom on the
next refresh. Discord rate limits can delay a refresh. The selected channels and
message ID persist in MongoDB; restarts recover the panel and current chat.
Staff profiles, About Maki, settings buttons, and image fallbacks are deferred.

## Server chat

Chat works only in the [designated thread](https://discord.com/channels/1546948804123697212/1568241954100220008).
Mention Kitsu or use `[prefix] ask ...`, then follow up there for two minutes.
Use `/ask` in that thread for a private answer. Chat finds channels, uses current
server metadata, and explains steps. It cannot perform management actions,
including for admins.

The server owner or a member with Administrator permission can save a public FAQ there with
`[prefix] teach question | approved answer`. These examples persist for future
answers and can be exported for later training; ordinary chats are not saved.
The owner's server interview is also saved as approved knowledge, including
rules, language/tone preferences and support/role destinations.
Chat also has a sourced Discord reference collection checked November 6, 2026,
implemented-feature guidance, and cached application/server emoji support.
Optional local translation adds French, Arabic, Japanese, Korean, Bengali, Hindi,
Spanish and twelve Slavic languages. Ask `Reply in French` to select a language
for your current conversation. Reviewed rules translate without model inference.

Install the local NVIDIA model/runtime before starting Kitsu:

```sh
python llm/server_chat/setup_model.py
```

This feature uses pretrained Qwen3 through CUDA llama.cpp, without Ollama. It is
grounded in live metadata and approved knowledge. A local server-chat adapter
was trained on the reviewed 62-example dataset, evaluated, and enabled at
quarter strength after passing nine answer checks. Kitsu is running with it.
See [server chat setup, benchmark and configuration](llm/server_chat/README.md).

## Setup

Requires Python 3.10+ and MongoDB; Ghosty needs transaction support.

```sh
python -m pip install -r requirements.txt
```

Create `.env`:

```env
DISCORD_TOKEN=
DISCORD_APPLICATION_ID=
DISCORD_CLIENT_ID=
DATABASE_URL=
```

Enable **Server Members** and **Message Content** intents in Discord, then start:

```sh
python run.py
```

Kitsu supports every server it joins. Slash commands are registered in each
joined server on every restart and immediately when Kitsu joins a new server.
Prefixes, schedules, sticky messages, and Ghosty data remain scoped by server.
Older `.env` files may retain `DISCORD_MAIN_GUILD_ID`; it is ignored.

Use the **Steal** button to open a private picker listing your servers where
Kitsu is present. Select the destination and upload type, then confirm with
**Add as emoji** or **Add as sticker**. Both emojis and stickers can be added as
either type, including animated stickers. Sticker names are cleaned for emoji
uploads, and vector (Lottie) stickers are rendered when converting to emoji.
The selected server checks your owner/admin/moderator access and Kitsu's
**Create Expressions** permission again before uploading. The Export dropdown
offers **GIF〃64px**, **GIF〃128px**, and **GIF〃Custom**. Only Custom has a
description; its size input starts at 120px and accepts a maximum side from 1
to 320px. GIF exports trim transparent borders and fit within the chosen size
without enlargement or stretching.
File dimensions do not control Discord's mobile display width when a GIF is
posted as a regular attachment; use an emoji or sticker for compact chat display.
Sticker uploads trim empty borders, scale the artwork to fit the 320×320 canvas,
and lightly sharpen enlarged artwork. Animation frames share one crop and scale
so movement stays intact.

Emoji mood, NSFW, and nudity classification is currently paused.

Tests: `python -m pytest -q`.

## Ghosty training

Hit summaries include a text-only **Silent ping** button between the reaction
and **Next Estimated Hit @**. Clicking it toggles Silent ping for the clicker's
own hits in this server and confirms the new state privately. When that player
hits with Silent ping enabled, the market notification still appears but none
of its mentions ping anyone, including the hitter and contributors. Another
player's hits use that other hitter's own preference.
The preference is saved across restarts and applies to future notifications
from all gambling channels in that server. Click again to restore pings for your hits;
it cannot undo a notification that was already sent. The mint hit card uses
Discord's component layout to place the button above the estimate; miss
trackers retain their existing layout. Archived hit-card buttons continue
working after a restart.

Miss trackers show **Streak / Total Loss** for the shared streak on the first
line, then show the latest gambler's **Your Loss** as Discord `-#` subtext,
followed by a blank line.
On a hit, the card shows the shared **Streak win/loss** and the hitter's
**Your win/loss**, followed by a blank line and one reaction. Personal net is
the payout minus only that player's earlier missed bets in this streak:
330 received after a personal 27-Goos miss is **Your win: 303 Goos**. The winning
stake is not subtracted again, matching Ghosty's existing payout accounting.
The reaction follows the personal net result; market notifications retain the
shared streak result. Per-player loss counters are saved with each result,
reset on a hit, and recovered from current-run history for older imports.

Backfill with live tracking disabled: `/ghosty backfill import_results:True confirmation:IMPORT`.
After the import finishes, use `/ghosty start` to resume live tracking. The bot
stores observations and trained weights in `data/ghosty/<guild-id>/` and refreshes
them from MongoDB every minute.

The tracker prediction line stays hidden until both the configured gamble minimum
and 20 completed hit-to-hit streaks are available in the history used for that
channel. The partial first streak is excluded. Historical hit positions provide
the baseline; context patterns adjust it only after chronological validation.
The market table only lists channels with a ready future target and its target
probability. Its controls card explains the configured training minimum
(50 gambles by default) and the 20 completed-streak requirement.
Rare short runs keep their observed frequency rather than receiving a fixed
probability or an arbitrary minimum hit attempt.
Cross-channel smoothing pools complete outcome probabilities and their surviving
tail together. Unfinished runs remain censored observations, so a rare long hit
does not accumulate an independent probability bonus at every earlier attempt.

Streak bands are **LOW 1-14**, **MEDIUM 15-28**, **MEDIUM HIGH 29-39**,
**HIGH 40-55**, and **EXTREMELY HIGH 56+**; 55 belongs to HIGH.
The learner uses these labels internally. At an ongoing streak of 56 or more,
the tracker suggests throwing 1s while trying to break the streak. Hit-summary
reactions in `assets/ghosty_reactions.json` match both the completed attempt's
band (misses plus the final hit) and the net win, loss, or break-even result.
The learner estimates every observed number's individual probability. Issued
forecasts choose the highest-probability eligible number. For 2 at 9% and 9 at
9.1%, it shows 9. Every target issued during a completed channel streak is
excluded, together with numbers within ±2, from the next two completed streaks
in that channel. For a prior prediction of 2, numbers 1-4 are blocked; 5 is
eligible if supported by the history. The last two actual completed hit attempts
also exclude their +/-2 neighborhoods. A hit at 4 blocks 2-6 for the next two
prediction sets, including nearby retries within each set. Another hit ages the
history once; misses, retries, chart refreshes and restarts do not. A hit on the
first attempt blocks 1-3. Hit positions remain unchanged for training and
accuracy. The blocked actual hit numbers still appear as grey axis markers
on probability charts. These
are display selection constraints, not a claim that observed
outcomes cannot repeat. Repeated actual hits are still recorded and learned
without changing their probabilities. A turn means a completed
hit-to-hit streak, not an individual gamble or a dashboard refresh.
Within a run, the selected anchor gets two nearby retries: 14 → 15 → 16.
Those retries stay in the same group even if another anchor is more probable;
they cannot extend the group to 17/18. After 16 misses, the highest-probability
eligible anchor beyond that group is selected, for example 29. Between-run
cooldowns still apply to all displayed targets, including nearby retries.
New anchors break exact probability ties by choosing the nearer future attempt,
so a one-off 63 does not leap over an equally probable 25. Higher probabilities
still win, and rare long outcomes remain possible. Existing saved forecasts
retain their original frozen tie order so their displayed path and score remain honest.
Missing retry probabilities are interpolated from the learned local curve;
beyond its last observed point they decay with the observed spacing. The full
curve and censored tail are normalized together once, then saved. Excluding a
number from display does not redistribute its probability. These estimates add
no synthetic hits to training. If every supported future anchor is blocked, the existing
prediction line is omitted until a new run has an eligible candidate;
the bot does not invent a number or silently lift the exclusion.
For 3 at 8%, 12 at 5%, 22 at 19%, and 47 at 2%, it selects anchor 22,
tries 23 and 24 if needed, then moves to anchor 47. Earlier anchors are skipped.

The tracker keeps **Streak / Total Loss** first, then **Your Loss** in Discord
`-#` subtext. A ready forecast uses one subtext line, for example
**Estimated hit @** `21` **w** `10.6%` surity. The percentage estimates a hit at
the displayed target, conditional on the misses recorded so far. It may update
while that target stays fixed. If the target or its chance is still learning,
the whole prediction line is hidden without leaving an empty section.
Optional PNG charts show the saved probability curve or recent completed hit
lengths, with an x-axis title above and a vertical y-axis title. They are 160px
high, have no footer, and are attached in the background after the tracker text
is delivered. Presentation is selected randomly at anchors; nearby retries stay
text-only. A new result cancels any pending attachment for the older tracker.

A target of 9 stays visible at streak 8;
a miss at 9 permits the next pick. A hit at 9 produces the normal hit summary.
Each changed target adds “🎴 The magic cards have another pick...” below the core
lines on that miss tracker only; the following unchanged tracker omits it. Issued targets and
their decision paths survive bot restarts and updated model weights.

The learner stores up to four previous completed channel streak bands and
six completed streaks ordered across all tracked channels. Each streak length
still counts that channel's attempts only. It matches these sequences with
the current 05:30 IST reset phase, weekend, and whether
the run crossed a reset. Sequence effects require enough matching completed
runs and improved forecasts in both later validation periods. The daily reset
does not erase the channel's streak or previous sequence.

The label-pulse candidate smooths next-label probabilities and the number
within each label from shared, channel, suffix, and reset-time histories.
At least three matching completed runs are needed before a context can
adjust its broader prior. Individual number probabilities identify plausible
peaks across all five bands. Low hits remain possible; the model
does not assume an alternating cycle or change numbers to simulate learning.
Activation requires at least 20 chronologically evaluated forecasts issued
after the normal 20-completed-run readiness threshold, with five in each
later period. Both periods must preserve number log loss, overall log loss
must improve by 1%, and exact, band, and ±2 hits plus band Brier score must
not worsen. Forecasts are scored before their outcomes update any history.

New streak forecasts retain every observed possible hit position internally.
The separate streak dashboard charts display five candidates, including the current selected position, so
long outcomes can appear after early windows fail. The original target remains
saved for accuracy evaluation. Previously issued forecasts keep their original
candidate set until that streak ends, except for a forecast-policy upgrade,
which retains valid displayed targets and timestamps its revision. A target
excluded by the new policy is replaced once, even if that run has started.
Migrated forecasts hold that number until it misses, then use its two nearby
retries before selecting another anchor. Their original accuracy score and earlier
target path are retained. The radius is versioned on saved forecasts, so an
older exclusion policy can migrate while retaining its valid current target.
Normal model refreshes keep the selected target locked until it misses.
Completed-run cooldown history and both the actual hit attempt and its preceding
miss count are saved atomically
with the hit before the old forecast is removed.
Every new recorded result updates live context immediately; the minute worker
saves and trains on the new history automatically. Live hits record both band
accuracy and number accuracy without changing the observed hit length. Wager
size is not a prediction feature.
New observations update the probabilities even when the highest-ranked number
stays the same. The process holds a workspace lock so a second `python run.py`
exits before connecting another bot client.

The recent-streak candidate discounts older runs with a four-completed-run
half-life, while retaining the full unweighted history and unfinished misses.
It estimates exact-number probabilities across all bands. Pattern validation
compares probabilities and their highest-probability targets before the display
cooldown and random tie order are applied. Activation
requires forward-only validation against an updating historical baseline:
both later periods must maintain band and ±2 accuracy, with at least 20
evaluated runs, four additional ±2 successes, and no loss in total exact hits.
Each validation result updates the learner only after its forecast is scored.
Persisted forecasts keep their issued number and advancement path when newer
weights arrive; the updated learner applies to newly issued streak forecasts.
