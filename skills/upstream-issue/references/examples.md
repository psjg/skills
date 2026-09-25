# Worked examples

- **Two issues on `paulsmith/computer-use-jev`** (2026-09-25, #2 and #3). No
  contribution guide or AI policy; the one open issue was careful and
  reproducible, and the drafts matched it. #2 had pinned links to the
  hard-coded endpoint and a paste-ready test, and offered a small PR "if this
  direction is welcome". #3 first claimed a model "typed eight times because
  of" a 200-character cap; that run had been confounded by the agent's own
  bug, so the claim came out. Both went out under the machine account with a
  per-command token, author and active account checked afterwards. Both put
  the disclosure at the end, which the user then corrected: it belongs on
  the first line.
- **A test report on an existing PR** (mozart/mozart2#354, comments
  [5829540306](https://github.com/mozart/mozart2/pull/354#issuecomment-5829540306)
  and
  [5829891090](https://github.com/mozart/mozart2/pull/354#issuecomment-5829891090)).
  Two crash traces, a 40-run flakiness check and the Nix expression inline;
  the follow-up linked a public flake in a
  [gist](https://gist.github.com/psjg/d65b60dc7ffb354f0fed30af1541121c),
  which this skill now replaces with a fork carrying the flake. The
  first draft credited a stack-size flag as the fix; the crash reports said
  null dereference, and the report was rewritten around them.
- **A bug found by a port, on a dormant project** (Mozart/Oz 1.4.0,
  `mozart/mozart`, no merge since 2018). The 3-line `sizeOf()` fix is
  attestable and goes as an issue with the diff inline, plus a PR if the
  user carries it; the 16-patch 64-bit port is a fork as a patch series on
  the release tag, linked from the issue, following the precedent of the
  earlier x86_64 fork it builds on. The draft's inference about 32-bit
  builds was replaced by an instrumented measurement before review.
- **A packaging bug with a one-line fix** (nixpkgs `chuffed` 0.13.2):
  `chuffed.msc` held paths relative to the working directory; the fix is
  absolute `$out` paths in `postInstall`, small and certain enough to put in
  the issue as a diff, with the `Assisted-by:` trailer if it becomes a PR,
  because nixpkgs requires that form.
- **A patch the user would not maintain** (a Catala port). The user read
  upstream's AI guidelines first, then decided against carrying a PR: the
  change stayed a local module, published under their own GitHub.
