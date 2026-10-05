---
name: my-review
description: Inspect, cluster, and resolve GitHub PR review comment threads, analyze local code diffs, and draft single-confirmation batch replies.
---

# Git Pull Request Review & Reply Workflow

Coordinate the **Review Specialist** with the **Tech Lead (Houhou)** to inspect, analyze, and resolve review comments across GitHub Pull Requests.

## Standard Execution Protocol: Single Confirmation Gate

DO NOT post responses piecemeal or ask for confirmations repeatedly for individual threads. Adhere strictly to the batch protocol:

1. **Extract Review Threads (`fetch_pr_review_threads`)**:
   - Automatically identify the Git repo from workspace configuration (`owner/repo`) and fetch review threads by PR number (`pull_number`).
   - Group comments into threads, extracting target file paths, line numbers, and diff hunks.
   - Filter for unresolved discussions (`unresolved_only=True`).

2. **Analyze Code & Draft Batch Resolutions**:
   - Trace line references in the local workspace, examine reviewer intent, and make necessary code adjustments.
   - Formulate technical replies for **ALL** active threads.
   - Present a consolidated **Review & Reply Summary Table** containing: File, Line, Reviewer feedback, Resolution summary, Commit hash.

3. **Single Confirmation Gate & Batch Dispatch (`post_batch_review_replies`)**:
   - **Single Confirmation Gate**: Present the summary table to the user for **EXACTLY ONE** global confirmation across the entire PR.
   - Upon user approval, invoke **`post_batch_review_replies`** to push all resolutions simultaneously in a single atomic operation.
   - If reviewing external PRs: Draft all review comments against the diff and invoke **`submit_pr_review`** with the unified verdict (`APPROVE`, `REQUEST_CHANGES`, `COMMENT`) in one shot.

---

## Display Standard

When reporting review threads to the user, follow this structured format:

```markdown
### PR #<number>: <title>
- **Repository:** `<owner>/<repo>` | **Author:** @<author> | **State:** <state>
- **Total Threads:** <total> | **Unresolved:** <unresolved>

---
#### [Thread #<id>] `<file_path>`:<line_number>
- **Author:** @<reviewer> | **Status:** UNRESOLVED | **Comments:** <count>
- **Diff Context:**
```diff
<diff_hunk>
```
- **Review Comments:**
  - **@<reviewer>** (<date>): <comment text>
- **Proposed Action:** <remediation strategy>
---
```

## Batch Summary Table Standard

Before dispatching to GitHub, present the single-confirmation batch table:

| Thread ID | File | Line | Reviewer | Proposed Resolution / Reply |
|:---|:---|:---|:---|:---|
| #101 | `src/auth.ts` | 42 | @reviewer | Fixed nil check in commit `abc1234` |
| #102 | `src/api.ts` | 88 | @reviewer | Updated parameter validation |

Wait for user confirmation, then call `post_batch_review_replies(pull_number, replies=[...], summary_text=...)`.

## GitHub Reply Standard

Outbound replies posted to GitHub follow a 3-part engineering standard:

```markdown
**Resolution:** <Concise technical summary of fix>

**Code Changes:**
```diff
<diff showing modifications>
```

Fixed in commit `<commit_hash>`. Ready for re-review.
```


