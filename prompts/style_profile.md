# Style Profile Generator

You are analysing a collection of emails written by a university professor
to produce a concise writing style guide. This guide will be injected into
a prompt that generates draft email replies on the professor's behalf — so
accuracy matters. The closer the drafts match the professor's real voice,
the less time they need to spend editing.

## What to look for

Analyse the provided emails and extract concrete, observable patterns.
Do not generalise or guess — only describe patterns that are actually
present in the samples.

Focus on:

**1. Salutations and closings**
- What greeting format do they use? (Hi, Dear, Hello, first name only, etc.)
- Does it vary by relationship (student vs. colleague vs. editor)?
- What sign-off do they use? ("Best,", "Best regards,", "Thanks,", etc.)

**2. Sentence and paragraph structure**
- Are sentences short and direct, or longer and more elaborated?
- Do they use bullet points or numbered lists?
- Typical email length (number of sentences/paragraphs for a typical reply)?

**3. Tone and register**
- Formal, semi-formal, or informal?
- Does this shift depending on recipient?
- Any characteristic warmth, directness, humour, or formality markers?

**4. Vocabulary and phrasing**
- Characteristic phrases they actually use
- Phrases or constructions they clearly avoid
- Any domain-specific language (academic, Dutch expressions, etc.)

**5. How they handle specific situations**
- Declining requests
- Acknowledging receipt / buying time
- Making commitments
- Asking follow-up questions

## Output format

Write the style profile as a concise Markdown document. Be specific and
concrete — show examples from the emails where helpful. Aim for 300–500
words total. The profile should be actionable enough that someone who has
never read the professor's emails could draft a convincing reply.

Start with a brief one-line summary of the overall voice, then cover each
of the five areas above. End with a short "What to avoid" section listing
the biggest stylistic mismatches to watch out for.

Do not mention that this is AI-generated, do not reference these
instructions, and do not begin with phrases like "Based on the provided
samples...". Just write the profile directly.
