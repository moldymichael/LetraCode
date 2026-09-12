# Native messenger and streaming-scroll fix

September 12, 2026. Development branch: `codex/messenger-theme-scroll-fix`.
Baseline: `8772a1916c1d65b6514aa2ffdaf024c8e6e886b8` (0.6.0).
This is source development, not a claim that a user's installation was updated.
The application version remains 0.6.0; use the source revision to identify this
candidate. No release, live data migration or model training is part of this work.

## Appearance

The approved messenger mockup is implemented using native PySide6 widgets and
QTextDocument, not an embedded browser or simulated messaging service. Paper and
Graphite provide neutral surfaces. Only user and assistant bubbles, including
color-picker previews, use the two editable colors. Headers, selection, menus,
buttons, status and navigation stay grayscale.

Bubble colors are available in the Chat sidebar and Settings. Native pickers and
six-digit hex fields update immediately. Swap exchanges the colors. Reset restores
blue `#dce5f3` and pink `#eedce7` while keeping the selected neutral mode. Black or
white text and underlined links are derived for readability. Invalid or cancelled
edits do not change the theme. A validated `messenger_theme` preference is stored
through the existing settings API; there is no schema change.

User messages appear on the right; assistant messages appear on the left. Saved
sender labels, timestamps, response status, thinking controls and receipt links
are retained. Tool/action records stay neutral. The contact header reports local
model state, not internet presence. No fake assistant responses are introduced.

## Why the scrollbar could not reach the bottom

The old renderer called `setHtml` repeatedly during generation, then used the
scrollbar's current maximum. QTextEdit can initially expose a provisional range
while its rich-text document is still being laid out. In the failing regression,
scrolling to the reported end left the view at 3,954 while the eventual maximum
was 8,298. Replacing the document during an active scrollbar drag also changed the
range and the thumb's target under the pointer.

`SafeBrowser.replace_html` now completes the document layout and synchronizes the
scroll range with its actual height before restoring the reading position.
Following the end is an explicit reading state, preserved over reflow rather than
inferred from an intermediate range. A later manual scroll cancels pending
following. An active thumb drag or text selection defers visible replacement;
saved model output continues independently. Release/selection-clear queues the
latest saved render. Latest message and Ctrl+End clear the reading selection and
return to the actual end. Updates while reading older text keep the reading
position instead of pulling the view down.

The renderer still uses whole-document replacement. This change does not claim
incremental rendering or a throughput improvement for arbitrary long chats.

## Boundaries preserved

Model Markdown still disables source HTML and cannot create application-action
links. Resource loading is still blocked in SafeBrowser. Only application-created
message tables receive bubble backgrounds. Per-reply receipts, action details,
Undo authority checks, source approvals, saved drafts, history paging, search,
exports and the existing single/two-model execution paths remain in place.
Theme styling is window-local and retains the application/system font. The native
window manager continues to control the operating system's title bar.

## Verification scope

Tests use disposable data and the real native Qt renderer/scrollbar, with stored
streaming deltas or existing scripted engine peers. They do not certify a real
model, GPU workload or the exact build installed on the maintainer's computer.
Native Windows/Fedora CI and teammate review remain separate from Linux offscreen
verification. The branch temporarily used a dependency-download workflow to set up
Qt in the isolated development environment; it is removed from the final change.

### Recorded verification, September 12

Application source commit: `8a59c2e113cedaa7a9f6a443cf6e13e228f2d02c`.
In the isolated Linux environment (Python 3.13.5, Qt/PySide6 6.8.3), as an ordinary
user, this command completed with **1,175 passed, 18 skipped, 1 deselected and
6 existing multiprocessing deprecation warnings** in 252.98 seconds:

```sh
QT_QPA_PLATFORM=offscreen python -m pytest -q \
  --deselect=tests/test_gemma_backend.py::test_frozen_cpu_ple_lookup_transfers_only_rows
```

The one deselection is an optional-training dependency issue: this environment
has torch but not accelerate. The same test fails for that reason on the unchanged
baseline. The test itself and training code are not changed or skipped in source.
All 16 messenger-theme/scroll tests passed, including actual Qt mouse-thumb drag,
wheel input, manual end navigation, reflow, selection, palette persistence and
rich-text/security checks. Compileall, shell syntax, archive/document tests and
`git diff --check` passed.

The temporary source-transfer workflow applied a SHA-256-verified copy of the
locally tested patch, but its Ubuntu test attempt lacked libEGL.so.1 and stopped
at collection (18 errors). Its pipeline incorrectly masked the pytest exit code
with tee. Its green workflow badge is **not test evidence**. Both temporary
workflows and the transfer payload are removed from the final diff. The normal
Windows/Fedora PR workflow runs pytest directly and remains the authoritative
platform verification; inspect that PR's checks before merging. No native
Windows/Fedora or real-model result is asserted by this local record.
