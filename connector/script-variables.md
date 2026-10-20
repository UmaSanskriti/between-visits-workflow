# Script Variables

Every placeholder the connector fills, aligned with playbook §4 and §6. If a source field is missing, use the fallback; never send a literal placeholder to Voice. Syntax is `{snake_case}` in v0 and `{{double_braces}}` in the playbook (**[CONFIRM]** with the Brain team).

| Placeholder | Source | Fallback | Notes |
|-------------|--------|----------|-------|
| `{doctor}` | Contact record (practice website) | "the doctor" | Check pronunciation hint |
| `{practice}` | Contact record | "your practice" | |
| `{specialty}` | Contact record | omit | Drives segment |
| `{practice_size}` | Contact / enrichment | omit | solo / small group; drives segment |
| `{hook}` | Contact record: one line from the practice website | omit; the pitch works without it | Woven in naturally, never forced |
| `{segment}` | Connector: specialty × practice size | `default` | Picks pitch angle |
| `{pitch_angle}` | Connector, using Brain guidance for the segment | Brain default angle | |
| `{slot_1}` … `{slot_4}` | Anirudh's calendar (stub in v0): next 10 business days, 30 minutes, Boston time | at least `slot_1` and `slot_2` required; otherwise ask for "the best week or time of day" | Offer 1-2 first, then 3-4 |
| `{callback_number}` | Config | **Required. No fallback**; blocks the call when empty | Currently TBD in the playbook |
| `{ask}` | Brain | **Required** | 30-minute conversation |
| `{caller_identity}` | Brain | **Required** | Anirudh Bagla, HBS student |
| `{script_version}` | Brain | **Required** | Logged, not spoken |

## Not a placeholder: the LOCKED opening
The AI and recording disclosure (playbook §5 and §6) is not a variable and has no fallback. The connector **always prepends** it to the first message (BRN-001, CMP-004, CMP-005) and never edits it. If the Brain's script contains its own version, the playbook's LOCKED text still wins.

## Rules
- Required field missing: do not queue the call; write to the error report.
- Optional field missing: use the fallback and record it in `fallbacks_applied`.
- An unfilled placeholder anywhere in the output blocks the call.
- Inputs are public business information only. Never use patient data, and never a doctor's mobile or personal line.
