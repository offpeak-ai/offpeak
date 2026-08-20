# Contributing

Thanks for helping build the deadline standard.

## Dev setup

```bash
git clone https://github.com/offpeak-ai/offpeak
cd offpeak
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

```bash
ruff check src tests
pytest
```

Both run in CI on every PR. Tests are network-free — venue drivers are tested against fakes; never add a test that needs an API key.

## What's welcome

- New venue drivers (implement `offpeak.Venue`; keep provider SDKs behind optional extras).
- Deadline-form and receipt improvements.
- Price-sheet corrections (cite the provider's public pricing page in the PR).

## Spec changes

[SPEC.md](SPEC.md) changes start as an issue before a PR, so semantics get discussed ahead of wording.
