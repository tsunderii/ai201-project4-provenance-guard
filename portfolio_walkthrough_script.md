# Portfolio Walkthrough Script

Hi, this is Provenance Guard, a small Flask app for estimating whether creative writing looks more likely to be human-written or AI-generated.

The main idea behind the project is to be cautious rather than overconfident. Instead of just giving a binary verdict, the app returns a confidence score, a transparency label, and a structured audit trail so a human reviewer can understand what happened.

To show it working, I’ll submit some text through the API and walk through the response. The app runs two signals: a Groq-based language model signal and a stylometric heuristic signal that looks at structural features like sentence length, punctuation, and repetition.

Those two signals are combined into a single confidence score, and the app maps that score to one of three labels: likely AI, likely human, or uncertain. I think that uncertainty is really important here because false positives are harmful on a creative platform.

I also built an appeals workflow, so after a submission is classified, a creator can submit an appeal with reasoning and the content is marked as under review. The app also writes entries to an audit log so the original decision and the appeal live in the same record.

For the bonus features, I added a simple analytics endpoint and a basic provenance certificate flow for verified creators, plus support for non-text content types by passing a content type field into the submission payload.

So overall, the project is really about making AI detection feel more responsible and transparent. It’s not trying to be perfect; it’s trying to be explainable, cautious, and useful in a real product setting.
