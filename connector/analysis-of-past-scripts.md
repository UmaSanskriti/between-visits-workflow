# Analysis of Past Scripts

Status: DRAFT. This is where the connector learns from earlier calls to choose the pitch angle per doctor.

## Inputs
- Past scripts and their version IDs (from Brain: [inputs/from-brain.md](inputs/from-brain.md))
- Past calls, outcomes, and transcripts (from Voice: [inputs/from-voice.md](inputs/from-voice.md))

## Questions to answer
1. Conversion (booked / called) by script version and by segment
2. Which opening lines and pitch angles correlate with booking, declining, or hanging up
3. Where in the call doctors drop off
4. Which objections recur, and which responses worked
5. What distinguishes segments (solo vs. group, specialty, EHR) in how they respond

## Method
- Join call outcomes to script_version and doctor segment via call_id
- Tag transcripts by objection type and drop-off point
- Compare variants only with enough calls per segment; flag small samples
- **Bias guard**: include declines, voicemails, and no-answers, not only booked calls

## Outputs
- Recommended pitch angle per segment, feeding `{pitch_angle}` in [script-variables.md](script-variables.md)
- Template change suggestions to the Brain ([outputs/to-brain.md](outputs/to-brain.md))

## Open questions
- [ ] How many past calls exist, and who holds them?
- [ ] Is analysis done by the connector, or by the Brain with the connector supplying joined data?
- [ ] How are objections tagged: manually, or by an LLM pass?
