# README showcase media

The README combines conceptual hero artwork with actual Battle View footage.
The MP4 is an interrupted demonstration; the GIF has a newer source described
below. Neither asset is a gameplay-strength benchmark. Gameplay and graph footage are recorded together;
neither was composited from a different run or replaced with simulated activity.

## Animated preview refresh — 2026-10-15

`../images/jev-battle-preview.gif` now shows scripted Jev against **Harder Terran**
in run `c7a98527acab4cd7a447e57f23207af4`, Simple64, seed11. Result: loss at
586.607143 game seconds (last metric sample586.428571), no match error, zero model
calls. This uses the **unaccepted first recovery-repair candidate**, frozen in
`.build-step/jev-order-repair/review-r4/`; it does not validate later repairs or
claim stronger play. It contains no debug units or injected decisions.

Source recording `battle/2026-10-15 07-55-53.mkv`, seconds576–584, 8-second silent
loop,1440×800,6fps,96colors. Full combined game/graph, Follow decisions and changing
node details remain visible. The normal match had no game-time limit; the private
developer harness retained a1200-second wall limit. It finished naturally.

[Watch the controlled recovery comparison and harder-game excerpt](../jev-recovery-comparison.html).
The before/after recovery scenes are separately labeled LOCAL MOCK/DEBUG; the GIF
is ordinary scripted gameplay. [Evidence and review status](../investigations/jev-order-repair-2026-10-15.md).

## Previous animated preview — 2026-10-14

The preceding preview used real Observer Rush footage from
run `525a289465684dbb830f0abc05d88eb5`: **scripted Jev v2 versus Hard Terran**,
Simple64, seed 11, native real time, no game/wall timer, no paid model calls.
The match completed naturally with a win at approximately 6:18 game time and
no match error; disk/API validation passed.
The current checkout was `c0836c3` plus the SC2 ability compatibility fix and
the operator's documentation reorganization. This is not the held JDI candidate.

The full game/graph layout remains visible with **Follow decisions: On** and
changing node inspection details. Source seconds 202–210; 8 seconds, 1440×800,
6 fps, 48 frames, 96 colors, 6,098,168 bytes, silent loop. Full GIF decode passed;
sampled frames show the inspector changing from reserve handling to army attack.
Raw recording: `data/jev/runs/525a289465684dbb830f0abc05d88eb5/battle/2026-10-14 13-30-08.mkv`.
The MP4 linked by the preview retains its older source below.

```powershell
ffmpeg -ss 202 -i "data/jev/runs/525a289465684dbb830f0abc05d88eb5/battle/2026-10-14 13-30-08.mkv" -t 8 -filter_complex "fps=6,scale=1440:800:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" -loop 0 -y docs/images/jev-battle-preview.gif
```

The compatibility diagnosis, test results and match outcomes are recorded in
[the refresh report](../investigations/jev-observer-refresh-2026-10-14.md).

## Original MP4 source recording

- Run: `63447b5fcb2f43eeb835f3f83370e7a7`, recorded 2026-10-11.
- Code at launch: `ba8f8a7`, branch `master-plan/phase-ev`.
- Player: Jev v2; Typesafe `jev-1.13.0`, 80-request budget for this capture.
- Opponent: Blizzard Medium Terran, Simple64, seed 11, real-time match.
- Caster: local Ollama `qwen2.5:1.5b`, including explicitly labeled local fallback.
- Source: OBS capture of the combined 2160×1200 window, game-process audio only.
- Raw recording, trace and replay: `data/jev/runs/<run_id>/` on the capture
  workstation. Raw captures are deliberately not committed.

`jev-battle-view.mp4` is the short, compressed README clip. The animated preview
in `../images/jev-battle-preview.gif` links to it. Both keep the full combined
layout visible. The committed MP4 is an optimized showcase asset, not a raw capture.

### Final edit and checks

- Video: source seconds 190–230, unchanged playback speed; 40.0 seconds,
  H.264/AAC, 1620×900, 30 fps, 6,373,330 bytes. No added narration or music.
- Original animated preview (replaced above): video seconds 18–26, 900×500, 6 fps, 96-color palette,
  2,593,242 bytes, silent loop.
- Audio check: mean −34.3 dB, peak −17.1 dB; non-silent game audio.
- Full MP4 decode passed; sampled frames checked the fitted game, moving graph,
  decisions and caster subject halo. The local README render loaded all images.
- The source run stopped normally at 236.34 game seconds, with no match error.
  Hosted Typesafe decisions and labeled fallbacks are both visible. This capture
  does not satisfy the remaining J2 gameplay acceptance gates.

Reproduce the video from the local raw capture:

```powershell
ffmpeg -ss 190 -i "data/jev/runs/63447b5fcb2f43eeb835f3f83370e7a7/battle/2026-10-11 16-51-25.mkv" -t 40 -vf "scale=1620:900:flags=lanczos" -c:v libx264 -preset medium -crf 24 -pix_fmt yuv420p -c:a aac -b:a 96k -movflags +faststart docs/media/jev-battle-view.mp4
```

## Hero graphic

`../images/alpha4gate-hero.png` is conceptual artwork generated with the built-in
imagegen tool. It is not a screenshot or a claim about the UI. Original resolution:
2172×724. The original generated PNG is preserved without further image editing.

Final generation prompt:

> Use case: ads-marketing. Create a polished wide GitHub README hero banner for
> Alpha4Gate, a StarCraft II AI project about playing, seeing decisions, and evolving
> bots. Aspect ratio 3:1, dark charcoal navy background, restrained cyan, violet, and
> warm gold highlights matching a dark engineering dashboard. Illustration: four
> elegant gold alien warp gateways with blue energy on a rocky sci-fi battlefield
> on the left, transitioning through fine glowing lines into a beautiful
> rotating-looking geometric sphere of colored decision graph nodes on the right.
> At the center, generous dark negative space and extremely clear bold modern
> typography reading exactly 'Alpha4Gate', with smaller subtitle exactly 'Play.
> Observe. Evolve.' No other text. Crisp editorial sci-fi art, refined rather than
> busy, subtly luminous, premium open-source software cover. This is conceptual
> artwork, not a fake screenshot: do not draw UI panels, logos of other companies,
> status numbers, or fake metrics. Keep all important elements within comfortable
> margins.
