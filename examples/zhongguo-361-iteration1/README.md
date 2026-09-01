# ZhongGuo 361 · iteration 1 capability demo

This small, offline demo is the first iteration of the ZhongGuo 361 promo
project.  It is intentionally a **legacy v1 manifest** plus a native authoring
projection so that the package can demonstrate both compatibility validation and
the new project contract without requiring CK3, FFmpeg, Edge TTS, or network
access.

The demo keeps three representative acceptance stills:

* calibration choices;
* the managed scoreboard;
* a superior-assigned 3.25 result.

The images are copied byte-for-byte from the parent repository's imported
acceptance fixture.  `provenance.json` records the original path, byte count,
and SHA-256 for every copy.  The source run was GREEN as an acceptance fixture,
but these stills are **fixture-live evidence, not a clean promotional recording**
and are not a release claim.  In particular, do not infer a complete gameplay
loop or a final video from this directory.

The three PNGs total 12,964,898 bytes:

| Local fixture | Bytes | SHA-256 |
|---|---:|---|
| `assets/calibration-event.png` | 4,693,773 | `8E1813D538BE9F95736D9F07EB88B7BCB719320D421EB37DFBCFCE649BD65AAB` |
| `assets/scoreboard-panel.png` | 3,225,633 | `4B1D7157D3B999F2041BD840DBDEC841B94E7B60F9AEF7AA8FD99011B0337EE7` |
| `assets/superior-result.png` | 5,045,492 | `93ECFAD072711B6CAA1759FEFA267F492897E36B8CE14905230541A7F7EAB46F` |

## Offline check

From the repository root, with no optional dependencies installed:

```powershell
py examples/zhongguo-361-iteration1/validate_demo.py
```

The same check can be run through the installed CLI:

```powershell
xar-promo validate examples/zhongguo-361-iteration1/promo-manifest.json --json
xar-promo validate examples/zhongguo-361-iteration1/promo-project.json --json
```

The first command validates the legacy manifest and its bound image bytes; the
second validates the native `ProjectConfig` projection.  Both are authoring/demo
documents and deliberately do not create a run, call a provider, or publish
media.

## Source mapping

The selected text is adapted from `mod_zhongguo_style/promo/promo-manifest.json`
at the first ZhongGuo 361 release line.  The full production authoring script,
capture runner, visual audit, and release gates remain in the parent project;
they are not copied here because they depend on CK3 fixtures and project-local
tools.
