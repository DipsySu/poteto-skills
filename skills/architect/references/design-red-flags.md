# Design red flags

Screen every candidate against this list before choosing. Assume the next contributor is an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. A flag counts when that agent would be led into a mistake.

1. **Two ways to do one thing.** An old path and a new path both work. The agent copies whichever it saw first.
2. **State with more than one writer.** Two modules write the same file, table, or field. One owner per piece of state.
3. **Hand-synced lists.** Adding a case means editing N places (a registry, a switch, docs, tests) with nothing failing when one is missed.
4. **Stringly-typed states.** Status, kind, or mode are free strings, so an illegal state or a typo compiles.
5. **Escape hatches to compile.** The design needs `any`, casts, `# type: ignore`, or optional fields that are always set in practice.
6. **Callers must know internal rules.** Call order, locking, init steps, or a fold before reading. If a caller can get it wrong, one will.
7. **Shallow module.** The interface is as large as the implementation, or a layer only passes calls through.
8. **Information leakage.** One decision (a file format, path, wire shape, or retry policy) is encoded in two modules.
9. **Flag arguments.** A boolean parameter switches between two behaviors that should be two functions or two types.
10. **Hidden I/O or global state.** Constructors that read files, module-level mutable singletons, implicit environment lookups deep in the call graph.
11. **Silent failure.** Catch-all handlers, default values that mask a missing input, retries that hide a bug.
12. **Test-only seams in production APIs.** Parameters or setters that exist only so tests can reach in.
13. **Misleading nearest example.** The file an agent would copy demonstrates the pattern you are trying to remove. Delete or fix it in the same change.
14. **Non-local correctness.** A change that looks right inside one file is wrong for the repo because an invariant lives elsewhere.

Prefer the candidate with fewer flags and the smaller public surface that hides more. When two candidates tie, pick the one whose mistakes fail at compile or lint time instead of in review.
