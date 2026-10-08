# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `main`.
2. If you've changed something, update the documentation and add an entry under
   `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md).
3. Make sure your code lints (using `pre-commit`, see below).
4. Test you contribution.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People _love_ thorough bug reports. I'm not even kidding.

## Development setup

The project uses [Poetry](https://python-poetry.org). Install the dependencies and the
`pre-commit` hooks with:

```console
$ poetry install
$ poetry run pre-commit install
```

A [dev container](.devcontainer) for Visual Studio Code is included as well; it runs
these commands automatically.

## Use a Consistent Coding Style

Python code is linted and formatted with [Ruff](https://docs.astral.sh/ruff/),
other files with [prettier](https://prettier.io/). The `pre-commit` hooks run both,
plus a spell check and the tests, every time you commit.

To run all checks on all files:

```console
$ poetry run pre-commit run --all-files
```

To run Ruff on its own:

```console
$ poetry run ruff check --fix .
$ poetry run ruff format .
```

## Test your code modification

Run the tests with:

```console
$ poetry run pytest
```

Please add tests for new features and bug fixes.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
