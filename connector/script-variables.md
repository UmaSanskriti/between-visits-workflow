# Script Variables

Every placeholder the connector fills. If the source field is missing, use the fallback; never send a literal `{placeholder}` to Voice.

| Placeholder | Source | Fallback | Notes |
|-------------|--------|----------|-------|
| `{doctor_name}` | Voice doctor record | "Doctor" (use "Hi, may I speak with the doctor?") | Check pronunciation hint |
| `{practice_name}` | Voice | "your practice" | |
| `{specialty}` | Voice | "independent practice" | Drives segment |
| `{practice_size}` | Voice / enrichment | omit sentence | solo / small group |
| `{location}` | Voice | omit | Also sets calling hours |
| `{ehr}` | Enrichment | omit sentence | Only if known |
| `{segment}` | Connector logic | `default` | Picks pitch angle |
| `{pitch_angle}` | Connector + Brain guidance | Brain default angle | |
| `{ai_disclosure}` | Compliance | **Required, no fallback**: block the call if missing | |
| `{ask}` | Brain | **Required** | 20-minute conversation |
| `{caller_identity}` | Brain | **Required** | Who we are |
| `{slot_options}` | Calendar | "a time that works for you" | |
| `{script_version}` | Brain | **Required** | Logged, not spoken |

## Rules

- Required fields missing: do not queue the call; write to the error report.
- Optional fields missing: use fallback and record it in `variables_used`.
