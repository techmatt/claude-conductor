# Picks

When an executor produces candidates (art variants now, anything visual later), Matt
picks a favourite per row in his browser, adds a note where none is right, and saves;
the planner reads his choices to write the next prompt. One generic page serves every
project (`src/conductor/web/picker.html`); it knows nothing about what it shows. Code:
`picks.py` (manifest, choices, what is pending; no HTTP) and `picker.py` (the server).

**Manifest.** A JSON file the executor writes in its code repo; the preamble's
*Pickers* paragraph gives it the schema, since executors never see these docs. The
paragraph goes in only when the item's body could produce candidates (it names a picker,
`pick:`, candidates, alternatives or an `art:` command: `executors.md`, *Two preambles*):

```json
{"name": "art_alts_009",
 "title": "Hero art alternatives",
 "intro": "optional text above the rows",
 "rows": [{"id": "hero", "label": "Hero, idle pose", "note": "optional",
           "options": [{"key": "current", "image": "hero/current.png"},
                       {"key": "1", "image": "hero/alt1.png", "label": "warmer"}]}]}
```

It is named by its ref, `<repo>/<path relative to the repo root>` (normalised: `\`
becomes `/`, `.` and doubled separators go), and read from the repo's **main
checkout**, never a worktree. Validation, on every `pick:`; the error names the place
(`rows[2].options[1].image: missing file art/alt3.png`):

- the path is relative, has no `..`, and stays inside the repo; the file exists, is at
  most 1 MB, and is UTF-8 JSON whose top level is an object. "Inside" is judged on the
  path as written, links not followed, so a Drive-homed `shared` folder (a junction out
  of the main checkout) counts as the repo's;
- `name`: a slug, `[a-z0-9][a-z0-9_-]{0,63}` (it becomes a URL part and a file name),
  unique per project: a `pick:` is refused while the daemon's server serves that name
  for another manifest, or while `picks/<name>.choices.json` holds another manifest's
  choices; `title`: a non-empty string; `intro`: optional string;
- `rows`: a non-empty list; each row's `id` a non-empty string unique in the file,
  `label` a non-empty string, `note` an optional string, `options` a non-empty list;
- each option's `key` a non-empty string without whitespace, unique in its row (it is
  what the next prompt uses: `"current"`, `"1"`, …); `label` an optional string;
  `image` a path relative to the manifest (`..` allowed while it stays in the repo; no
  absolute path or drive), an existing file ending `.png .jpg .jpeg .gif .webp .avif .svg`;
- unknown keys are ignored, so the format can grow.

By convention the first option of a row is the current one, and it is the default; a
row whose choice differs from it is *changed*.

**Many rows** (58): `"kind": "many"` on a row (`"one"`, the default, is pick one) asks
"which of these go forward": each option is kept or dropped, any number kept, so an
executor need not ask Matt to "select the bare tile to carry it" (aicomplete 026).
`"default": [keys]`, optional, is what is kept to start with (none if absent); a
default naming a key the row lacks, or any other `kind`, refuses the manifest. Its saved
row has `keys` (kept, in option order) instead of `key`, and is *changed* when they
differ from the default.

**Surviving the merge.** Generated images belong in a `shared` folder, never committed
build output (aicomplete 026 committed 20 previews to `main` because its repo had none;
the preamble says so, and a planner checks `conductor.toml` before queuing a picker
prompt). The manifest and its images must be on `main` by the time the
`finish` event is logged, which is what the `pick:` line points at: commit them, or put
them in a `shared` folder (linked into every worktree, so present in the main checkout
already). An item with `paths:` must cover where it writes them. A `readonly` item is
never merged (its worktree is discarded), so it must use a `shared` folder.

**Signal and server.** `pick: <repo>/<manifest path>` (planner, app, `conductor pick`)
validates the manifest, starts the picker server if it is not up, and opens the
picker's page in the default browser (`pick_open`; `pick_failed`, red, if opening the
browser raised). A bad manifest, or a server that cannot start, rejects the signal file
(`invalid`, `.bad`, with the reason). The server:

- listens on `127.0.0.1` only, on a free port, started by the first `pick:`: **one per
  daemon**, serving any number of pickers, keyed by `name`. It stops when the daemon does;
- URLs are `http://127.0.0.1:<port>/<token>/<name>/`, the token random per daemon start,
  so other pages in the browser cannot find it;
- routes: `GET …/` (the page), `GET …/manifest.json` (the manifest as the page reads it,
  with the saved choices), `GET …/img/<row>/<option>` (by index into the validated
  manifest; no part of a URL is ever joined to a file path), `POST …/save`;
- **everything else is a 404**: another token, name or index, an extra path part, a
  query on an image, any other method (HEAD, PUT, DELETE, OPTIONS, …), a `Host` that is
  not `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding);
- a save must be `application/json` (a cross-site form cannot send that without a
  preflight the server never answers), at most 1 MB; a bad body is a 400 with the
  reason, a choices file that cannot be written a 500. Saves are one at a time (two
  tabs on one picker cannot interleave their writes). SVGs carry
  `Content-Security-Policy: sandbox`, every response `X-Content-Type-Options: nosniff`.

The manifest is a snapshot taken at `pick:` time; re-send `pick:` to serve an updated
one (same name: replaced). Images are read from disk on each request; one deleted since
is a 404. Handler threads touch only files and a queue: the daemon's loop appends their
events (`pick_saved`) each tick. **A daemon restart loses the server** (port, token and
what it serves): an open tab's Save then fails, and the page says so and keeps its
choices. Re-send `pick:` and save from the new tab.

**The page.** One row per `id`: label and note, then the options as tiles captioned
`key · label`, about 256px tall (a size slider, 128–640px, remembered by the browser).
Click a tile to select it; click the selected tile, or its magnifier corner, for a
full-size overlay where ←/→ step through the row and Enter picks the one shown. **The
page never alters how an image looks**: no opacity, filter, tint or overlay on any
image, selected or not, in the tiles or the overlay; nothing is drawn over one but the
magnifier button. The selection shows only outside the image: a strong accent outline
around the tile and a caption in the accent colour with a check and bold key. Each
row starts on its saved choice, else its first option; a changed row is badged. In a
many row a click toggles a tile (captioned `✓ keep` when kept; the magnifier opens the
overlay, where Enter toggles and stays open), and the row head counts `N of M kept`. A note
field per row. A sticky bar: "N of M changed · K notes", *show changed only*, and
**Save** (Ctrl+S), which shows "Saved 4:52pm". Leaving with unsaved changes warns.
Light or dark by system setting.

**Choices.** Save writes `picks/<name>.choices.json` in the control folder (git-ignored
runtime, like `questions/`), overwriting any earlier save:

```json
{"name": "art_alts_009", "title": "Hero art alternatives", "manifest": "code/picks/art_alts_009.json",
 "saved_at": "2026-10-18T16:52:03-07:00", "changed": 6, "notes": 2,
 "rows": [{"id": "hero", "key": "2", "changed": true, "note": "hair still too orange"}]}
```

Every manifest row appears once, in manifest order, with a key from its own options
(a many row: `"keys": [...]`, saved by the page as `keys` too);
`changed` is worked out by the server, never taken from the page; notes are stripped,
at most 4000 characters, possibly empty. Reopening loads the save (the page shows "last
saved 4:52pm"): rows match by `id`, and a key no longer offered falls back to the first
option with its note kept. A choices file whose `saved_at` is not a stamp with a zone is
ignored, as if unreadable.

**Pending.** A finished item whose `finish` carries `pick` is *saved* when a choices file
for that ref was saved after the finish, else *opened* when a `pick_open` for it came
after the finish, else *waiting*. So a later item that reuses a manifest path starts
again at waiting. Every such item in the current view is pending; so is one settled by a
checkpoint while its picker is unsaved and no later finish names the same manifest, so
a picker never drops out unpicked (saving it, even unchanged, lets it settle). The
inbox's *Picks* (`inbox.md`) and the app's **Pick…** buttons both read it; the app keeps an unsaved
one among the current finished rows, however old.

**Retiring.** Executors never delete a picker's files (the preamble says so: 029 deleted
round 1's while Matt still wanted it); conductor does, by retiring it. A picker's
**series** is its name minus a trailing `_r<N>` (`art_alts_r2` → `art_alts`, round 2).
Once a choices file is saved for a round, every live picker of that series with a lower
round is retired: after each `pick_saved` and at daemon start (a save made by `conductor
pick` with no daemon). A name with no round is retired only by the signal `retire:
<name>` (planner or app). Retiring deletes the manifest and its option images from the
main checkout, but keeps a file git tracks (deleting it would dirty `main`) and an image
another live picker still shows (a round's "current" tile is often the last round's
pick); the choices file stays. It logs `pick_retired` (name, manifest, `deleted`, `kept`,
and `later` or `by`), drops the name from the server (its URLs 404) and from *Pending*,
and the inbox's *Picks* lists it for a day. A name is retired once; `retire:` on an
unknown or retired name is refused.

**CLI.** `conductor pick <repo>/<path>` validates first (`conductor: <reason>`, exit 1,
and a red `pick_failed` event with the reason, as the signal's `invalid` would be).
With the daemon alive it writes `signals/cli-….signal` (`by: cli`), and the daemon opens
the picker. With no daemon it serves the picker itself in the foreground, printing the
URL and each event as it is logged, until Ctrl+C. Those events go to `events.db` as the
daemon's would.
