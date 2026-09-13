# Contributing

Thanks for helping improve Adobe Portfolio Scraper! Bug reports, fixes, and small focused improvements are all welcome.

## Reporting a problem

[Open an issue](https://github.com/Systyntono/adobe-portfolio-scraper/issues) and include:

- the command you ran, and its full output
- your Python version (`python --version`) and operating system
- what you expected to happen

If images are missing from a site, say which kind of layout they're in (single image, grid, side-by-side row, and so on). Only share site addresses you have permission to share.

## Making a change

1. Fork the repository and create a branch.
2. Set up: `pip install -r requirements.txt`
3. Make your change. Keep the scraper a single dependency-light file.
4. Add or update a test in `tests/` for any change in behaviour. The tests use HTML fixtures and must not need network access.
5. Run the tests: `python -m unittest discover -s tests -v`
6. Open a pull request that explains what changed and why.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE) and that the [Terms of Use](TERMS_OF_USE.md) apply to them.
