Kerao's fork of libplacebo, which adds a Metal backend. Read `kerao/README.md` first: it holds the fork's rules, and every change follows them.

## Issues

Issues and specs live in `keraolabs/kerao`. Every `gh` call in this clone takes `--repo keraolabs/kerao`; without it, `gh` reads this fork or upstream instead.

The tickets for the Metal backend and its build are sub-issues of the spec keraolabs/kerao#18; read the spec alongside any of them. Comments can revise an issue's body, so read both:

```sh
gh issue view 18 --repo keraolabs/kerao --json title,body,comments --jq '.title, .body, .comments[].body'
```

## Code style

C follows the style of the surrounding upstream libplacebo code. Objective-C uses ARC.
