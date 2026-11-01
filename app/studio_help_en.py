# studio_help_en.py
"""Английская справка конструктора тем (главы studio_help.CHAPTERS). Подключается словарём i18n_en_extra:
страница в английском режиме подменяется целиком."""

BODY = {
"start": """
<h1>Your first theme in 5 minutes</h1>
<p>The constructor is a “photoshop” for the player's look. Nothing will break: until you press
<b>“Save and apply”</b>, everything you see on the player is only a <b>preview</b>.
Undo any action — <span class=k>Ctrl+Z</span> (redo — <span class=k>Ctrl+Y</span>) or the ↶ ↷ arrows in the header.</p>
<h2>Step 1. Start a theme</h2>
<p>The <b>“Start”</b> page (in the list on the left):</p>
<ul>
<li><b>Surprise me</b> — a random but pretty theme. Press until you like it.</li>
<li><b>+ From scratch</b> — a simple dark theme where everything can be changed.</li>
<li><b>Empty canvas</b> — nothing at all, just a background: for those who want to draw everything themselves.</li>
<li>Ready-made template tiles — can also be used as a base.</li>
</ul>
<h2>Step 2. Background</h2>
<p>The <b>“Background”</b> page: color, gradient, image, GIF, video, “live wallpapers” (aurora, plasma, stars…)
or the cover of the playing track. You can simply <b>drag</b> a file from a folder onto the constructor.
The <b>“Dim”</b> slider makes the background darker so text on it is readable.</p>
<h2>Step 3. Colors and text</h2>
<p><b>“Colors”</b>: the main color (accent), the second color, the text color. The <b>“From the track cover”</b> or
<b>“From the theme background”</b> button picks colors by itself. <b>“Text”</b>: font (your own .ttf/.otf file works) and size.</p>
<h2>Step 4. Decorate</h2>
<p><b>“Layers”</b> → <b>+ Image / GIF</b>, <b>+ Text</b>, <b>+ Shape</b>, <b>+ Widget ▾</b> (record,
visualizer, buttons, lyrics…). Then <b>Arrange on screen</b> — and move everything with the mouse right on the player.</p>
<h2>Step 5. Save</h2>
<p>The <b>“Save”</b> page → <b>“Save and apply”</b>. The theme appears in the player's theme list under
<b>“My themes”</b> (the ✦ icon).</p>
<p class=tip>Tip: the constructor header is a field with the theme name. Name the theme right away — it's easier to find.</p>
""",
"ui": """
<h1>What's where in the constructor</h1>
<h2>Header</h2>
<table>
<tr><td><b>Name</b></td><td>what the theme will be called in the list</td></tr>
<tr><td><b>↶ ↷</b></td><td>undo / redo (Ctrl+Z / Ctrl+Y)</td></tr>
<tr><td><b>Surprise me</b></td><td>a random theme</td></tr>
<tr><td><b>⇲</b></td><td>embed the constructor into the player window or move it to a separate window</td></tr>
</table>
<h2>What we edit: three “views”</h2>
<ul>
<li><b>Main view</b> — the regular player screen.</li>
<li><b>Lyrics mode</b> — the screen with the song lyrics. It can have its own background, layers and layout.</li>
<li><b>Video mode</b> — how watching the song's video looks (frame, background around it, glow, effects).
The player shows a video by itself so you can see what you're changing.</li>
</ul>
<h2>Pages on the left</h2>
<table>
<tr><td><b>Start</b></td><td>new theme, templates, “Surprise me”</td></tr>
<tr><td><b>Colors</b></td><td>theme palette, picking from an image/cover</td></tr>
<tr><td><b>Text</b></td><td>fonts and sizes</td></tr>
<tr><td><b>Shapes</b></td><td>corner rounding, frames, panel glass, shadows, spacing</td></tr>
<tr><td><b>Background</b></td><td>what's behind the interface + background script</td></tr>
<tr><td><b>Layers</b></td><td>images, texts, shapes, videos, effects and widgets over or under the interface</td></tr>
<tr><td><b>Library</b></td><td>ready widgets: record, visualizer, buttons, lyrics, “My Wave”…</td></tr>
<tr><td><b>Effects</b></td><td>particles (snow, stars, sakura, your own image), grain, scanlines, vignette, beat flashes</td></tr>
<tr><td><b>Elements</b></td><td>the player's “native” parts: record, buttons, seek bar, lists — move, hide, resize</td></tr>
<tr><td><b>Lyrics / video mode</b></td><td>the look of these screens</td></tr>
<tr><td><b>Save</b></td><td>save, export/import as a file, themes folder</td></tr>
<tr><td><b>Help</b></td><td>you are here</td></tr>
</table>
""",
"canvas": """
<h1>Mouse and keys on the canvas</h1>
<p>Press <b>Arrange on screen</b> (the “Layers” or “Elements” page): the player turns into a canvas
where everything moves with the mouse. <span class=k>Esc</span> — leave arranging.</p>
<table>
<tr><td><b>Click</b></td><td>select a layer or element</td></tr>
<tr><td><b>Drag</b></td><td>move the selection (the selected one is dragged, even if another lies on top)</td></tr>
<tr><td><b>Drag a corner / edge</b></td><td>resize; it stretches only in the direction you drag</td></tr>
<tr><td><b>Double click</b></td><td>if several things lie in this spot — select the one <b>under</b> the selected one;
if just one — open its editing (text, file, code, button editor)</td></tr>
<tr><td><b>Alt + click</b></td><td>also “what's under the selection”</td></tr>
<tr><td><b>Ctrl + click</b></td><td>select several; <span class=k>Ctrl+A</span> — everything</td></tr>
<tr><td><b>Wheel</b></td><td>layer size; <b>Ctrl + wheel</b> — rotation; <b>Alt + wheel</b> — 3D tilt</td></tr>
<tr><td><span class=k>Arrows</span></td><td>nudge by 1 px, with <span class=k>Shift</span> — by 10 px</td></tr>
<tr><td><span class=k>Delete</span></td><td>delete the layer (a native element — hide)</td></tr>
<tr><td><span class=k>Ctrl+D</span></td><td>duplicate the layer</td></tr>
<tr><td><span class=k>PageUp / PageDown</span></td><td>layer above / below others</td></tr>
<tr><td><span class=k>Ctrl+G</span></td><td>group the selection (Ctrl+Shift+G — ungroup)</td></tr>
<tr><td><span class=k>H</span></td><td>hide / show the layer</td></tr>
<tr><td><span class=k>Enter</span> or <span class=k>F2</span></td><td>edit the selection</td></tr>
<tr><td><b>Right click</b></td><td>menu: “Remove background…”, up/down, duplicate, delete</td></tr>
</table>
<p class=tip>Drag an image, GIF or video from Explorer right onto the player — it appears as a new layer
where you dropped it.</p>
""",
"layers": """
<h1>Layers</h1>
<p>A layer is anything that lies <b>over the interface</b> or <b>under it</b> (behind buttons and lists).
Where it lies is the “Over the interface / In the background, behind panels” switch in the layer properties.</p>
<h2>Kinds of layers</h2>
<ul>
<li><b>Image / GIF</b> — PNG with transparency works too. The <b>Remove background…</b> button cuts out the background
of an image or GIF (our own model, works offline; best on a plain background).</li>
<li><b>Video</b> — loops without sound.</li>
<li><b>Text</b> — your own font, color, gradient, outline, glow.</li>
<li><b>Shape</b> — rectangle, circle, star, heart…</li>
<li><b>Effect</b> — a piece of live wallpaper: stars, rain, matrix, MilkDrop, fluid. The effect's dark background
is transparent; if you need a solid rectangle — the “Solid effect background” checkbox.</li>
<li><b>Widget</b> — a piece of interface with its own code (see the chapter about widgets).</li>
</ul>
<h2>Animations without code</h2>
<p>In the layer properties: <b>+ Animation ▾</b> — rotation, sway, bass pulse, beat jump, float,
flicker… Each has a speed, strength and “music reaction”. You can add several at once.</p>
<h2>Blending</h2>
<p>“Normal”; “Screen” — dark becomes transparent (for highlights, fire, stars); “Add (light)” —
glow; “Multiply” — light becomes transparent (shadows, dirt, textures); “Overlay”, “Lighten”, “Darken”, “Difference”.</p>
""",
"widgets": """
<h1>Widgets and buttons</h1>
<p>The <b>“Library”</b> page — ready widgets: the player's real record, visualizers, lyrics,
“My Wave”, the Winamp spectrum, player buttons, a glass card… Click a tile — the widget appears on the player.</p>
<h2>Widget properties</h2>
<p>Each widget has sliders and colors (for example, the number of spectrum bars). If you want more —
<b>Widget code (EchoScript)…</b>: opens the code, which you can change (the chapter “Scripts: your own widget”).</p>
<h2>Your own buttons</h2>
<p>The “Button (any action)” widget can do everything: play/pause, next track, repeat, lyrics, queue, cover,
video, themes, full screen… The look — the <b>Button editor…</b> button: draw a button, put your own
image, text or icon, colors for hover and press.</p>
<h2>Record — your own image</h2>
<p><b>Elements</b> (or “Library”) → record → <b>“Choose an image (PNG)…”</b>: your image will
spin instead of the disc, <b>only in this theme</b>. “Regular record” — bring the disc back.</p>
""",
"elements": """
<h1>Native player elements</h1>
<p>The <b>“Elements”</b> page: record, visualizer, title, buttons, seek bar, volume, lists, panels.</p>
<ul>
<li>Drag an element on the canvas — it becomes <b>free</b>: any place and any size.</li>
<li><b>“Back to its place”</b> — back to the usual layout; <b>“All as they were”</b> — reset everything.</li>
<li><b>“Remove element”</b> / <span class=k>Delete</span> — hide; bring back — <b>+ Player element ▾</b>.</li>
<li><b>“Build the interface from layers”</b> — “Layers only” mode: the usual interface hides, and all buttons,
the seek bar and the record become widget layers. Complete freedom — the player can be drawn from scratch.</li>
</ul>
""",
"lyrics": """
<h1>Lyrics mode and video mode</h1>
<h2>Lyrics mode</h2>
<p>Switch to <b>Lyrics mode</b> at the top: everything you change now (background, layers, effects, layout)
applies only to the lyrics screen. Buttons on the “Lyrics mode” page:
<b>“Background and effects — like the main view”</b>, <b>“Copy the main view's layers”</b>, line font,
colors of the current and neighboring lines.</p>
<h2>Video mode</h2>
<p>The <b>Video mode</b> button opens a video on the player. On the “Video mode” page: the background around the video (cover,
the video's own colors, a color, the theme background), frame (rounded, TV, neon, polaroid, film strip), glow,
size, control panel and effects that turn on by themselves.</p>
""",
"fx": """
<h1>Particles and screen effects</h1>
<p>The <b>“Effects”</b> page: snow, rain, stars, fireflies, hearts, sakura, confetti, your own image;
film grain, scanlines, vignette, beat flash, flicker.</p>
<p>“Where effects go”: <b>“Behind the interface (background)”</b> — particles fly over the background, under the buttons;
<b>“Over the interface”</b> — over everything.</p>
<p class=tip>Lots of particles and a video background load the CPU. If it lags, reduce the number
of particles or turn off “frosted glass” on the “Shapes” page.</p>
""",
"script1": """
<h1>EchoScript scripts — from scratch</h1>
<p>A script is a few lines of text that tell a layer <b>what to do every frame</b>
(60 times a second) and <b>how to react</b> to music, clicks and the mouse wheel. You don't need to be a programmer:
start with <b>“Ready ▾”</b> — there are working pieces there, you can just paste them and change the numbers.</p>
<h2>Three kinds of scripts</h2>
<table>
<tr><td><b>Layer behavior</b></td><td>moves/rotates/scales any layer. Layer properties → “Behavior” →
<b>+ Add behavior…</b></td></tr>
<tr><td><b>Widget code</b></td><td>draws the widget itself (circles, text, bars). <b>Widget code…</b>
or <b>+ Empty script</b> on the “Layers” page</td></tr>
<tr><td><b>Background script</b></td><td>changes the background video/GIF speed, zoom, offset, dim.
The “Background” page → <b>Background script…</b></td></tr>
</table>
<h2>How to read a script</h2>
<pre>// everything after two slashes is a comment, the player doesn't read it
on frame(dt) {                    // “every frame do this”
  scale = 1 + audio.bass * 0.3    // size = 1 + bass strength × 0.3
}</pre>
<ul>
<li><code>on frame(dt) { … }</code> — every frame. <code>dt</code> — how many seconds passed since the last frame.</li>
<li><code>on beat(p) { … }</code> — a hit in the music (kick). <code>p</code> — hit strength from 0 to 1.</li>
<li><code>on click { … }</code> — a click on the layer; <code>on wheel(d) { … }</code> — the mouse wheel.</li>
<li><code>state name = 0</code> — the layer's “memory”: a number kept between frames.</li>
<li><code>let x = 5</code> — a temporary variable within one frame.</li>
</ul>
<p class=tip>The code applies by itself half a second after you edit it. If the code has an error — at the bottom of the editor there
will be a red line with the line number and a hint; the player won't break.</p>
""",
"script2": """
<h1>Layer behavior — examples</h1>
<p>A behavior changes the layer's <b>fields</b>:</p>
<table>
<tr><td><code>dx, dy</code></td><td>offset in pixels (right / down)</td></tr>
<tr><td><code>rot</code></td><td>rotation in degrees</td></tr>
<tr><td><code>scale, sx, sy</code></td><td>size (1 — as is), by width and by height</td></tr>
<tr><td><code>opacity</code></td><td>opacity 0…1</td></tr>
<tr><td><code>tilt_x, tilt_y</code></td><td>3D tilt in degrees</td></tr>
<tr><td><code>hidden</code></td><td>true — hide</td></tr>
<tr><td><code>tint</code></td><td>color (for text and shapes), for example <code>"#ff66aa"</code></td></tr>
<tr><td><code>label</code></td><td>text (for a text layer)</td></tr>
</table>
<h3>Pulses with the bass</h3>
<pre>on frame(dt) { scale = 1 + audio.bass * 0.3 }</pre>
<h3>Spins, faster in loud parts</h3>
<pre>on frame(dt) { rot += dt * (20 + audio.level * 200) }</pre>
<h3>Jumps on every hit</h3>
<pre>state vy = 0
on beat(p) { vy = -400 * p }      // push up
on frame(dt) {
  vy += 1400 * dt                 // gravity
  dy += vy * dt
  if dy > 0 {                     // floor
    dy = 0
    vy = 0
  }
}</pre>
<h3>Blinks on the beat</h3>
<pre>state f = 0
on beat(p) { f = 1 }
on frame(dt) {
  f = approach(f, 0, 5, dt)       // smoothly fades to 0
  opacity = 0.4 + f * 0.6
}</pre>
<h3>Shows the track title (for a text layer)</h3>
<pre>on frame(dt) { label = track.loaded ? track.title : "ECHOES" }</pre>
<h3>Click — pause, wheel — volume</h3>
<pre>on click { toggle() }
on wheel(d) { set_volume(clamp(player.volume + d * 0.05)) }</pre>
<p class=tip><code>approach(value, target, speed, dt)</code> — the most useful function: smoothly pulls
a number toward the target. The higher the speed, the faster.</p>
""",
"script3": """
<h1>Your own widget</h1>
<p>A widget draws itself. The skeleton:</p>
<pre>widget MyWidget {                 // any name, in Latin letters
  prop color = theme.accent       // a setting (shows up as a slider/color in the properties)
  state t = 0                     // memory
  on frame(dt) { t += dt }        // logic
  on draw {                       // drawing; w and h — the widget size
    nostroke()
    fill(color)
    circle(w / 2, h / 2, min(w, h) * 0.3 * (1 + audio.bass * 0.3))
  }
}</pre>
<h2>What to draw with</h2>
<table>
<tr><td><code>fill(color)</code> / <code>nofill()</code></td><td>fill / no fill</td></tr>
<tr><td><code>stroke(color, width)</code> / <code>nostroke()</code></td><td>outline / no outline</td></tr>
<tr><td><code>rect(x, y, w, h, radius)</code></td><td>rectangle (radius — rounding)</td></tr>
<tr><td><code>circle(x, y, r)</code></td><td>circle</td></tr>
<tr><td><code>line(x1, y1, x2, y2)</code></td><td>line</td></tr>
<tr><td><code>text("string", x, y, size, align: "center")</code></td><td>text</td></tr>
<tr><td><code>image(path, x, y, w, h)</code></td><td>image (for example <code>track.cover</code>)</td></tr>
<tr><td><code>glow(x, y, r, color)</code></td><td>soft glow</td></tr>
<tr><td><code>with_alpha(color, 0.5)</code>, <code>mix(a, b, 0.3)</code></td><td>color transparency, color mix</td></tr>
</table>
<h2>What you can read</h2>
<table>
<tr><td><code>audio.bass / mid / high / level</code></td><td>strength of the lows / mids / highs / overall, 0…1</td></tr>
<tr><td><code>audio.fft[i]</code></td><td>spectrum: 64 bands from low to high</td></tr>
<tr><td><code>track.title, artist, cover, progress</code></td><td>the track and its progress 0…1</td></tr>
<tr><td><code>player.playing, volume, shuffle, repeat</code></td><td>player state</td></tr>
<tr><td><code>time</code></td><td>seconds since start; <code>mouse.x, mouse.y</code> — the cursor</td></tr>
<tr><td><code>theme.accent, theme.text, theme.bg</code></td><td>theme colors</td></tr>
</table>
<h3>A simple 32-bar spectrum</h3>
<pre>widget Spectrum {
  prop color = theme.accent
  on draw {
    nostroke()
    fill(color)
    let n = 32
    let bw = w / n
    for i in 0..n {
      let v = audio.fft[i * 2]
      rect(i * bw + 1, h - v * h, bw - 2, v * h, 2)
    }
  }
}</pre>
<h3>A “next track” button</h3>
<pre>widget NextBtn {
  on click { next() }
  on draw {
    fill(hover ? theme.accent : with_alpha("#ffffff", 0.12))
    rect(0, 0, w, h, h / 2)
    fill(theme.text)
    text("▶▶", w / 2, h / 2, h * 0.4, align: "center")
  }
}</pre>
<p>The full list of functions — the <b>“Help”</b> button in the code editor window or the button below.</p>
""",
"script4": """
<h1>Background script</h1>
<p>The “Background” page → <b>Background script…</b>. It changes:</p>
<table>
<tr><td><code>speed</code></td><td>video / GIF / live wallpaper speed (1 — normal, 2 — twice as fast)</td></tr>
<tr><td><code>zoom</code></td><td>zoom (1 — as is)</td></tr>
<tr><td><code>dx, dy</code></td><td>offset in pixels</td></tr>
<tr><td><code>dim</code></td><td>dim 0…1 (-1 — as in the theme settings)</td></tr>
</table>
<h3>The video speeds up on every hit</h3>
<pre>state kick = 0
on beat(p) { kick = 1 }
on frame(dt) {
  kick = approach(kick, 0, 4, dt)
  speed = 1 + kick * 1.5
}</pre>
<h3>The background “breathes” with the bass</h3>
<pre>on frame(dt) { zoom = 1 + audio.bass * 0.06 }</pre>
<h3>On pause the background darkens and slows down</h3>
<pre>on frame(dt) {
  speed = player.playing ? 1 : 0.25
  dim = player.playing ? -1 : 0.7
}</pre>
""",
"files": """
<h1>Save and share</h1>
<ul>
<li><b>“Save and apply”</b> — the theme turns on and stays in “My themes”.</li>
<li><b>“Save as a new theme”</b> — a copy under another name (the old one doesn't change).</li>
<li><b>“Export to a file…”</b> — one <code>.echoestheme</code> file with all images, videos, fonts
and scripts. You can send it to a friend.</li>
</ul>
<h2>How a friend opens your theme</h2>
<ol>
<li>The easiest — <b>drag the .echoestheme file onto the ECHOES window</b>: the theme is added and turned on right away.</li>
<li>Or put the file into the <b>“Import”</b> folder — <code>C:\\Users\\&lt;name&gt;\\.neon_player\\import</code>.
The player picks it up by itself at startup and when you return to the window.</li>
<li>Or here: the “Save” page → <b>“Import…”</b>.</li>
</ol>
<p>Playlists are shared the same way: right click a playlist → <b>“Share as a file…”</b> — a small
<code>.echoesplaylist</code> file with the track list; your friend's missing tracks download by themselves.</p>
<p><b>“Open the themes folder”</b> — where all your themes are (each has its own folder with <code>theme.json</code>
and an <code>assets</code> folder).</p>
""",
"script5": """
<h1>Widgets in widgets, linked layers, games</h1>
<p>Widget code can do much more than draw: you can <b>nest other widgets</b> in it, reach other widgets and theme
layers, catch the keyboard, play sounds, run timers and save data — enough even for a game.</p>
<h2>Nest a widget</h2>
<pre>widget Pod {
  child lib = Library { x: 0.05, y: 0.1, w: 0.9, h: 0.6 }   // the catalog library inside
  on draw { fill("#222"); rect(0, 0, w, h, 20) }
  on wheel(d) { lib.move_sel(-d) }                            // control the nested one
}</pre>
<p>A ready example is the <b>iPod-style player</b> block (+ Widget ▾ → Player): the real library and the cover live
inside it, the wheel turns with the mouse.</p>
<h2>A cover of any shape</h2>
<p>The <b>Cover of any shape</b> block: pick a shape in the layer properties — circle, heart, star, drop, gear,
flower, cloud… For your own shape use shape = svg with an outline in "Outline (SVG)" or shape = points with your points.</p>
<h2>Linking layers</h2>
<pre>layer("Logo").rot = sin(time) * 20         // move/hide any theme layer
find_widget("Library").open(0)             // call a widget's method from another layer
shared.score += 1                          // variables shared by all layers
post("pause", nil)                         // a message to everyone: on message(name, data) { … }</pre>
<h2>Games</h2>
<p>Keys (<code>on key</code>, <code>key_down("w")</code>, <code>capture_keys()</code>), timers (<code>every</code>,
<code>after</code>), smooth animation (<code>tween</code>), sounds (<code>sound</code>, <code>beep</code>), records
(<code>save_data</code>), camera static (<code>static_noise</code>), sprites, canvases. A whole game skeleton is in the
full EchoScript reference, chapter "Games".</p>
<p class=tip>The <b>Snippets ▾</b> menu of the widget code editor inserts all these pieces at the cursor.</p>
""",
"faq": """
<h1>If something goes wrong</h1>
<h3>A video or GIF background lags</h3>
<p>The “Shapes” page → turn off <b>“Frosted glass (background under panels is blurred)”</b> — the blur under the panels
is the heaviest. Reduce the number of particles. In the player settings you can turn on “Smooth live background (up to 60 fps)”.</p>
<h3>An effect covers everything with a black rectangle</h3>
<p>Uncheck <b>“Solid effect background”</b> in the effect layer properties.</p>
<h3>A layer disappeared</h3>
<p>The “Layers” page: the layer may be “hidden” (the eye) or it went off the edge. Select it in the list and press
<span class=k>H</span> or set “Horizontal/Vertical” = 0.5.</p>
<h3>I can't select what's underneath</h3>
<p><b>Double click</b> that spot (or Alt + click) — the next layer under the cursor is selected.</p>
<h3>The script doesn't work</h3>
<p>Look at the red line at the bottom of the code editor — it shows the line number and what's wrong. Common mistakes: a forgotten
closing bracket <code>}</code>, quotes around text (<code>"text"</code>), a comma instead of a dot in a number
(<code>0.5</code>, not <code>0,5</code>).</p>
<h3>I want it back as it was</h3>
<p><span class=k>Ctrl+Z</span> many times, or the “Save” page → <b>“Revert”</b> (restores the last
saved version).</p>
""",
}
