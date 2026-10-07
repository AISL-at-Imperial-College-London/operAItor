# LLM ablation: model size vs context (feeling-lucky mode, no validator)

Scenarios: S08, C01, D04, S09, S05, S07 (where gpt-5-nano stopped the plant in the campaign); 2 repeats each.

| model | context | runs | plant saved | stops | profit | condenser runs using BOT_TIC | drum TOP_TIC wrong way | cost | S08 | C01 | D04 | S09 | S05 | S07 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| gpt-5-nano | base | 12 | 4 | 8 | $-171,603 | 0/4 | 3/14 | $0.13 | stop/stop | stop/stop | stop/stop | ok/stop | ok/stop | ok/ok |
| gpt-5-nano | loops | 12 | 1 | 11 | $-228,560 | 0/4 | 6/9 | $0.10 | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop | ok/stop |
| gpt-5-nano | manual | 12 | 2 | 10 | $-208,768 | 0/4 | 9/11 | $0.12 | stop/stop | stop/stop | stop/stop | stop/stop | ok/stop | ok/stop |
| gpt-5-nano | dynamics | 12 | 1 | 11 | $-227,447 | 0/4 | 4/4 | $0.12 | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop | ok/stop |
| gpt-5-nano | base-nokg | 8 | 0 | 8 | $-176,335 | 0/4 | 0/0 | $0.03 | stop/stop | stop/stop | stop/stop | stop/stop | — | — |
| gpt-5-nano | dynamics-nokg | 8 | 0 | 8 | $-175,116 | 0/4 | 2/2 | $0.03 | stop/stop | stop/stop | stop/stop | stop/stop | — | — |
| gpt-5-mini | base | 12 | 2 | 10 | $-209,429 | 0/4 | 2/2 | $0.46 | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop | ok/ok |
| gpt-5-mini | loops | 12 | 1 | 11 | $-227,403 | 4/4 | 8/8 | $0.44 | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop | ok/stop |
| gpt-5-mini | manual | 12 | 0 | 12 | $-248,291 | 4/4 | 6/7 | $0.39 | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop |
| gpt-5-mini | dynamics | 12 | 0 | 12 | $-248,464 | 4/4 | 9/11 | $0.37 | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop | stop/stop |
| gpt-5-mini | base-nokg | 8 | 0 | 8 | $-175,467 | 0/4 | 0/0 | $0.11 | stop/stop | stop/stop | stop/stop | stop/stop | — | — |
| gpt-5-mini | dynamics-nokg | 8 | 0 | 8 | $-169,197 | 4/4 | 8/10 | $0.14 | stop/stop | stop/stop | stop/stop | stop/stop | — | — |
