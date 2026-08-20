# Secret scanner fixtures

Secret-shaped values are constructed dynamically inside temporary Git repositories
by `tests/test_secrets_hygiene.py`. Keeping literal candidates out of this directory
ensures the repository can scan its own working tree without fixture exemptions.
