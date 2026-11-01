# dsl_docs_en.py
"""English EchoScript reference (dsl_docs.html() picks it when the interface language is English)."""

from dsl_docs import GAME_EXAMPLE

SECTIONS = [
("What it is", """
<h1>EchoScript — the ECHOES theme language</h1>
<p>EchoScript lets you write <b>your own theme from scratch</b>: any widgets (buttons, progress, cover, lists), their
behavior (mouse, animations, physics) and sound-reactive visualizers. The script is rebuilt <b>on the fly</b>: edit
the code and half a second later the change is on screen, while widget state (positions, particles) is kept.</p>
<p>A script file (<code>.echo</code>) has three parts:</p>
<pre>theme { name: "My theme", bg: "#0b0a14", accent: "#ffb347" }   // look

widget Pulse {                    // your widget
  on draw {
    fill(theme.accent)
    circle(w / 2, h / 2, 20 + audio.bass * 60)
  }
}

scene {                           // what goes where
  add Pulse { x: 0.4, y: 0.3, w: 0.2, h: 0.3 }
}</pre>
<h2>What the language can do</h2>
<ul>
<li><b>Widgets inside widgets</b>: <code>child lib = Library { … }</code> — an iPod with the real library inside, a
window with buttons, a game screen with its own objects. Nested widgets get clicks, wheel and frames on their own.</li>
<li><b>Widgets and layers talk</b>: call another widget's methods (<code>lib.move_sel(1)</code>), messages
<code>emit</code> / <code>send</code> / <code>post</code>, shared variables <code>shared</code>, lookup
<code>find_widget</code>, control any theme layer <code>layer("Logo").rot = 30</code>.</li>
<li><b>Games and interactivity</b>: keyboard (<code>key_down("w")</code>, <code>capture_keys</code>), timers
<code>after</code> / <code>every</code>, smooth animation <code>tween</code>, sounds from files and synthesized
<code>beep</code> / <code>tone</code>, saves <code>save_data</code>, input dialogs, collisions, randomness,
camera static, sprites, drawing canvases.</li>
<li><b>Shapes of any complexity</b>: outlines (<code>shape("heart", …)</code>, SVG, your own points), union and
subtraction, masks — a picture or cover of any shape, clicks only inside a shape.</li>
<li><b>The whole library</b>: <code>library()</code>, playlists, search, artists, albums, queue, player themes.</li>
<li><b>A friendly language</b>: <code>match</code>, <code>try / catch</code>, template strings
<code>`Score: {score}`</code>, <code>x in list</code>, value methods <code>list.map(…)</code>,
<code>string.upper()</code>.</li>
</ul>
"""),

("Without code", """
<h2>Build a theme with the mouse</h2>
<p>In the theme constructor, open <b>Layers → + Widget ▾</b> and pick a ready block: cover (also of any shape),
title, progress, buttons, volume, spectrum, wave, circular spectrum, sparks, track list, the whole library, an iPod,
a record, lyrics… Each block is a layer: move it, stretch it, rotate it, tilt it in 3D. Its properties (colors, sizes,
modes) are in the layer panel. <b>Widget code (EchoScript)…</b> opens its code: you can change anything.</p>
<p>The <b>Snippets ▾</b> menu of the code editor inserts ready pieces at the cursor: nested widgets, timers, sounds,
keys, masks, canvases, saves, layer control and more.</p>
"""),

("Syntax", """
<h2>Syntax</h2>
<h3>Comments, variables, assignment</h3>
<pre>// one line     /* multi-line */
let x = 10           // declare a variable (no semicolon needed)
x = x + 1
x += 2   x -= 1   x *= 3   x /= 2   x %= 5</pre>
<h3>Values</h3>
<table>
<tr><td class=k>12  3.5  1e3</td><td>numbers</td></tr>
<tr><td class=k>"text"  'text'</td><td>strings (<code>\\n</code>, <code>\\t</code>, <code>\\"</code>); <code>+</code> joins with anything</td></tr>
<tr><td class=k>`Score: {score}`</td><td>template string, may span several lines</td></tr>
<tr><td class=k>true false nil</td><td>booleans and "nothing"</td></tr>
<tr><td class=k>[1, 2, 3]</td><td>list: <code>a[0]</code>, <code>a[-1]</code>, <code>a.length</code>, <code>a.first</code>, <code>a.last</code></td></tr>
<tr><td class=k>{name: "x", "k": 1}</td><td>dict: <code>d.name</code> or <code>d["k"]</code>; missing key — <code>nil</code></td></tr>
</table>
<h3>Operators (lowest to highest priority)</h3>
<pre>cond ? a : b        ternary
a or b   a and b   not a
==  !=  &lt;  &lt;=  &gt;  &gt;=   in   not in
a..b                number range a, a+1, …, b-1 (for loops)
+  -   *  /  %   **  (division by 0 gives 0, not an error)</pre>
<h3>Conditions and loops</h3>
<pre>if level &gt; 0.8 { … } elif level &gt; 0.4 { … } else { … }

for i in 0..64 { … }            // numbers
for item in list { … }          // items
for i, item in list { … }       // with index
for key, value in dict { … }    // dict
while x &lt; 10 { x += 1 }
break   continue</pre>
<h3>Functions</h3>
<pre>fn smoothstep(e0, e1, x = 0.5) {
  let t = clamp((x - e0) / (e1 - e0))
  return t * t * (3 - 2 * t)
}
smoothstep(0, 1, 0.3)
smoothstep(0, 1, x: 0.3)        // named argument
let twice = fn(v) { return v * 2 }   // anonymous function (closure)</pre>
<p class=m>Variables are visible inside functions; ones declared at the top of the file — everywhere.
Inside a widget names are looked up: locals → widget fields → globals.</p>
"""),

("More language", """
<h2>More language features</h2>
<h3>match — pick a branch</h3>
<pre>match mode {
  "menu" =&gt; draw_menu()
  "game", "pause" =&gt; { draw_game(); if mode == "pause" { draw_pause() } }
  0..10 =&gt; log("a number 0 to 9")         // a range
  else =&gt; log("something else")
}</pre>
<h3>try / catch — catch an error</h3>
<pre>try {
  let d = from_json(text)
  score = d.score
} catch err {                     // err — the error text (the name is optional)
  log("bad data:", err)
}</pre>
<h3>Value methods</h3>
<pre>[3, 1, 2].sort()   lst.push(4)   lst.map(fn(v) { return v * 2 })   lst.filter(fn(v) { return v &gt; 1 })
lst.find(fn(v) { return v.id == 3 })   lst.contains(2)   lst.join(", ")   lst.random()   lst.sum()
"Hello".upper()   s.split(",")   s.replace("a", "b")   s.trim()   s.starts_with("x")   s.words()
d.keys()   d.values()   d.has("k")   d.get("k", 0)   d.remove("k")   d.merge(other)</pre>
<p class=m>A dict's own key wins over a method: for <code>{keys: 5}</code>, <code>d.keys</code> is 5.</p>
"""),

("Widgets", """
<h2>Widgets</h2>
<pre>widget Name {
  prop size = 40          // property: a default value, can be set in add
  state angle = 0         // state: lives between frames (kept on hot reload)
  child list = Library { }   // a nested widget (see "Nested widgets")
  fn helper(a) { … }      // method (called as helper(1) or self.helper(1))
  on frame(dt) { angle += dt * 90 }
  on draw { … }           // drawing, coordinates inside the widget: (0,0) is the top-left corner
}</pre>
<h3>Built-in fields of every widget</h3>
<table>
<tr><td class=k>x, y, w, h</td><td>position and size in pixels (computed from fractions of the screen)</td></tr>
<tr><td class=k>z</td><td>layer: higher is on top (drawn later, gets the mouse first)</td></tr>
<tr><td class=k>visible</td><td>show the widget</td></tr>
<tr><td class=k>clip</td><td>clip drawing to the widget frame (true by default; false for glow past the edges)</td></tr>
<tr><td class=k>hover, pressed</td><td>the cursor is over the widget / a mouse button is held</td></tr>
<tr><td class=k>cache, cache_key</td><td>cache: true — drawn once and then copied; redrawn when cache_key changes</td></tr>
<tr><td class=k>resolution</td><td>0.1…1 — draw into a smaller picture and stretch (cheap for glowing visualizers)</td></tr>
<tr><td class=k>name</td><td>instance name (used by find_widget)</td></tr>
<tr><td class=k>hit_shape, cursor</td><td>clickable shape (an outline) and mouse cursor — see "Keys and mouse"</td></tr>
</table>
<h3>Scene and theme</h3>
<pre>theme { name: "Neon", bg: "#05040a", accent: "#ff3d81", accent2: "#3dd6ff",
        text: "#f4f4f8", muted: "#8a8aa0", font: "Segoe UI" }

scene {
  add Pulse { x: 0.1, y: 0.1, w: 0.3, h: 0.3, z: 2, size: 60 }
  for i in 0..5 {                       // regular code works in a scene
    add Pulse { x: 0.1 + i * 0.15, y: 0.6, w: 0.12, h: 0.2 }
  }
}</pre>
<p>Coordinates: a number <b>from 0 to 1</b> is a fraction of the window width/height; <b>greater than 1</b> — pixels;
negative <code>x</code>/<code>y</code> count from the right/bottom edge.</p>
"""),

("Events", """
<h2>Events (on …)</h2>
<table>
<tr><td class=k>init</td><td>once when the scene starts</td></tr>
<tr><td class=k>frame(dt)</td><td>every frame (~60 per second); dt — seconds since the previous frame</td></tr>
<tr><td class=k>draw</td><td>drawing</td></tr>
<tr><td class=k>press(x, y, button)</td><td>mouse press (button: 1 — left, 2 — right)</td></tr>
<tr><td class=k>release(x, y, button)</td><td>release</td></tr>
<tr><td class=k>click(x, y, button)</td><td>click (pressed and released almost in place)</td></tr>
<tr><td class=k>dblclick(x, y, button)</td><td>double click</td></tr>
<tr><td class=k>drag(x, y, dx, dy)</td><td>dragging after press</td></tr>
<tr><td class=k>move(x, y)</td><td>cursor moves over the widget</td></tr>
<tr><td class=k>enter / leave</td><td>cursor entered / left</td></tr>
<tr><td class=k>wheel(delta, x, y)</td><td>mouse wheel (+1 up, -1 down)</td></tr>
<tr><td class=k>beat(power)</td><td>a beat in the music (power 0…1)</td></tr>
<tr><td class=k>track(t)</td><td>the track changed</td></tr>
<tr><td class=k>key(name) / keyup(name)</td><td>a key pressed / released ("a"…"z", "0"…"9", "space", "left", "right", "up",
"down", "enter", "escape", "tab", "shift", "ctrl", "alt", "f1"…"f12"). Letters follow the physical key.
<code>return true</code> — the key is taken from the player</td></tr>
<tr><td class=k>message(name, data, from)</td><td>a message from another widget (emit / send / post); <code>return</code> — the reply</td></tr>
<tr><td class=k>resize(w, h)</td><td>the widget size changed</td></tr>
<tr><td class=k>destroy</td><td>the widget is removed (kill)</td></tr>
</table>
<p class=m>Parameters are optional: <code>on click { … }</code> works too. Mouse coordinates are inside the widget
(for a nested one — inside itself).</p>
"""),

("Nested widgets", """
<h2>A widget inside a widget</h2>
<p>Any widget can contain other widgets — your own (from the same code) or ready catalog blocks
(<code>Library</code>, <code>CoverShape</code>, <code>Tracklist</code>, <code>Spectrum</code>, <code>Vinyl</code>,
<code>ActionButton</code>… — by class name or block key: <code>"library"</code>, <code>"cover_shape"</code>).
A nested widget lives on its own: frames, beats, track changes, clicks, wheel, hover.</p>
<h3>child — declare in a widget</h3>
<pre>widget Pod {
  child lib = Library { x: 0.05, y: 0.1, w: 0.9, h: 0.6, row: 30 }
  child art = CoverShape { x: 0.3, y: 0.72, w: 0.4, h: 0.26, shape: "heart" }
  on draw { fill("#222"); rect(0, 0, w, h, 20) }      // nested widgets are drawn on top of this
}</pre>
<p>Nested coordinates are relative to the parent: <b>up to 1</b> — a fraction of the parent size, <b>above 1</b> —
pixels, negative — from the right/bottom edge. They follow the parent's size automatically.</p>
<h3>spawn / kill — create and remove from code</h3>
<pre>on click(x, y) {
  let b = spawn("CoverShape", {x: x - 40, y: y - 40, w: 80, h: 80, shape: "star"})
  after(2, fn() { kill(b) })                 // remove after 2 seconds
}</pre>
<h3>draw_child — draw exactly where you need</h3>
<pre>on draw {
  rect(0, 0, w, h)
  draw_child(lib, 10, 30, w - 20, h - 40)   // here and this size; clicks land here too
  text("on top of the list", 20, 20)
}</pre>
<p>After the first <code>draw_child</code> the widget no longer draws itself (<code>auto = false</code>).
<code>visible = false</code> hides it and turns off its clicks.</p>
<table>
<tr><td class=k>lib.scroll = 0   lib.move_sel(1)</td><td>fields and methods of a nested widget — directly</td></tr>
<tr><td class=k>place(c, x, y, w, h)</td><td>move it (parent fractions or pixels)</td></tr>
<tr><td class=k>children()   c.parent   parent_of(c)</td><td>nested list / the parent</td></tr>
<tr><td class=k>bring_to_front(c)   send_to_back(c)</td><td>above / below its siblings (z)</td></tr>
<tr><td class=k>c.rot   c.scale   c.opacity</td><td>rotation (°), scale and opacity (clicks respect rotation)</td></tr>
</table>
<p>A complete example is the <b>iPod-style player</b> block: the library and the cover live inside it, the wheel turns
with the mouse.</p>
"""),

("Widgets talking", """
<h2>Widgets and layers talk</h2>
<table>
<tr><td class=k>emit(name, data)</td><td>a message to the parent</td></tr>
<tr><td class=k>send(widget_or_name, name, data)</td><td>a message to one widget → its reply</td></tr>
<tr><td class=k>post(name, data)</td><td>to all widgets of all theme layers that have on message → how many got it</td></tr>
<tr><td class=k>find_widget(name)   find_widgets(name)</td><td>a widget by name, class or layer name — in any layer</td></tr>
<tr><td class=k>widgets()   widget_at(x, y)</td><td>all widgets of this layer / the widget under a point</td></tr>
</table>
<pre>widget Item {
  on click { emit("picked", self.name) }
}
widget Menu {
  child a = Item { name: "Play", y: 0, h: 0.3 }
  child b = Item { name: "Quit", y: 0.4, h: 0.3 }
  on message(name, data, from) { if name == "picked" { log("pressed", data) } }
}</pre>
<h3>shared — variables shared by all layers</h3>
<pre>shared.score = (shared.score or 0) + 1      // in one layer
text(`Score: {shared.score}`, 10, 20)       // in another</pre>
<h3>layer — control any theme layer</h3>
<pre>let l = layer("Logo")                       // the layer name in the constructor
l.rot = sin(time) * 20                      // added on top of the layer's animations
l.dx = 30     l.dy = -10     l.scale = 1.2     l.opacity = 0.5
l.hidden = true    l.show()    l.hide()    l.toggle()    l.reset()
l.tint = "#ff66aa"     l.label = "New text"    l.widget.move_sel(1)
layers()                                    // names of all layers</pre>
"""),

("Time and animation", """
<h2>Timers</h2>
<table>
<tr><td class=k>after(sec, fn)</td><td>once in sec seconds → id</td></tr>
<tr><td class=k>every(sec, fn)</td><td>every sec seconds → id</td></tr>
<tr><td class=k>cancel(id)</td><td>cancel a timer or tween; <code>cancel()</code> — all timers of the widget</td></tr>
</table>
<h2>Smooth changes: tween</h2>
<p><code>tween(target, "field", value, seconds, easing = "out_cubic", fn_when_done)</code> smoothly brings a field of a
widget (<code>self</code>, a nested one) or a dict to a value. Numbers, colors, lists of numbers.</p>
<pre>on click { tween(self, "k", k &gt; 0.5 ? 0 : 1, 0.5, "out_back") }
tween(art, "rot", 360, 1.2, "in_out_sine", fn() { art.rot = 0 })</pre>
<p><code>tween_stop(target, "field")</code>; <code>ease(name, t)</code> — the curve itself. Names: <code>linear</code>,
<code>smooth</code> and <code>in_</code> / <code>out_</code> / <code>in_out_</code> + <code>quad cubic quart quint sine
expo circ back elastic bounce</code>.</p>
"""),

("Keys and mouse", """
<h2>Keyboard</h2>
<table>
<tr><td class=k>on key(k) / on keyup(k)</td><td>press / release; <code>return true</code> takes the key from the player</td></tr>
<tr><td class=k>key_down("w")</td><td>is the key held right now — for movement in games</td></tr>
<tr><td class=k>keys_pressed()</td><td>list of held keys</td></tr>
<tr><td class=k>capture_keys()</td><td>all keys go only to scripts (space doesn't pause);
<code>capture_keys(true, ["w", "a", "s", "d"])</code> — only these; <code>capture_keys(false)</code> — give them back</td></tr>
</table>
<p class=m>While the cursor is in a text field, keys don't go to scripts.</p>
<h2>Mouse</h2>
<table>
<tr><td class=k>mouse_in()</td><td>the cursor relative to this widget: {x, y, inside, down}</td></tr>
<tr><td class=k>mouse.x, mouse.y</td><td>the cursor in window coordinates</td></tr>
<tr><td class=k>self.hit_shape = outline</td><td>clicks only inside the shape</td></tr>
<tr><td class=k>cursor("hand")</td><td>hand, arrow, cross, none, text, move, wait, grab, grabbing, forbidden</td></tr>
</table>
"""),

("Sound", """
<h2>Sounds</h2>
<table>
<tr><td class=k>sound(path, volume = 1, pan = 0, rate = 1, loop = false)</td><td>a sound file (wav, mp3, ogg, flac,
m4a…) over the music → id. pan: -1 left … 1 right; rate — speed and pitch</td></tr>
<tr><td class=k>sound_loop(path, volume)</td><td>looped (background, a fan's hum)</td></tr>
<tr><td class=k>sound_stop(id, fade = 0)   sound_stop_all()</td><td>stop (fade — seconds)</td></tr>
<tr><td class=k>sound_volume  sound_fade  sound_pan  sound_rate  sound_pause  sound_seek</td><td>change on the fly</td></tr>
<tr><td class=k>sound_playing(id)   sound_pos(id)   sound_length(path)   sound_preload(path)</td><td>questions / load ahead</td></tr>
<tr><td class=k>beep(freq = 880, dur = 0.15, volume = 0.5, wave = "sine", sweep = 0, pan = 0)</td><td>synthesized sound:
sine, square, saw, triangle, noise</td></tr>
<tr><td class=k>tone([[freq, duration], …], volume, wave)   note("A4")</td><td>a melody; note frequencies</td></tr>
<tr><td class=k>sound_master(v)</td><td>overall volume of script sounds 0…2</td></tr>
</table>
"""),

("Shapes and masks", """
<h2>Outlines of any shape</h2>
<p><code>shape(kind, x, y, w, h, options)</code> → an outline fitted into a rectangle. Kinds: <code>rect square rounded
circle ellipse squircle triangle diamond pentagon hexagon octagon ngon star heart blob flower wave gear drop leaf pill
shield cross ring arch cloud moon badge parallelogram trapezoid svg points</code>.</p>
<p>Options: <code>sides</code>, <code>points</code> and <code>inner</code> (star), <code>radius</code>,
<code>petals</code>/<code>depth</code>, <code>teeth</code>/<code>hole</code>, <code>waves</code>/<code>amp</code>,
<code>wobble</code>/<code>t</code>/<code>seed</code> (blob), <code>d</code> (svg), <code>points</code> (points),
<code>smooth</code>, <code>rot</code>, <code>stretch</code>.</p>
<table>
<tr><td class=k>draw_path(p)   clip_path(p)</td><td>draw with the current fill/stroke / mask (until restore())</td></tr>
<tr><td class=k>draw_shape(…)   clip_shape(…)   image_shape(path, kind, x, y, w, h)</td><td>by kind directly; a picture in a shape</td></tr>
<tr><td class=k>path_svg("M0 0 L10 0 …")   path_new()</td><td>outline from SVG / an empty one built with methods</td></tr>
</table>
<pre>let p = path_new().move(0, 0).line(100, 0).curve(120, 40, 60, 90, 0, 60).close()
p.rect  p.circle  p.ellipse  p.poly  p.smooth  p.quad  p.arc  p.text
p.union(q)  p.minus(q)  p.intersect(q)  p.outline(width)  p.moved  p.rotated  p.scaled  p.fit  p.copy
p.contains(x, y)   p.point_at(0.5) → {x, y, angle}   p.bounds()   p.length</pre>
"""),

("Canvases and images", """
<h2>Canvases</h2>
<table>
<tr><td class=k>canvas(w, h)</td><td>a picture in memory that keeps what's drawn between frames</td></tr>
<tr><td class=k>draw_on(c, fn() { … })</td><td>all drawing inside fn goes into the canvas</td></tr>
<tr><td class=k>draw_canvas(c, x, y, w, h, smooth)</td><td>show it (smooth: false — crisp pixels)</td></tr>
<tr><td class=k>pixel(c, x, y, color)   get_pixel(c, x, y)   canvas_clear(c)   blur_canvas(c, sigma)</td><td>pixels, clear, blur</td></tr>
</table>
<h2>Images, text, effects</h2>
<table>
<tr><td class=k>thumb(path, x, y, w, h, r)</td><td>a small picture loaded in the background — for long lists of covers</td></tr>
<tr><td class=k>image_ex(path, x, y, w, h, {fit, r, flip_x, flip_y, opacity, sx, sy, sw, sh})</td><td>fit: cover | contain | stretch</td></tr>
<tr><td class=k>image_part   sprite(path, frame, cols, rows, x, y, w, h)   image_size</td><td>parts, sprite sheets, size</td></tr>
<tr><td class=k>text_box(s, x, y, w, h, size, align, valign)   text_height   font_load(path)</td><td>wrapped text, own fonts</td></tr>
<tr><td class=k>shadow  static_noise  scanlines  vignette  rect4  stroke_dash  antialias  smooth_images</td><td>shadows,
camera static, CRT lines, dark edges, per-corner radius, dashes</td></tr>
</table>
"""),

("Library", """
<h2>Library and player</h2>
<table>
<tr><td class=k>library()</td><td>all tracks: {title, artist, album, duration, cover, path, index, year, genre}</td></tr>
<tr><td class=k>search_tracks(query)   artists()   albums()   by_artist(name)   by_album(name)</td><td>search and grouping</td></tr>
<tr><td class=k>playlists()   playlist_tracks(name)</td><td>playlists</td></tr>
<tr><td class=k>play_track(index, context)   play_path(path)   queue_add(track, next)   queue()</td><td>play and queue</td></tr>
<tr><td class=k>set_shuffle(on)   set_repeat(0 | 1 | 2)   seek_to(sec)   themes()   set_theme(name)   toast(text)</td><td>player</td></tr>
<tr><td class=k>track_menu(path)   reveal(path)</td><td>track menu (play, show in Explorer, tags) / open the folder with the track file</td></tr>
<tr><td class=k>artist_shuffle(name?)   artist_menu()</td><td>shuffle by artist: all their tracks shuffled (no name — artist of the current track) / artist picker menu</td></tr>
<tr><td class=k>toggle() play() pause() next() prev() seek(frac) set_volume(v) lyrics_mode() fullscreen() …</td><td>actions</td></tr>
<tr><td class=k>audio.fft / bass / mid / high / level / beat   track.*   player.*   playlist.*   lyrics.*</td><td>what scripts see</td></tr>
</table>
"""),

("Data and windows", """
<h2>Saves</h2>
<table>
<tr><td class=k>save_data(key, value)   load_data(key, default)   delete_data(key)   data_keys()</td><td>survive a restart</td></tr>
<tr><td class=k>to_json(v, pretty)   from_json(s, default)</td><td>JSON</td></tr>
</table>
<h2>Windows and system</h2>
<table>
<tr><td class=k>prompt(title, label, default, fn(text) { … })</td><td>ask for text (nil — cancelled)</td></tr>
<tr><td class=k>ask(text, fn(yes) { … })</td><td>a Yes/No question</td></tr>
<tr><td class=k>pick_file(title, filter, fn(path) { … })</td><td>choose a file</td></tr>
<tr><td class=k>open_url(url)   copy_text(s)   paste_text()</td><td>links, clipboard</td></tr>
</table>
"""),

("All functions", """
<h2>Function library</h2>
<p><b>Strings:</b> <code>replace trim starts_with ends_with find_str substr repeat pad_left pad_right chars char code
format title_case capitalize count_of is_digit is_alpha wrap_words join split upper lower fmt fmt_time fmt_num percent</code></p>
<p><b>Lists:</b> <code>push pop insert remove slice range sort reverse contains map filter reduce each find find_index
index_of unique shuffle zip flatten concat copy deep_copy count any all min_of max_of min_by max_by group_by chunk take
drop sum avg first last swap rotate_list list_of grid_of fill_list</code></p>
<p><b>Dicts and types:</b> <code>keys values items from_items has get_or del_key merge type is_num is_str is_list
is_dict is_fn is_nil is_widget default</code></p>
<p><b>Math:</b> <code>sin cos tan asin acos atan atan2 sqrt abs min max floor ceil round pow exp ln log10 log2 cbrt sign
hypot deg rad clamp lerp remap smooth approach spring noise noise2 fbm wrap fract mod step pingpong round_to snap trunc
rand randint choice seed chance rand_gauss weighted_choice uid bezier catmull bit_and bit_or bit_xor bit_not shl shr</code></p>
<p><b>Geometry:</b> <code>point_in_rect point_in_circle rect_hit circle_hit circle_rect_hit line_hit dist dist2
angle_to vec_len normalize dot cross rotate_point angle_diff lerp_angle move_toward</code></p>
<p><b>Colors:</b> <code>rgb hsv hsl mix with_alpha hex lighten darken saturate grayscale invert color_rgb brightness
contrast_text gradient_at palette_of</code></p>
<p><b>Time:</b> <code>now() date() clock(seconds) fmt_date("%d.%m.%Y %H:%M")</code></p>
"""),

("Games", """
<h2>How to make a game (anything)</h2>
<p>A game is one widget layer over the whole window (or several layers linked with <code>shared</code> and
<code>post</code>). State lives in <code>state</code>, logic in <code>on frame</code>, drawing in <code>on draw</code>,
input in <code>on key</code> / <code>key_down</code> / <code>on click</code>, timed events in <code>every</code> /
<code>after</code>, effects in <code>tween</code>, sounds in <code>sound</code> / <code>beep</code>, records in
<code>save_data</code>. Below is a "night shift" skeleton: cameras with static, doors, power drain, an enemy walking
between rooms, a scare and a win at 6 AM.</p>
<pre>""" + GAME_EXAMPLE + """</pre>
<p class=m>Make a widget layer over the whole window (constructor → Layers → + Widget → Empty script, stretch it to the
full screen), paste the code and add your own room pictures: <code>image("assets/room_a.png", 0, 0, w, h)</code>.</p>
"""),

("Speed", """
<h2>Keeping it smooth (60 fps)</h2>
<ul>
<li>Static things (background, frames, labels) — <code>cache: true</code>; rarely changing — <code>cache_key</code>.</li>
<li>A heavy glowing visualizer — <code>resolution: 0.5…0.7</code>.</li>
<li>Many lines — one <code>lines([...])</code> instead of <code>line()</code> in a loop.</li>
<li>Transparency in the color (<code>with_alpha(c, 0.5)</code>) rather than <code>alpha()</code> for every shape.</li>
<li>Heavy computations in <code>on frame</code>, only drawing in <code>on draw</code>; lists of covers — <code>thumb</code>.</li>
<li>Each handler has a step limit: an endless loop won't freeze the player — the widget just stops with an error.</li>
</ul>
<h2>Errors</h2>
<p>A syntax error keeps the previous version running and shows the line and the reason at the bottom of the editor.
A runtime error disables only the widget where it happened.</p>
"""),
]
