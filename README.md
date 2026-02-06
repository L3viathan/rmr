# rmr (remember-my-rejections)

Tame your formatter/linter by telling it which changes you like and having it
remember which ones you don't.

## Usage

First call your formatter/linter (a tool that changes the code in a
reproducible manner), but prepend `rmr.py`.

The tool will then make changes, and rmr will drop you into an interactive
`git add` session where for each hunk you have to say whether you like the
change (`y`) or not (`n`).

Afterwards, it will revert the changes you didn't like and remember them. When
calling the tool again with rmr, it will no longer ask you about those
changes, and it won't make them.
