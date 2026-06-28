# Portfolio Walkthrough

This project is a cautious AI-text attribution service built with Flask. The core idea is not to pretend the system can make perfect judgments, but to surface uncertainty clearly and give creators a fair review path.

In the walkthrough, I would explain that the app receives text through `/submit`, runs two independent signals, and combines them into a single confidence score. One signal is a Groq-backed LLM evaluation, and the other is a stylometric heuristic that looks at sentence structure, vocabulary diversity, punctuation, and repetition. The combined score is mapped to a transparent label that can say likely AI, likely human, or uncertain.

I would then highlight the production layer: the app supports appeals through `/appeal`, updates the item to `under_review`, and writes structured audit entries to `audit_log.jsonl`. That means the system is not just returning a score; it is preserving decision context and review history. I would also mention the rate limit on `/submit` and the automated tests that verify the full workflow end to end.

The overall story is that this project balances technical detection logic with product-minded design: it is useful, explainable, and cautious rather than overconfident.
