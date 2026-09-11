# Installed comparison workflow repair

Installed at the user's request from the canonical
`/home/miceoil/Projects/LetraCode` checkout using `./install.sh --no-deps`.
The Python, PySide6 and pypdf dependencies were verified before installation.
No running LetraCode application process was found, and none was stopped.

The version label remains 0.6.0; the installed modules include the comparison
workflow repair. Module content, rather than that shared version label, identifies
this update. The source changes remain in the canonical main checkout.

Verification after installation:

- `~/.local/bin/letracode --version` succeeds and reports LetraCode 0.6.0.
- Every installed module under `~/.local/share/letracode-app/letracode` matches
  the canonical source, excluding generated `__pycache__` files.
- A disposable offscreen smoke test imported the installed package directly,
  opened Compare with Strand immediately without inference, saved a fresh prompt
  alongside the original held-out questions, and exported the comparison record.
- All 394 existing entries under `~/.local/share/letracode` retained their paths,
  modes, sizes, modification times and symlink targets through installation.
  No live chat, training run, model adoption or rollback was invoked by the check.

Open LetraCode and select **Improve → Compare and choose**, choose the candidate,
then **Compare with Strand**. Add fresh questions and choose **Run comparison**.
