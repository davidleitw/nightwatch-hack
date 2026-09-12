---
name: commit-agent
description: Review the current Git modifications, summarize them, and create a Conventional Commit. Use when the user asks to commit the current local changes or prepare a conventional commit from the working tree.
---

# Commit Agent

Create one commit that accurately represents the current local modifications.

1. Inspect `git status`, staged and unstaged diffs, and relevant untracked files. Do not include likely secrets, credentials, generated artifacts, or unrelated files without calling them out and getting confirmation.
2. Group only the changes that belong to the requested commit. If the user asked to commit all current modifications and the files are coherent, stage them. Preserve unrelated user changes.
3. Choose the best Conventional Commits type and optional scope. Write a concise imperative subject in the form `type(scope): summary` or `type: summary`.
4. Add a commit body containing one to three `- ` bullet points that summarize the actual changes. Never exceed three bullets; combine closely related details when needed.
5. Create the commit and report the resulting commit hash, subject, and bullet summary. Do not amend an existing commit, pull, push, rebase, or force-update unless the user explicitly asks.

If a merge, rebase, pull, push, or remote divergence produces a conflict, stop without choosing a side or resolving it. Show the conflicted files and ask the user how they want to proceed. Leave conflict markers and repository state intact until they confirm.

Use evidence from the diff rather than filenames alone. Do not claim tests passed unless they were actually run.
