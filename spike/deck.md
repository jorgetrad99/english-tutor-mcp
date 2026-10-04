# Situation deck (spec §7)

Draw without replacement; reshuffle when empty. Record the card number in runs.csv.

| # | Type | Situation |
| --- | --- | --- |
| 1 | explain | Standup: explain last night's outage and what you are doing about it |
| 2 | explain | Explain a design decision to a product manager who is not technical |
| 3 | negotiate | Ask your lead to move a deadline by three days |
| 4 | negotiate | Agree with a client which features to cut from a release |
| 5 | disagree | Push back on a code review comment you think is wrong |
| 6 | disagree | Disagree with an architecture choice in a team meeting |
| 7 | ask for help | You are stuck on a bug; ask a senior engineer for help |
| 8 | ask for help | Ask the infrastructure team for production access |
| 9 | give feedback | Give feedback on a junior developer's pull request |
| 10 | give feedback | Give your manager feedback in a 1:1 |
| 11 | small talk | The first two minutes of a call with a US client |
| 12 | small talk | Coffee chat with a new teammate from the US |

Shuffled order for the week (generate once, paste here):
`uv run python -c "import random; print(random.sample(range(1, 13), 12))"` (from `spike/`)
