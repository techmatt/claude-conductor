# The app: icon, title, tray

How the app shows each project's state at a glance.

**Icon.** A tetrahedron's four nodes and six links around a gold hub, in navy:
`src/conductor/app/icon.svg` is the master; `conductor.ico` beside it (16, 20, 24, 32,
48, 256 px; rebuilt by `uv run python tools/build_icon.py`) is the window's and the
taskbar's. At 24 px and below the links and nodes are drawn bolder so the shape still
reads. The app sets its own AppUserModelID (`conductor.app`, `procs.set_app_id`), so the
taskbar shows this icon rather than Python's. Each project's tab carries the icon with
its hub red (needs Matt), orange (waiting for review), gold (running) or slate (idle);
its name is red or orange to match, selected (bold) or not: the style sheet draws a selected
tab dark unless its bar's `signal` property says otherwise (`theme.mark_selected`, 121; an
open question had shown bold black). Every tab bar in the app does the same.

**Tab motion.** While a project has an item moving (executing, setup, merge and
concluding included, or a stage still staging, in a repo that isn't paused), its tab's name leads with the title's turning
glyph, ◐ ◓ ◑ ◒ once a second (`status.moving`, `status.tab_text`), whichever tab is
selected. It turns on while that project needs Matt (the hub says that), stops when the item finishes or its repo is paused, and rides the refresh, so a
still row of tabs means nothing is running anywhere.

**Window title**: the most urgent thing across every open project, cheap enough to ride
the refresh:

| State | Title |
|---|---|
| Items running (a stage still staging counts; a staged item waiting with its plan doesn't), whatever else holds | `◐ 2 running · Conductor · pick · 1 to review`, the glyph turning ◐ ◓ ◑ ◒ once a second |
| Nothing running, something needs Matt: a blocking question, a red hold, a picker waiting, a daemon down with work | `● Conductor · 1 question` (`· red`, `· pick`, `· daemon down`; counts when more than one) |
| Nothing running, something waiting | `🟠 Conductor · 2 to review · 1 open question` |
| Idle | `Conductor` |

Running leads (127): glyph and count come before the app's name, so a taskbar button
cut to its first words still says it (one cut `● Conductor · pick · 1 running` to
`• Conductor · pick ...`). What needs Matt follows, red on the badge. The glyph's timer
(`TURN_MS`) runs while anything runs or a tab turns; running leaves the title on the
first refresh after the last item ends. A project whose refresh fails part-way keeps its last whole
snapshot in the title. The header's state line above the tabs is unchanged.

**Waiting** (81): an unreviewed report or any open question (non-blocking and design
included; a blocking one is also *needs Matt*), in any project, paused repos included.
The title adds it to the other parts (`· 1 to review`, `· 1 open question`) and, with
nothing running, leads with an orange dot. A window title can't be coloured, so the
colour lives on the icon: the **taskbar** (the window's icon, `icon.window_icon`) gets a
badge over its bottom-right corner, orange and still with nothing running, orange and
turning (its bright half going round ◐ ◓ ◑ ◒ with the title) while items run too, red
while something needs Matt, none while items only run or nothing does (124, Matt: the
gold running badge duplicated the title's turning glyph; 85 dropped it, 120 restored it,
124 drops it again). The title's `N running` shows beside what needs Matt (84), and
leads it (127). A project tab's hub
and name turn orange the same way (`status.tab_kind`). A `reviewed` event or the
question's answer clears it on the next refresh (`Status.badge`, `status.summarize`).

**Tray.** The taskbar's badge, and the same states on the icon's hub: red when something
needs Matt, orange when something waits (a review or a question), slate otherwise, items
running or not (124; 120's gold for running is gone).
Its tooltip lists each project's state; clicking opens the window. Closing the window
hides it to the tray; *Quit* in the tray menu exits. Neither touches a daemon or an
executor. Without a system tray, closing the window quits.

**Toast click.** Not wired. Toasts are raised under Command Prompt's app id (`toasts.md`), so a plain click goes to that app, never to this one. `windows-toasts` can
make a toast *protocol*-activated (`Toast(launch_action="scheme:…")`), which Windows
honours for unpackaged apps; opening the app on an item would need a URI scheme
(e.g. `conductor-app:`) registered under `HKCU\Software\Classes` pointing at
`conductor-app`, plus a single-instance hand-off to a running app. Unverified and left
for later; *Events* lists the ids a toast's tag names in its first column.
