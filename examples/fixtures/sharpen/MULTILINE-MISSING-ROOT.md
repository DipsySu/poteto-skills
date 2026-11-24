# Brief: stop todos getting lost Use a quick fix

## In my own words

Stop losing todos when adds overlap.

## How a top expert would approach it

Read todo/store.py and reproduce overlapping writes before choosing a change.

## Specification check

- Over-specified: quick fix
- Under-specified: none

## Outside check

- Command or link: `python3 -m unittest -q`
- Pass condition: concurrent adds retain both todos.
