# LLM attempts audit

Counted from every finished run file (one file per scenario and configuration). A *decision* is one fault response; each decision allows up to 4 proposals (the first plus 3 reprompts with feedback). Rejection reasons: **empty** = no commands (malformed reply, or the model proposed nothing); **not exec.** = commands the plant cannot run (unknown target, out of bounds); **repeat** = same commands as an already-rejected proposal; **judge** / **twin** / **ADMIT** = rejected by that validator.

| model | configuration | runs | decisions | proposals | malformed replies | accepted 1st try | rejected: empty | not exec. | repeat | judge | twin | ADMIT | reprompts | commands sent | no command sent |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| GPT-5 nano | no validation | 20 | 339 | 339 | 0 | 100 % | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 329 | 10 |
| GPT-5 nano | LLM judge | 20 | 359 | 572 | 0 | 59 % | 0 | 8 | 18 | 201 | 0 | 0 | 213 | 338 | 21 |
| GPT-5 nano | digital twin | 20 | 238 | 927 | 0 | 3 % | 0 | 0 | 131 | 0 | 785 | 0 | 689 | 142 (131 fallback) | 96 |
| GPT-5 nano | ADMIT (sim) | 20 | 435 | 1462 | 0 | 14 % | 0 | 221 | 375 | 0 | 0 | 727 | 1027 | 135 | 300 |
| GPT-5 nano | ADMIT (no sim) | 20 | 402 | 1292 | 0 | 18 % | 0 | 169 | 344 | 0 | 0 | 621 | 890 | 148 | 254 |
| Llama 3.2 3B | no validation | 20 | 322 | 1260 | 169 | 0 % | 322 | 9 | 910 | 0 | 0 | 0 | 938 | 14 | 308 |
| Llama 3.2 3B | LLM judge | 20 | 296 | 1184 | 134 | 0 % | 295 | 24 | 822 | 43 | 0 | 0 | 888 | 0 | 296 |
| Llama 3.2 3B | digital twin | 20 | 323 | 1290 | 214 | 0 % | 323 | 14 | 938 | 0 | 14 | 0 | 967 | 6 (5 fallback) | 317 |
| Llama 3.2 3B | ADMIT (sim) | 20 | 332 | 1306 | 121 | 0 % | 332 | 2 | 949 | 0 | 0 | 6 | 974 | 17 | 315 |
| Llama 3.2 3B | ADMIT (no sim) | 20 | 332 | 1299 | 128 | 0 % | 332 | 1 | 945 | 0 | 0 | 1 | 967 | 20 | 312 |
| Qwen3 8B | no validation | 20 | 339 | 345 | 0 | 98 % | 0 | 6 | 0 | 0 | 0 | 0 | 6 | 339 | 0 |
| Qwen3 8B | LLM judge | 20 | 358 | 397 | 0 | 92 % | 0 | 8 | 4 | 29 | 0 | 0 | 39 | 356 | 2 |
| Qwen3 8B | digital twin | 20 | 250 | 967 | 0 | 3 % | 0 | 15 | 281 | 0 | 658 | 0 | 717 | 143 (130 fallback) | 107 |
| Qwen3 8B | ADMIT (sim) | 20 | 307 | 581 | 0 | 61 % | 0 | 59 | 178 | 0 | 0 | 101 | 274 | 243 | 64 |
| Qwen3 8B | ADMIT (no sim) | 20 | 320 | 490 | 0 | 73 % | 0 | 47 | 92 | 0 | 0 | 62 | 170 | 289 | 31 |
| Mistral 7B | no validation | 20 | 369 | 384 | 1 | 96 % | 1 | 14 | 0 | 0 | 0 | 0 | 15 | 368 | 1 |
| Mistral 7B | LLM judge | 18 | 241 | 248 | 1 | 97 % | 1 | 6 | 0 | 0 | 0 | 0 | 7 | 241 | 0 |
| Mistral 7B | digital twin | 16 | 96 | 366 | 1 | 5 % | 1 | 7 | 203 | 0 | 148 | 0 | 270 | 49 (42 fallback) | 47 |
| Mistral 7B | ADMIT (sim) | 3 | 6 | 6 | 0 | 100 % | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 |

## ADMIT violation codes (first failing gate per rejected record)

| model | configuration | most frequent codes |
|---|---|---|
| GPT-5 nano | ADMIT (sim) | `AAS-T3-STEP-MISSING` 247, `AAS-T3-STEP-ORDER` 197, `AAS-T1-EVIDENCE-UNKNOWN` 154, `AAS-T1-UNTRUSTED-SOURCE` 54, `AAS-T0-BAD-FIELD` 31 |
| GPT-5 nano | ADMIT (no sim) | `AAS-T3-STEP-MISSING` 235, `AAS-T3-STEP-ORDER` 159, `AAS-T1-EVIDENCE-UNKNOWN` 112, `AAS-T1-UNTRUSTED-SOURCE` 53, `AAS-T0-BAD-FIELD` 34 |
| Llama 3.2 3B | ADMIT (sim) | `AAS-T1-EVIDENCE-MISSING` 11, `AAS-T4-ENVELOPE` 5 |
| Llama 3.2 3B | ADMIT (no sim) | `AAS-T1-EVIDENCE-MISSING` 3 |
| Qwen3 8B | ADMIT (sim) | `AAS-T0-BAD-FIELD` 46, `AAS-T4-ENVELOPE` 38, `AAS-T1-UNTRUSTED-SOURCE` 23, `AAS-T3-REVERSIBILITY-MISMATCH` 10, `AAS-T3-STEP-MISSING` 6 |
| Qwen3 8B | ADMIT (no sim) | `AAS-T0-BAD-FIELD` 65, `AAS-T1-UNTRUSTED-SOURCE` 12, `AAS-T3-REVERSIBILITY-MISMATCH` 8, `AAS-T3-STEP-MISSING` 4, `AAS-T1-EVIDENCE-MISSING` 1 |
