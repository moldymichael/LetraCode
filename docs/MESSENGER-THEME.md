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
