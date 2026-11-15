# Q2 wheel: interaction research and design proposals

Research date: 13 November 2026. This is a design exploration, not an implementation specification or a claim of world-first invention. Product behavior below comes from manufacturers' documentation and original research; the proposed Q2 experiences and feasibility judgments are my synthesis. No hands-on comparative testing was performed.

The strongest direction is to make rotation express **scale, musical time, and listening intent**. Q2 Pod already uses the wheel well for selection and adjustment. Its next distinctive experiences should let someone explore an album as a continuous object, shape the next half hour of listening, and move confidently through a large collection.

## What the Q2 actually offers

- A 2.09-inch touchscreen with a 320 × 375 panel, a touch wheel, Centre, and four surrounding buttons; 128 MB RAM and no swap. See [hardware](hardware.md).
- The input reader receives an absolute wheel coordinate with a nominal 200-unit wrap, and a field used as contact/no contact. The first contact anchors movement; subsequent samples yield signed travel. This supports relative turns, direction changes, speed estimation, and potentially broad absolute sectors. It does **not** establish force sensing, radial finger position, multitouch, or 200 equally accurate physical targets. See [wheel_sample](../patch/playback.c).
- Existing raw travel consumers include Coverflow and lyrics. Ordinary lists accelerate by the duration of a sustained run; Coverflow already uses measured rate, continuous movement, reversal handling, contact debounce, and settling. Merely adding acceleration, inertia, or snapping would not be new. See [wheel movement](internals.md#wheel-movement) and [Coverflow thumb](internals.md#coverflow-thumb).
- Existing experiences also include wheel scrubbing, a wheel-operated rating picker, Era browsing, queue actions, song-duration caching, and EQ. Proposals below distinguish the new behavior from these foundations. See [scrubbing](internals.md#scrub-ipod), [ratings](internals.md#ratings), [Era](internals.md#era), [durations](internals.md#song-durations), and [queue handling](internals.md#queue-menu).
- Centre double-press, Centre hold, Play/Pause hold, and Return hold already have jobs. New features should start through visible context actions and retain these conventions. A button chord should earn its place through ergonomic testing rather than serve as the default entry. See [controls](../README.md#controls) and [Centre](internals.md#centre-button).
- The input documentation records lost releases during concurrent wheel and touchscreen use. A still thumb can also send no frames for seconds. Screen-plus-wheel chords and commands committed on inferred finger lift are therefore poor starting points. See [input internals](internals.md#hooks).
- No controllable haptic actuator or motorized wheel is established by the hardware inventory. Visual snapping is available; simulated physical resistance is not. The buzzer is not a substitute for a haptic motor and is suppressed while listening through supported outputs.

## What other interfaces teach us

The observations are documented behavior. The last column is a design inference, not a measured transfer of usability benefits to the Q2.

| Reference                                                                                                                                            | Actual mechanism and experience                                                                                                                                             | Useful Q2 lesson                                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [iPod classic, 120 GB guide](https://cdsassets.apple.com/live/6GJYWVAV/user/ma630_ipod_classic_120gb_en.pdf), pp. 6–8                                | Fast circular movement exposes alphabetical navigation; lifting returns to ordinary scrolling. Cover Flow uses the same wheel and Centre.                                   | A wheel can traverse different levels of a collection, with an understandable return to precision. An alphabet overlay alone is established prior art. |
| [Apple Watch Digital Crown](https://developer.apple.com/design/human-interface-guidelines/digital-crown)                                             | Rotation navigates lists/pages and can inspect data such as time. Apple calls for speed-matched visual feedback and touch alternatives; crown presses belong to the system. | Keep the screen readable and make the active interpretation visible. Preserve system button conventions. Do not assume Q2 can reproduce crown haptics. |
| [Samsung Galaxy Watch bezels](https://www.samsung.com/uk/support/mobile-devices/how-to-use-the-bezel-of-the-galaxy-watch/)                           | Physical and touch bezels navigate tiles and adjust values. Some contextual actions use opposite rotation directions.                                                       | Rotation can traverse a sequence of useful views, not only rows. Directional actions need an unambiguous context.                                      |
| [Surface Dial](https://learn.microsoft.com/en-us/windows/uwp/ui-input/windows-wheel-interactions)                                                    | Hold exposes a tool menu; rotation selects a tool or adjusts its value; click has a context-dependent action.                                                               | Separate choosing what the wheel controls from operating it. Avoid a menu that silently steals volume.                                                 |
| [Logitech MX Master 3](https://support.logi.com/hc/en-us/articles/360035271133-Getting-Started-MX-Master-3)                                          | SmartShift changes between ratcheted and free spinning modes with speed; manual switching is also available.                                                                | Fast travel and precise landing can be different gears. A visible/manual way to select a gear helps users who cannot reliably make fast movements.     |
| [DaVinci Resolve Speed Editor](https://www.blackmagicdesign.com/products/davinciresolve/keyboard)                                                    | A search dial has jog, shuttle, and scroll modes. Source Tape joins clips into one browsable sequence; trim actions give the dial another clear job.                        | Treat an album or upcoming queue as one timeline spanning files. One-dimensional control works well when the controlled object is explicit.            |
| [Pioneer DJ CDJ controls](https://blog.pioneerdj.com/djtips/what-do-all-of-these-buttons-do/)                                                        | Slip mode lets a temporary performance return to the underlying advancing play position. Quantization uses previously analyzed beat grids.                                  | Explore temporary musical excursions with a clear return point. Beat-aware claims require actual beat data; a waveform or duration is insufficient.    |
| [Playdate design guidance](https://help.play.date/developer/designing-for-playdate/) and [Crankin's Time Travel Adventure](https://uvula.jp/crankin) | The crank can directly advance or reverse a character's timeline. Panic emphasizes physical comfort, hardware tuning, and alternatives to crank-only operation.             | Let rotation manipulate a meaningful process, such as composing a listening session. Preserve a way to perform the task without a dexterous gesture.   |
| [Nest thermostat](https://support.google.com/googlehome/answer/9243193)                                                                              | Turning adjusts a prominent target; pressing enters a menu where turn and press select options.                                                                             | A single legible goal can make a dial satisfying: “Give me 35 minutes of music.”                                                                       |
| [OP-1 tape mode](https://teenage.engineering/guides/op-1/original/tape-mode)                                                                         | Encoders scrub tape and manipulate loops and tape parameters.                                                                                                               | Album and excerpt controls can feel like handling recorded material. Their useful information must survive the tape metaphor.                          |
| [OB-4](https://teenage.engineering/guides/ob-4)                                                                                                      | A virtual two-hour tape records playback, and the tape dial can rewind it; Play returns to real time.                                                                       | Listening history can become navigable musical time. On Q2, reopening local files is a much smaller starting point than recording every source.        |
| [BeoSound 5](https://bangolufsenassistentgohe.blob.core.windows.net/manuals/SOUND_SYSTEMS/BEOSOUND_5/beosound5_gettingstarted_english.pdf), pp. 6–10 | Browser and player modes separate exploration from playback. Its pointer and wheel have distinct roles; a selected song can seed a MOTS queue.                              | Browsing related music should preserve what is playing. The Q2 has fewer independent controls, so do not copy the whole control arrangement.           |
| [SmartKnob](https://github.com/scottbez1/smartknob)                                                                                                  | A motor and magnetic encoder produce software-defined detents and end stops.                                                                                                | Meaningful boundaries matter. Q2 can show and snap to them, but copying their physical sensation needs different hardware.                             |

Two research ideas are particularly relevant:

- [Igarashi and Hinckley's speed-dependent automatic zooming](https://www.microsoft.com/en-us/research/video/speed-dependent-automatic-zooming-browsing-large-documents-2/) links rapid travel to a broader overview, reducing unreadable visual motion. This motivates Library Atlas. It does not establish that the technique will outperform the current Q2 browser.
- [Kurtenbach and Buxton's marking-menu study](https://billbuxton.com/MMUserLearn.html) connects visible menu use to practiced directional gestures. This motivates stable, learnable destinations. A one-dimensional touch ring cannot perform the paper's arbitrary two-dimensional pen strokes, so the Q2 adaptation needs its own testing.

## Ten proposed experiences

### 1. Library Atlas — change the scale of the collection

**Experience.** At a relaxed pace, browse individual albums. A sustained fast turn expands the view into labeled groups. Slowing down opens the neighborhood around the current group, with the selected album retained as an anchor. You can travel from the whole collection to an album without watching hundreds of covers flash past.

**Concrete flow.** Open Atlas from Coverflow's context action. With Artist sort, the scales are albums → artists → initial groups. With Year sort, use albums → years → decades. A permanent scale label says what one step means. Centre on a group opens it; Centre on an album opens its tracks. Return climbs a scale and ultimately restores the original browser position.

**New contribution.** The representation and unit of navigation change together. This extends the existing Coverflow movement rather than selling acceleration or an alphabet overlay as new.

**Visual character.** Covers compress into labeled bundles as the view broadens. Keep one central reference and a small breadcrumb; avoid a tiny galaxy of illegible album covers.

**Implementation boundary.** Reuse the current sort and grouping identities, raw wheel path, and cover cache. Groups must not change under the thumb. Use separate entry/exit speed thresholds and a short stability window, calibrated on hardware. Offer explicit scale selection through the same visible control for people who prefer it. Missing years form an explicit Unknown group; names need the existing locale-aware ordering, not an English-only A–Z assumption.

**Main risk.** Automatic zoom can feel like losing control. If users cannot predict where slowing down will land, retain manual scale changes and abandon automatic switching.

### 2. Album Reel — browse the entire record as one piece of time

**Experience.** An album becomes a continuous strip with track boundaries. Turning the wheel moves a silent playhead across the record, including across files. The screen identifies the track and its local timestamp under the cursor.

**Concrete flow.** Choose Album Reel from an album's menu. At overview scale, turn between track regions; Centre opens a selected region for finer positioning. In that detailed view, turn to a position and Centre plays there. Return backs out without seeking. The original playing location remains marked while exploring.

**New contribution.** Current scrubbing moves within a track. This gives the whole album a continuous geography, using the Source Tape idea from editing consoles. It is especially appealing for live albums, mixes, classical works, and CUE-based releases.

**Visual character.** The current cover anchors one end; track segments occupy proportional time. A thin second marker distinguishes the playing position from the proposed destination. A textual track list remains an alternative for very short segments.

**Implementation boundary.** Resolve album ordering and CUE identities through existing helpers; use the duration cache. If some lengths are unknown, offer track-step browsing with an explicit “length unavailable” indication, not invented proportional lengths. Only one seek occurs after confirmation. A same-album seek should preserve the surrounding queue where possible; starting another album must be visibly identified as a new play action.

**Main risk.** File boundaries, decoder seek delays, and unknown lengths can undermine the continuous metaphor. No promise of audible vinyl scratching, seamless cross-file seeking, or a second preview decoder.

### 3. Queue Splice — put this song where you want to hear it

**Experience.** Select a song, then turn the wheel to carry its card through the upcoming queue. Gaps say “Next,” “After 2 songs,” or “In about 12 min.” Centre drops it into that gap.

**Concrete flow.** The song's existing context menu gains Place in Queue. The playing item stays pinned. Rotation moves the insertion cursor among future boundaries; the chosen song stays attached to it. Centre inserts one occurrence; Return cancels. A visible Undo restores that insertion if the relevant queue state still permits it.

**New contribution.** Play Next and Add to Queue become a continuous range of intent. This is useful when a song belongs later in the evening, not immediately or at the very end.

**Visual character.** Three large song cards and one insertion seam are enough. An estimated time beside the seam makes placement musical rather than administrative.

**Implementation boundary.** Start with ordered local queues. Arbitrary insertion into actual future shuffle order is a separate capability; do not confuse stored list order with playback order. Revalidate occurrence identity and queue generation on commit, preserve the current track, and update gapless/preload bookkeeping through the queue owner. Timing is approximate and requires known lengths; otherwise show song counts.

**Main risk.** The queue can advance or change while the placement is open. On a changed target, refresh the preview and require a new confirmation instead of silently inserting elsewhere.

### 4. Wind a Session — turn available time into music

**Experience.** Open a playlist, album, or current queue and choose Wind a Session. Turn clockwise to wind more listening time onto a reel. Track cards accumulate, and an endpoint moves to the last complete track that fits.

**Concrete flow.** Turn to a target of 30 minutes. The screen might say “28:42 · 7 songs · ends with Night Window.” Centre starts that bounded session; Return leaves playback untouched. The default is to finish the selected last track. An exact cutoff, if offered later, must be a separately labeled behavior.

**New contribution.** The existing sleep timer controls when playback stops. This control previews and chooses the musical content that will fill the time, including its final song.

**Visual character.** A winding arc fills around a large duration, with the final song prominent. Turning backward removes songs from the end of the same draft; it never reshuffles everything.

**Implementation boundary.** The smallest useful version takes a prefix of a selected ordered source with cached durations. It does not need subset optimization or an AI playlist engine. Unknown durations prevent a credible budget and need a visible fallback. Show that confirmation starts a new bounded queue; save a temporary return point to the previous session if adding Undo.

**Main risk.** Crossfade, playback-speed changes, skipped tracks, and pauses affect the wall-clock finish. Present estimated music duration, not a guaranteed arrival time. Reuse the current timer only if its endpoint semantics match.

### 5. Listening Reel — rewind the evening

**Experience.** Turn backward through actual listening events: the album before dinner, the song interrupted halfway through, the track heard before switching playlists. Album art and “42 min ago” provide memory cues.

**Concrete flow.** Open Listening Reel from Now Playing's menu. Rotate backward into history; clockwise returns toward the present. Centre resumes a chosen event at its recorded position. Before the jump, preserve a Return to Present anchor so a detour does not strand the listener.

**New contribution.** Recent items and play counts describe tracks. This describes the sequence of listening episodes, including partial listens and repeated occurrences of the same track.

**Implementation boundary.** Begin with a bounded local-file event journal containing track identity, position, and session order. Keep it local and clearable. Reopen files; do not claim OB-4-style recorded radio or streaming rewind. Missing files remain identifiable but cannot be resumed. Cross-session wall-clock labels depend on a valid device clock.

**Main risk.** Current playback history is not automatically a durable event journal. Restoring the correct queue context, not just the right file, is the difficult part.

### 6. Wander Dial — choose how far from familiar to venture

**Experience.** A dial runs from Familiar through Overlooked to Unplayed. As it turns, the upcoming handful of songs changes, with small explanations such as “same artist, unplayed album” or “high rating, rarely played.”

**Concrete flow.** Choose Wander from a selected artist, genre, or playlist. Turn to set how much of the proposed queue comes from less-played eligible material. Inspect the first few tracks, then Centre starts it. Return cancels. Turning back restores the previous preview rather than drawing a new random result.

**New contribution.** The wheel controls a listening preference rather than choosing a song. Visible candidates make the consequence understandable.

**Implementation boundary.** Start with existing metadata, ratings, and play counts inside a declared source. Generate a stable candidate order once per visit and change the proportion drawn from each pool. “Unplayed” means no recorded plays on this Q2, not never heard in the user's life. Do not label tracks with inferred energy, mood, or sonic similarity without a real source for those attributes.

**Main risk.** Sparse metadata and new-device histories can make the control uninformative. Show the available pool sizes and gracefully reduce to an ordinary shuffle when there is no meaningful distinction.

### 7. Phrase Lens — turn through words instead of seconds

**Experience.** On a timed-lyrics screen, enter Phrase Lens. The wheel moves a focused lyric line; the proposed playback destination follows the line's timestamp. For speech with suitable timed metadata, the same interaction could select passages.

**Concrete flow.** Open the mode through a visible lyrics action. Turn to a line; Centre seeks there. Return exits without seeking. A further explicit action can mark a start and end line for a repeated passage, with the interval always visible.

**New contribution.** Q2 already scrolls lyrics. This turns the text into an addressable structure for seeking and repeat listening.

**Implementation boundary.** Use actual LRC timestamps. Unsynchronized lyrics remain scrollable but have no seek cursor. Repeated choruses are separate timestamped occurrences. Do not infer verse/chorus boundaries or claim word-accurate seeking from line timestamps. A/B repeat requires separate decoder and loop-boundary validation, so line seeking is the first version.

**Main risk.** Lyrics from a different recording or inaccurate timing can jump to the wrong phrase. Show the destination timestamp and keep ordinary time scrubbing available.

### 8. Moment Pins — bookmark the part you love

**Experience.** Save a musical moment, then use the wheel to browse a small set of timestamped pins. A pin might be a solo, a spoken quote, a movement entrance, or the point where a mix changes character.

**Concrete flow.** Choose Pin this Moment from the playing track's menu. Adjust its position with the wheel, then Centre saves it. Open Moments later to browse pins; Centre returns to one. Naming is optional: album art, song title, and timestamp already identify the memory.

**New contribution.** Ratings attach sentiment to a whole song. Moment Pins preserve a specific passage and create useful landmarks for Album Reel and Phrase Lens.

**Implementation boundary.** Store file identity and track-relative position including CUE identity. This needs no audio extraction. A four-slot visual compass is a possible compact view; absolute ring-sector selection is an experiment requiring orientation and accuracy checks, not a core dependency.

**Main risk.** Replaced or retagged files can invalidate pins. Keep a missing pin visible rather than jumping to an unrelated replacement. Direct quarter-ring access must not steal ordinary volume or scrolling gestures.

### 9. Sound Loupe — inspect an EQ change by turning through it

**Experience.** Select one editable tone change. The wheel progressively moves from the saved setting toward the proposed change while a curve and numeric amount remain visible. Returning to the original marker makes it easy to hear what changed.

**Concrete flow.** Enter Compare from an EQ edit. Turn within the bounded comparison range; Centre keeps the chosen setting, Return restores the saved one. Apply an appropriate shared attenuation across the comparison to reduce loudness bias, while acknowledging that equal peak headroom does not guarantee equal perceived loudness.

**New contribution.** This is continuous, reversible audition of one controlled change. Ordinary wheel EQ editing and level-aware 3EQ bypass already exist in the project.

**Implementation boundary.** Begin with one gain or a small, specifically designed tone macro. Reuse the existing EQ update ramp and headroom machinery. Arbitrary interpolation between unrelated filter types, frequencies, Q values, and presets is not a safe generic shortcut.

**Main risk.** Clicking, clipping, and loudness differences can make the comparison misleading. This needs audio tests and listening checks before being recommended for normal use.

### 10. Orbit — four stable doors into the current music

**Experience.** A compact ring around the current cover offers Album, Artist, Queue, and Moments. Rotation highlights one door; Centre opens it. Return restores the same orbit and current track context.

**Concrete flow.** Enter Orbit through a visible Now Playing action. The four destinations never reorder according to frequency. A practiced user can make the same small movement every time, while a newcomer sees the destinations.

**New contribution.** The context remains anchored to the same piece of music while the user traverses related views. The radial menu itself is established prior art; stable musical context and remembered destinations are the useful Q2 combination.

**Implementation boundary.** Start with relative turns and explicit confirmation. Test direct selection by broad touch-down sector only as a later accelerator. Ring input does not provide the radial distance needed for swipes toward and away from the centre.

**Main risk.** Four large list rows might be faster and clearer. Keep Orbit only if its spatial memory advantage appears in repeated-use testing.

## Four deliberately more experimental directions

**Crate stamps.** In an explicit queue-building session, an album is a sleeve and a short clockwise movement adds a copy to a draft crate; counterclockwise undoes the last addition. A separate selection phase chooses the next album, so reversal during normal browsing never becomes an edit. Centre reviews the draft before playback. The potential is an enjoyable collection ritual; the concern is adding a gesture that a conventional checkbox already solves.

**A destination song.** Choose the song you want to end with, then wind backward to select how much lead-in to build before it. Start with a declared playlist and a deterministic sequence; later metadata-aware sequencing could try to make the route coherent. This makes Session Wind about arriving at a musical destination. It requires new queue-generation behavior, and is more speculative than selecting a prefix.

**A musical excursion with a return tether.** Save the current track, position, queue, and state; briefly explore a different passage or saved moment; Return restores the anchor. The inspiration is DJ slip, but a Q2 prototype should restore a frozen bookmark. Continuing an inaudible second playback timeline, recording streams, and beat-perfect re-entry are substantially different audio work. Do not imply they are available.

**Cover Memory.** “I cannot remember the artist, but the sleeve was mostly orange.” Turn through a color spectrum while a small set of matching covers fans out; Centre freezes that color neighborhood and the wheel then selects an album. Return restores the color position. A second explicit group handles monochrome covers. Derive a compact palette from already cached thumbnails off the input path, keep candidates stable while turning, and show the actual covers as the result. The unusual value is searching a remembered visual quality without typing. Multicolor art, missing covers, and color-vision differences limit it, so it belongs beside ordinary browsing, not in its place. This is a proposed visual index, not semantic image recognition.

## A consistent interaction contract

1. Rotation previews, traverses, or adjusts the visible object. Centre confirms or opens the highlighted object; Return backs out or restores the pre-edit state. Where a feature has two stages, label the stage and Centre's next action.
2. New modes enter through named, discoverable actions. Keep existing screen-off, shutdown, transport, and hold behavior. Prototype these modes locally before introducing global gestures.
3. Finger lift must never by itself play a different song, alter a queue, or save a setting. Use lift for settling a visual position only when reliably observed.
4. Stop speculative animation when the user reverses or cancels. Do not carry a previous page's movement into an editor, and do not let a mode retain wheel ownership after its page closes.
5. Keep normal volume behavior outside these explicit modes. All screen-off operation must follow existing lock and volume guard rules.
6. Use visible semantic boundaries: tracks, albums, phrases, or insertion gaps. Do not call visual snapping physical feedback.
7. Provide a touch or simple step alternative. Use text and position as well as color, retain readable titles, and support reduced motion without losing the hierarchy.
8. Retain the user's Wheel Sensitivity meaning. Calibrate velocity thresholds across its supported range rather than tuning at one default.
9. Cache before turning. No whole-library scan, synchronous network request, or repeated decoder seek in the motion path.

## What I would prototype first

| Candidate      | Why first                                                                        | Main work beyond UI                                               | Advancement criterion                                                                             |
| -------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Wind a Session | Most distinctive everyday ritual; obvious benefit for walks, breaks, and bedtime | Bounded queue and credible duration/end behavior                  | People understand the final song and duration before starting; turning back preserves their draft |
| Queue Splice   | Concrete improvement to a frequent music task                                    | Arbitrary future insertion with correct queue/preload bookkeeping | Correct placement without restarting current playback or silently changing shuffle behavior       |
| Library Atlas  | Largest potential payoff for large collections                                   | Stable grouping and a predictable scale transition                | Faster target finding than current Coverflow without more overshoots or loss of position          |
| Album Reel     | Strong fit between circular movement and musical time                            | Duration completeness, cross-file addressing, seek semantics      | People reach a known part of an album more reliably than through track selection and scrub        |
| Phrase Lens    | Useful smaller feature with a clear starting point                               | Binding lyric selection to timestamps                             | Correct destinations on known-good timed lyrics; plain lyrics never imply seekability             |

These are qualitative priorities, not measured performance scores or effort estimates. Atlas and Album Reel can share ideas about scale without requiring one large generalized navigation framework.

## Validation before firmware integration

Use the current UI as the baseline. Ask people to find a known album in a large collection; place a song after two upcoming songs; build roughly half an hour ending at a complete track; and reach a known lyric or passage. Observe completion time, wrong commits, corrective reversals, lost context, and whether they can explain what the wheel currently controls.

Use both familiar Q2 users and people who have not memorized its gestures. Repeat after the initial discovery session to see whether a spatial interaction is learned. Include different sensitivity settings, left/right hands, one-handed use, reduced motion, short and very large libraries, missing tags/durations, and non-Latin names. Hardware measurements must establish responsiveness and contact reliability; browser mockups cannot prove either.

Check the interruptions that matter: screen-off, Return, double Centre, page changes, a track ending during an edit, a changed queue, card removal, and a missing contact release. Editing may not trigger playback or gain changes merely because the recognizer lost track of a gesture.

The existing wheel input path and page ownership mechanisms are the integration foundation. Put any implemented feature in its own module as [CONTRIBUTING](../CONTRIBUTING.md#where-new-code-goes) requires. Wiring an owner into an existing dispatch point does not justify placing the whole feature in `patch/navigation.c`.

Not implemented or tested on hardware: all proposed features, gesture thresholds, absolute-sector accuracy, and performance gains. No claim of patent novelty or exhaustive coverage of every wheel interface.
