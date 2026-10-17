# Metadata-only stall decisions

`xar_promo.stall_edl.build(config)` consumes recording segment metadata and
verified stop/progress endpoints supplied by a project adapter. It performs no
file, media, process, network or game I/O. It cannot infer an endpoint or approve
a rendered film. This is a library API; there is no new `xar-promo edl` command.

```python
from xar_promo.stall_edl import build

edl = build({
    "segments": [{"session_id": "capture-a",
                  "started_at_utc": "2026-10-17T00:00:00Z",
                  "duration_seconds": 900}],
    "intervals": [{"id": "wait-a",
                   "start_at_utc": "2026-10-17T00:00:10Z",
                   "end_at_utc": "2026-10-17T00:10:10Z"}],
})
assert edl["intervals"][0]["edited_middle_seconds"] == 4
```

The default mode is speedup: keep two seconds of the error head and two seconds
of the actual recovery tail, then compress only the recorded middle to at most
four seconds. Short waits remain unchanged. The speed factor has no arbitrary
cap; `atempo_factors` supplies bounded audio factors whose product is the
requested speed. Explicit `mode="cut"` is available, but is not the default.

Multiple recordings retain separate source offsets and report missing source
gaps without inventing footage. An open wait has no edit actions until an
adapter supplies its actual endpoint. Overlay and source provenance metadata
are retained. A late repair notice is a separate marker and cannot extend an
already closed waiting interval across normal footage.

Wallclock offsets are explicitly estimates. Before applying an edit, the
project must bind the exact EDL and recording sources to actual PTS boundaries,
verify the boundary pixels, obtain current rendering storage admission and
confirm that the source capture is closed. A recipe, filter plan or successful
encoder command is not human sign-off or publication.

The CK3 project owns its runtime-status decoder, immutable metadata collector,
recording launch hook and checked-in 2/2/4 preset. None of those project-specific
interfaces are imported here. No current recording or finished film was read
or rendered by this source/test package.
