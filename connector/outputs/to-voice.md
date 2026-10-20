# Output: To Voice

Status: DRAFT. Items marked **[CONFIRM]** need an answer from the Voice team.

## 1. Personalized script per call (JSON)
call_id, doctor_id, script_version, segment, first_message, system_prompt, variables_used, fallbacks_applied, fallback_text. Shape: [../data-schema.md](../data-schema.md)

## 2. Call queue
Ordered list of doctors with the script attached and priority or segment tags. Excludes DNC doctors and anyone outside permitted calling hours ([../compliance.md](../compliance.md)).

## Rules
- A literal `{placeholder}` must never reach Voice; use fallbacks ([../script-variables.md](../script-variables.md)).
- If a required field (e.g. AI disclosure) is missing, the call is blocked and logged to the error report.

## To pin down **[CONFIRM]**
1. How Voice accepts a script: API, webhook, or file; JSON format
2. Prompt and first-message length limits
3. Supported variables; name-pronunciation handling
4. Who owns AI disclosure, voicemail behavior, retries, calling hours, opt-outs
