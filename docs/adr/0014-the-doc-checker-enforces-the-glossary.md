# The doc checker enforces the glossary

`GLOSSARY.md` names each concept once, and the doc checker reports a phrase the glossary lists under _Avoid_ when an agent doc uses it, naming the term to write; a quoted phrase passes, and a bare word that is also another entry's term is never refused. We chose this over a prose rule alone, because the vocabulary drifted without a check: one word came to name both a body of work and the model's reasoning effort, and two words named the app that runs a session.
