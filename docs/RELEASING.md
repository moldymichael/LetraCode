# Release process

Publishing a release is a separate maintainer decision. Building an archive, merging a PR or seeing one green job does not publish or certify a release.

For each release, name two people in the tracking issue or pull request: a **release owner** who prepares the candidate and a **verifier** who independently checks the evidence. These responsibilities can swap between releases; they are not permanent platform roles. Neither person should approve their own release change.

## 1. Define the candidate

1. Agree on the release scope, supported platforms and known limitations in an issue or pull request.
2. Start from an up-to-date, clean `main`. Record the exact candidate commit.
3. Confirm that version metadata, user-facing docs and intended artifact names agree. Source version and published release version remain different until publication is complete.
4. Review pinned GitHub Actions, Python build requirements and the Inno Setup compiler source, version and checksum against their official releases. Confirm compatibility with the supported Python and platform matrix; an automated dependency PR is a prompt for review, not approval.
5. Review [known issues](KNOWN-ISSUES.md), open release-blocking issues and in-flight pull requests. Resolve or explicitly disclose each relevant limitation; do not describe an open issue as fixed because adjacent CI work passed.

## 2. Produce evidence for that commit

The release owner records the commands, environment and exact results. The verifier checks that the evidence belongs to the candidate commit and covers the claims in the release notes.

- Run the full Fedora regression suite and source/package build required by the workflow.
- Run the Windows source matrix and Windows package lifecycle job.
- On a native Windows desktop, open the installer or portable build with disposable data and perform the [Windows smoke test](WINDOWS.md#first-smoke-test).
- For claims about inference, complete a real reply with the named llama.cpp build, GGUF, settings and hardware. A scripted server or package startup is insufficient.
- Exercise update and data-retention behavior on copied or disposable data. Keep live chats, notes, models and training artifacts out of release tests.
- Verify each checksum against the final artifact bytes.

A failed or skipped required CI job blocks publication. Expected platform-specific test skips must be recorded with their scope; they are distinct from skipping an entire required job. One packaging pass cannot waive a failed application suite. Do not weaken a safety check to make a candidate green.

## 3. Review and publish

1. Open a focused release pull request. It requires approval from the other teammate and the `repository`, three `windows` matrix, `windows-package` and `fedora` checks before merge. The `main` policy requires one approving review, the complete required checks and a branch current with `main`. New commits invalidate stale approvals.
2. Prepare release notes that name the version, exact commit, supported platforms, installation/update path, verification performed and known issues. Keep historical test results attached to the commits that produced them.
3. The verifier compares the proposed tag, version metadata, artifact filenames, checksums and release notes with the reviewed commit.
4. Publish the tag and GitHub release only after that review and an explicit release decision. Upload artifacts built from the verified commit; do not substitute a same-version artifact from another run.
5. Download the published artifacts, verify their checksums, and perform a small installation/startup check from the public download path.

## 4. Close the handoff

Update the README, [Windows guide](WINDOWS.md), [known issues](KNOWN-ISSUES.md) and documentation index if the newly published version changes their status statements. Link the release and its final evidence from the tracking issue. Record any follow-up work without rewriting older verification records.

If publication or the public-download check fails, stop and preserve the candidate evidence. Correct the problem in a new reviewed commit or withdraw the affected asset/release with a clear explanation; do not silently replace bytes behind an existing checksum.
