"""The fact-check demo: claims, then verdicts with cited fixture sources, on synthetic transcripts.

It runs on Qwen/Qwen3-4B, not pinned, with no bake-off behind it. Its verdicts are not measured and carry no D6
standing.

- `pipeline`: the fixtures, the frozen prompts, extraction, the mapping rule and the probe calls.
- `render`: the static HTML page and the JSON record.
- `__main__`: the live command line, which launches the server through `launch.LaunchedServer`.

The demo reuses the harness: `client`, `extraction`, `probe` and `schemas` for the calls, `inputs` and `frozen` for the
frozen prompts and the request forms, `launch` for the server, and `records` for the record's form and new-file
writes. It imports nothing from `app.insights`, and imports directly none of `gold`, `gates`, `report`, `entry`, `cli`,
`wrapper`, `runner` or `machine`. The H5a loader it reuses, `frozen` with `inputs`, does load the code of `gold`,
`gates`, `runner` and their helpers, but the demo calls no gold loader. Only the harness client talks to the network,
and only to http://127.0.0.1:<port>. There is no retrieval and no provider or web call. The fixtures under
`fixtures/` were invented for the demo, kept apart from the H5 dev set, and hold no gold or probe text.
"""
