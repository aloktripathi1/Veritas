# Stage: bounded repair
Your previous brief failed automatic validation. Fix ONLY the listed problems, keeping everything else. Remove or qualify any claim you cannot support with a listed evidence/calculation id; move unsupported matters to Open questions. Output the full corrected brief with the same four sections.
Validation problems:
{issues}

How to fix typical problems:
- uncited_claim / number_unsupported: add the citation of the item that contains that number, or split the sentence so each part has its own citation; otherwise delete the claim.
- metric_mismatch: you attached a number to the wrong metric; cite the right item or remove the number.
- too_long: delete the least material bullets and shorten Open questions until under 480 words; do not add new content.
- injection_phrase: remove recommendation language, or attribute it ("a promotional blog claims ...") with a citation.
- uncited_claim on one sentence of a multi-sentence bullet: give THAT sentence its own citation before its full stop (not only the last sentence of the bullet), or merge it into a cited sentence.
- Output only the four sections of the brief. Never output JSON, the registry, or any other text.
