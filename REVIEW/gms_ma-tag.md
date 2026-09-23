# gms_ma/tag.py

## pattern_tags / _style <!-- ref:gms_ma/tag.py:63-134 -->
**Purpose**: Heuristic tags for a window: `key`, `mode`, `chord`, `harmony`, `energy` and up to three `style` labels.
**Why**: Deliberately coarse and overridable — manual tags beat heuristic ones everywhere via `store.tag_groups` source precedence (`manual > model > heuristic`).
**Data Flow**: `PatternMetrics, is_drums` → `[{kind, value, confidence}]`.
**Relationships**: Called by `indexer._persist_pattern`; surfaced as tag chips in the viewer/library.

### _style <!-- ref:gms_ma/tag.py:110-134 -->
**Purpose**: Derive up to three style labels (e.g. `steady-pulse`, `syncopated`, `legato-pad`, `chordal`, `arpeggiated`, `sparse`, `staccato`) from the metrics.
**Why**: The style rules are heuristic and intentionally broad — they provide a starting classification that users can override with manual tags. The `[:3]` truncation keeps the tag list manageable.
**Data Flow**: `PatternMetrics, is_drums` → `[str]` style labels with confidence scores.
**Relationships**: Called by `pattern_tags`; uses metrics like `onsets_per_bar`, `syncopation`, `mean_polyphony`, `legato_ratio`, `staccato_ratio`, `occupancy`, `pitch_range`.

### genre_for / emotion_for <!-- ref:gms_ma/tag.py:205-228 -->
**Purpose**: Song-level `genre` (instrument-family weights + tempo/drum modifiers) and a 2-D valence/arousal `emotion` label.
**Why**: Provides a starting classification without an ML stack; `genre_scores` is a weighted matrix so it is easy to tune.
**Data Flow**: `(program counts, has_drums, bpm)` → `(genre, conf)`; `(energy, bpm, is_major, centroid)` → `(valence, arousal, label)`.
**Relationships**: Used by `indexer.song_analysis`.

### genre_scores <!-- ref:gms_ma/tag.py:140-203 -->
**Purpose**: Compute genre scores from instrument family counts and tempo/drum modifiers.
**Why**: The weighted matrix approach is transparent and tunable — each instrument family contributes to genre scores based on its association with musical styles. Tempo and drum modifiers adjust the scores.
**Data Flow**: `program_counts: Counter, has_drums: bool, bpm: float` → `dict[str, float]` genre scores.
**Relationships**: Called by `genre_for`; uses `family()` to map GM program numbers to instrument families.

### tempo_class <!-- ref:gms_ma/tag.py:136-138 -->
**Purpose**: Classify BPM into five tempo classes.
**Why**: Simple threshold-based classification used for both song-level and pattern-level tags.
**Data Flow**: `bpm: float` → `str` ("very slow", "slow", "moderate", "fast", "very fast").
**Relationships**: Called by `indexer.song_analysis` and `tag.pattern_tags`.