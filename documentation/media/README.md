# README showcase media

The README combines conceptual hero artwork with actual Battle View footage.
The footage is an interrupted demonstration, not a completed-match result or a
gameplay-strength benchmark. Gameplay and graph footage are recorded together;
neither was composited from a different run or replaced with simulated activity.

## Source recording

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
- Animated preview: video seconds 18–26, 900×500, 6 fps, 96-color palette,
  2,593,242 bytes, silent loop.
- Audio check: mean −34.3 dB, peak −17.1 dB; non-silent game audio.
- Full MP4 decode passed; sampled frames checked the fitted game, moving graph,
  decisions and caster subject halo. The local README render loaded all images.
- The source run stopped normally at 236.34 game seconds, with no match error.
  Hosted Typesafe decisions and labeled fallbacks are both visible. This capture
  does not satisfy the remaining J2 gameplay acceptance gates.

Reproduce the video from the local raw capture:

```powershell
ffmpeg -ss 190 -i "data/jev/runs/63447b5fcb2f43eeb835f3f83370e7a7/battle/2026-10-11 16-51-25.mkv" -t 40 -vf "scale=1620:900:flags=lanczos" -c:v libx264 -preset medium -crf 24 -pix_fmt yuv420p -c:a aac -b:a 96k -movflags +faststart documentation/media/jev-battle-view.mp4
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
