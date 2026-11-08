## Where code goes

Don't add features to `patch/navigation.c`: give each its own file, as CONTRIBUTING.md's [Where new code goes](CONTRIBUTING.md#where-new-code-goes) says. Add to navigation.c only where the code being changed already lives, and say why.

## Making a change

- Make the smallest complete change that fixes the problem shown, in the style of the code around it ([CONTRIBUTING.md](CONTRIBUTING.md)). Reuse the helpers already here, and add no abstraction, dependency, refactor or optimisation the change doesn't need.
- Keep existing behaviour unless the task is to change it.
- The Q2 is small: large libraries (tens of thousands of songs) and oversized tags have run demo out of memory. Library scans, indexing, Cover Flow and navigation are where that bites, so mind memory and the UI thread there.
- Card files, tags and network replies can be malformed or huge: check them and fail safely.
- A fix comes with a focused test that fails without it.
- Before committing, read the diff for risk, needless complexity and unintended changes. Get an independent review of a complex or risky change.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:

- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
