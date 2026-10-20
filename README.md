# Connector Specs

Customer-discovery system for the Launching Tech Ventures project. Three components:

| Component | Owner | Job |
|-----------|-------|-----|
| **Voice agent** | Voice team | Calls doctors at independent practices, asks for a 20-minute discovery interview, records outcomes and transcripts |
| **Brain** | Brain team | Holds startup context, base script, segment guidance, and learns from every transcript |
| **Connector** (this repo) | Me | Merges doctor data with the Brain's base script into a personalized per-call script; routes outcomes and transcripts back to the Brain |

## Data flow

```
Voice team --doctor records--> CONNECTOR <--base template + context-- Brain
Voice team <--personalized script + call queue-- CONNECTOR
Voice team --outcomes + transcripts--> CONNECTOR --transcript package + variant stats--> Brain
```

## Connector outputs

1. Personalized script per call (JSON)
2. Call queue (ordered, tagged)
3. Transcript package for the Brain
4. Audit log
5. Error report
6. Outcome summary (by script version and segment)

## Docs

- [voice-interface.md](voice-interface.md): what I send to and receive from Voice
- [brain-interface.md](brain-interface.md): what I send to and receive from Brain
- [script-variables.md](script-variables.md): every placeholder, source, fallback
- [data-schema.md](data-schema.md): doctor, call, script, transcript records
- [compliance.md](compliance.md): AI disclosure, consent, DNC, recording
- [open-questions.md](open-questions.md): unresolved items per team
- [test-plan.md](test-plan.md): sample doctors and edge cases

## Design principles

- The connector should do more than mail-merge: choose the pitch angle per doctor based on segment and past-call results.
- Every script carries a version ID so each call traces back to the script that produced it.
- Guard against feedback bias: the Brain must see declines and no-answers, not only the "yes" calls.
- The agent books the 20-minute slot. A human runs the discovery interview (at least early on).
