"""
Pictures, GIFs and videos for the bar ticker.

- Library: every picture you've sent from your phone, saved on the Pi (pictures/ folder) with its own
  settings: how it fits the 640 x 64 strip (whole picture / fill / slow pan) and which part shows.
- Videos (MP4 / MOV from a phone) are shrunk once with ffmpeg in the background, then play as a loop.
- Player: shows one picture, or a slideshow of the ones you checked, with fades between them.
- LED color boost: a little more color, contrast and sharpness so photos pop on LEDs instead of looking washed out.
"""
import io, json, math, os, random, shutil, subprocess, tempfile, threading, time, uuid
from collections import OrderedDict
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageSequence

FITS = ["contain", "fill", "pan"]          # whole picture (black bars) / fill the strip (crop) / fill and slowly pan
VIDEO_SECONDS = 20                         # videos keep their first 20 seconds
VIDEO_FPS = 20
MAX_GIF_FRAMES = 400
MAX_UPLOAD = 250_000_000                   # bytes (a 20 s 4K phone clip is ~100 MB)
CACHE_FRAMES = 700                         # rendered frames kept in memory (~123 KB each at 640 x 64)
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".3gp")


def boost(img):
    """Tuned for LEDs: they crush dark tones and wash out pastel colors, so lift both a little."""
    img = ImageEnhance.Color(img).enhance(1.35)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    return img.filter(ImageFilter.UnsharpMask(radius=1, percent=55, threshold=2))


def _resize(img, size):
    """Smooth for photos; crisp blocky pixels for small pixel-art GIFs being blown up."""
    if size[0] > img.width * 1.4 and img.height <= 128:
        return img.resize(size, Image.NEAREST)
    return img.resize(size, Image.LANCZOS, reducing_gap=3.0)


def cover_size(w, h, W, H):
    s = max(W / w, H / h)
    return max(W, round(w * s)), max(H, round(h * s))


MAX_ZOOM = 4.0
MIN_ZOOM = 0.02                                    # below 1 backs out; render() stops at the whole picture

def render(img, fit, view, W, H, do_boost=True):
    """One frame of a picture fitted to the W x H strip.
    contain: whole picture, black bars.
    fill: view = (zoom, x, y). zoom 1 just fills the strip; up to 4x closer, or below 1 to back out
          (down to the whole picture, with black around it). x and y (0-1) slide which part shows,
          left..right and top..bottom; when the picture is smaller than the strip they move it around inside it."""
    img = img.convert("RGB")
    out = Image.new("RGB", (W, H))
    if fit == "contain":
        s = min(W / img.width, H / img.height)
        r = _resize(img, (max(1, round(img.width * s)), max(1, round(img.height * s))))
        out.paste(r, ((W - r.width) // 2, (H - r.height) // 2))
    else:
        zoom, fx, fy = view
        cover, contain = max(W / img.width, H / img.height), min(W / img.width, H / img.height)
        s = max(contain, cover * zoom)                        # picture pixels -> strip pixels
        cw, ch = img.width * s, img.height * s
        x0, y0 = (W - cw) * fx, (H - ch) * fy                # where the picture's corner lands on the strip
        dx0, dy0 = max(0.0, x0), max(0.0, y0)
        dx1, dy1 = min(float(W), x0 + cw), min(float(H), y0 + ch)
        dw, dh = max(1, round(dx1 - dx0)), max(1, round(dy1 - dy0))
        box = ((dx0 - x0) / s, (dy0 - y0) / s, (dx1 - x0) / s, (dy1 - y0) / s)   # the part of the picture that shows
        if s > 1.4 and img.height <= 128:                     # pixel art: crisp blocks
            part = img.resize((dw, dh), Image.NEAREST, box=box)
        else:
            part = img.resize((dw, dh), Image.LANCZOS, box=box, reducing_gap=3.0)
        out.paste(part, (round(dx0), round(dy0)))
    return boost(out) if do_boost else out


def view_of(it):
    """(zoom, x, y) for a library item. Older items only had one "focus" slider (up/down, or left/right
    for extra-wide panoramas)."""
    if "fy" in it:
        return (it.get("zoom", 1.0), it.get("fx", 0.5), it["fy"])
    f = it.get("focus", 0.5)
    wide = it.get("w") and it.get("h") and it["w"] / it["h"] > 10
    return (1.0, f, 0.5) if wide else (1.0, 0.5, f)


def pan_source(img, W, H, do_boost=True):
    """For Pan: the whole picture scaled to cover the strip (taller or wider than it); the player slides a window."""
    img = img.convert("RGB")
    cw, ch = cover_size(img.width, img.height, W, H)
    r = _resize(img, (cw, ch))
    return boost(r) if do_boost else r


def pan_window(src, t, period, W, H):
    """Window into a pan source at time t: glides one way, eases, glides back."""
    span = max(src.width - W, src.height - H)
    if span <= 0:
        return src.crop((0, 0, W, H))
    phase = (t % (2 * period)) / period              # 0..2
    p = phase if phase <= 1 else 2 - phase           # 0..1..0
    p = 0.5 - 0.5 * math.cos(math.pi * p)            # ease in and out
    off = round(span * p)
    if src.height - H >= src.width - W:              # tall picture: pan top -> bottom
        return src.crop((0, off, W, off + H))
    return src.crop((off, 0, off + W, H))            # wide panorama: pan left -> right


class Rendered:
    """A picture ready to play: frames [(Image, ms)], or a pan source for a slow pan."""
    def __init__(self, frames=None, pan=None):
        self.frames, self.pan = frames or [], pan
        self.total = sum(ms for _, ms in self.frames) or 0

    def frame(self, t, W, H, period=10):
        if self.pan is not None:
            return pan_window(self.pan, t, period, W, H)
        if len(self.frames) == 1:
            return self.frames[0][0]
        pos = (t * 1000) % self.total
        for fr, ms in self.frames:
            if pos < ms:
                return fr
            pos -= ms
        return self.frames[-1][0]

    @property
    def seconds(self):
        """Length of one loop (0 for a still picture)."""
        return self.total / 1000 if len(self.frames) > 1 else 0

    @property
    def size(self):
        return len(self.frames) if self.pan is None else 4


class Library:
    def __init__(self, folder, W, H):
        self.dir, self.W, self.H = folder, W, H
        os.makedirs(folder, exist_ok=True)
        self.index = os.path.join(folder, "index.json")
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self._busy = set()
        self.version = 0
        try:
            self.items = json.load(open(self.index))
        except (OSError, ValueError):
            self.items = []
        for it in self.items:                          # a video that was mid-convert when the Pi restarted
            if it.get("status") == "converting":
                self._convert_later(it)

    # ---- bookkeeping
    def save(self):
        with self.lock:
            tmp = self.index + ".tmp"
            json.dump(self.items, open(tmp, "w"), indent=1)
            os.replace(tmp, self.index)
            self.version += 1

    def get(self, pid):
        return next((it for it in self.items if it["id"] == pid), None)

    def ready_ids(self):
        return [it["id"] for it in self.items if it.get("status") == "ready"]

    def path(self, it):
        return os.path.join(self.dir, it["file"])

    def list(self):
        return [dict(it) for it in self.items]

    # ---- adding
    def add_file(self, src_path, name="", mime="", fit="fill"):
        """Adds an uploaded file (already saved to src_path). Pictures are ready at once; videos convert in the background."""
        pid = uuid.uuid4().hex[:8]
        name = (name or "Picture").strip()[:60]
        ext = os.path.splitext(name)[1].lower()
        is_video = mime.startswith("video/") or ext in VIDEO_EXT
        it = {"id": pid, "name": os.path.splitext(name)[0] or "Picture", "fit": fit, "zoom": 1.0, "fx": 0.5, "fy": 0.5,
              "added": int(time.time())}
        if is_video:
            it.update(kind="video", status="converting", file=pid + ".src" + (ext or ".mp4"))
            shutil.move(src_path, self.path(it))
            with self.lock:
                self.items.append(it)
                self.save()
            self._convert_later(it)
            return it
        try:
            im = Image.open(src_path)
            im.load()
            n = getattr(im, "n_frames", 1)
        except Exception as e:
            os.remove(src_path)
            raise ValueError(f"can't read that picture ({e})")
        kind = "gif" if n > 1 else "image"
        if kind == "image" and im.format not in ("PNG", "JPEG"):     # HEIC/WebP/etc: keep a PNG copy
            fixed = ImageOps.exif_transpose(im).convert("RGB")
            it.update(kind=kind, status="ready", file=pid + ".png", w=fixed.width, h=fixed.height)
            fixed.save(self.path(it))
            os.remove(src_path)
        else:
            it.update(kind=kind, status="ready", file=pid + (".gif" if kind == "gif" else "." + im.format.lower().replace("jpeg", "jpg")),
                      w=im.width, h=im.height)
            shutil.move(src_path, self.path(it))
        if kind == "image" and it["fit"] == "fill" and it["h"] > it["w"]:
            it["fit"] = "pan"                              # tall phone photos look best panning top to bottom
        if kind == "gif" and it["h"] <= 80:              # small pixel-art GIFs: whole thing, blown up crisp
            it["fit"] = "contain"
        with self.lock:
            self.items.append(it)
            self.save()
        return it

    def _convert_later(self, it):
        threading.Thread(target=self._convert, args=(it,), daemon=True).start()

    def _convert(self, it):
        """Shrinks a phone video once (first 20 s, 20 fps, 640 wide, no sound) so it plays smoothly later."""
        src = self.path(it)
        out_name = it["id"] + ".mp4"
        out = os.path.join(self.dir, out_name)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", str(VIDEO_SECONDS), "-an",
               "-vf", f"fps={VIDEO_FPS},scale='min({self.W},iw)':-2", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "23", "-pix_fmt", "yuv420p", out]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "ffmpeg failed")
            w, h, secs = probe(out)
            with self.lock:
                it.update(status="ready", file=out_name, w=w, h=h, seconds=round(secs, 1))
                self.save()
            try:
                os.remove(src)
            except OSError:
                pass
        except FileNotFoundError:
            with self.lock:
                it.update(status="error", error="ffmpeg isn't installed (sudo apt install ffmpeg)")
                self.save()
        except Exception as e:
            with self.lock:
                it.update(status="error", error=str(e)[:200])
                self.save()

    # ---- editing
    def edit(self, pid, d):
        with self.lock:
            it = self.get(pid)
            if not it:
                raise KeyError("no such picture")
            if "fit" in d and d["fit"] in FITS:
                it["fit"] = d["fit"]
            zoom, fx, fy = view_of(it)
            if "focus" in d:                                   # older phone page: one slider
                f = max(0.0, min(1.0, float(d["focus"])))
                wide = it.get("w") and it.get("h") and it["w"] / it["h"] > 10
                fx, fy = (f, fy) if wide else (fx, f)
            if "zoom" in d:
                zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(d["zoom"])))
            if "fx" in d:
                fx = max(0.0, min(1.0, float(d["fx"])))
            if "fy" in d:
                fy = max(0.0, min(1.0, float(d["fy"])))
            it.update(zoom=round(zoom, 4), fx=round(fx, 3), fy=round(fy, 3))
            it.pop("focus", None)
            if "name" in d and str(d["name"]).strip():
                it["name"] = str(d["name"]).strip()[:60]
            self.save()
            return it

    def delete(self, pid):
        with self.lock:
            it = self.get(pid)
            if not it:
                return
            self.items.remove(it)
            for key in [k for k in self.cache if k[0] == pid]:
                del self.cache[key]
            self.save()
        try:
            os.remove(self.path(it))
        except OSError:
            pass

    def import_legacy(self, path, fit):
        """The single picture from before the library existed."""
        tmp = tempfile.NamedTemporaryFile(delete=False, dir=self.dir, suffix=".upload").name
        shutil.copy(path, tmp)
        it = self.add_file(tmp, "My picture", "", "fill" if fit == "fill" else "contain")
        os.replace(path, path + ".imported")
        return it

    # ---- rendering
    def rendered(self, pid, do_boost=True, fit=None, view=None):
        """Frames for a picture with its settings (or the given fit / (zoom, x, y) for a preview). Cached."""
        it = self.get(pid)
        if not it or it.get("status") != "ready":
            return None
        fit = fit if fit in FITS else it["fit"]
        view = view_of(it) if view is None else (max(MIN_ZOOM, min(MAX_ZOOM, float(view[0]))),
                                                 max(0.0, min(1.0, float(view[1]))), max(0.0, min(1.0, float(view[2]))))
        if it["kind"] != "image" and fit == "pan":
            fit = "fill"                                   # moving pictures don't also pan
        if fit != "fill":
            view = (1.0, 0.5, 0.5)
        key = (pid, fit, tuple(round(v, 3) for v in view), bool(do_boost))
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        r = self._render(it, fit, view, do_boost)
        with self.lock:
            self.cache[key] = r
            while sum(v.size for v in self.cache.values()) > CACHE_FRAMES and len(self.cache) > 1:
                self.cache.popitem(last=False)
        return r

    def rendered_async(self, pid, do_boost=True):
        """Like rendered(), but never makes the display wait: returns None and renders in the background
        if this picture isn't ready yet (a 20 s video takes a few seconds to prepare on the Pi)."""
        it = self.get(pid)
        if not it or it.get("status") != "ready":
            return None
        fit = "fill" if it["kind"] != "image" and it["fit"] == "pan" else it["fit"]
        view = view_of(it) if fit == "fill" else (1.0, 0.5, 0.5)
        key = (pid, fit, tuple(round(v, 3) for v in view), bool(do_boost))
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            if key in self._busy:
                return None
            self._busy.add(key)
        def work():
            try:
                self.rendered(pid, do_boost)
            except Exception as e:
                print("picture", pid, "failed:", e)
            finally:
                with self.lock:
                    self._busy.discard(key)
        threading.Thread(target=work, daemon=True).start()
        return None

    def _render(self, it, fit, view, do_boost):
        W, H = self.W, self.H
        path = self.path(it)
        if it["kind"] == "video":
            frames = [(render(fr, fit, view, W, H, do_boost), 1000 // VIDEO_FPS)
                      for fr in video_frames(path, it.get("w"), it.get("h"))]
            return Rendered(frames=frames or [(Image.new("RGB", (W, H)), 1000)])
        im = Image.open(path)
        if it["kind"] == "image":
            im = ImageOps.exif_transpose(im)
            if im.format == "JPEG" or im.mode == "RGB":
                im.draft("RGB", (W * 2, H * 2))
            if fit == "pan":
                return Rendered(pan=pan_source(im, W, H, do_boost))
            return Rendered(frames=[(render(im, fit, view, W, H, do_boost), 1000)])
        frames = []
        for i, fr in enumerate(ImageSequence.Iterator(im)):
            if i >= MAX_GIF_FRAMES:
                break
            ms = fr.info.get("duration", 100) or 100
            frames.append((render(fr.convert("RGB"), fit, view, W, H, do_boost), 100 if ms <= 10 else max(20, ms)))
        return Rendered(frames=frames)


def probe(path):
    """(width, height, seconds) of a video, using ffprobe."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", path],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    return int(s.get("width", 0)), int(s.get("height", 0)), float((d.get("format") or {}).get("duration", 0) or 0)


def video_frames(path, w, h):
    """Decodes the shrunk video one frame at a time (so a whole clip never sits in memory at full size)."""
    if not w or not h:
        w, h, _ = probe(path)
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield Image.frombytes("RGB", (w, h), buf)
    finally:
        p.stdout.close()
        p.wait()


class Player:
    """Plays one picture or a slideshow, by the clock.
    Each picture stays `secs` seconds (GIFs and videos at least one full loop, up to 30 s),
    then changes with a fade, a slide or a straight cut."""
    FADE = 0.6

    def __init__(self, lib, W, H):
        self.lib, self.W, self.H = lib, W, H
        self.ids, self.order, self.i = [], [], 0
        self.start = time.time()
        self.prev = None                      # last frame of the picture before, for the fade

    def set_playlist(self, ids, shuffle=False):
        ids = [i for i in ids if i in self.lib.ready_ids()]
        if ids != self.ids:
            self.ids = ids
            self.order = ids[:]
            if shuffle:
                random.shuffle(self.order)
            self.i, self.start, self.prev = 0, time.time(), None

    def current(self):
        return self.order[self.i % len(self.order)] if self.order else None

    def duration(self, r, secs):
        if r is None:
            return secs
        if r.seconds:
            loops = max(1, math.ceil(secs / r.seconds)) if r.seconds < secs else 1
            return min(max(secs, r.seconds * loops), max(secs, 30))
        return secs

    def next(self, shuffle=False):
        self.i += 1
        if self.i >= len(self.order):
            self.i = 0
            if shuffle and len(self.order) > 2:
                last = self.order[-1]
                random.shuffle(self.order)
                if self.order[0] == last:
                    self.order.append(self.order.pop(0))
        self.start = time.time()

    def frame(self, secs=10, fx="fade", do_boost=True, shuffle=False, now=None):
        """The picture to show right now (W x H), or None when there's nothing to show."""
        now = now or time.time()
        pid = self.current()
        if pid is None:
            return None
        if pid not in self.lib.ready_ids():    # deleted
            self.set_playlist([i for i in self.ids if i != pid], shuffle)
            return None
        r = self.lib.rendered_async(pid, do_boost)
        if r is None:                          # still being prepared: hold the last picture, start the clock when ready
            self.start = now
            return self.prev
        if len(self.order) > 1:                # get the next one ready in the background
            self.lib.rendered_async(self.order[(self.i + 1) % len(self.order)], do_boost)
        t = now - self.start
        img = r.frame(t, self.W, self.H, period=max(6, secs))
        if len(self.order) > 1 and t >= self.duration(r, secs):
            self.prev = img
            self.next(shuffle)
            return self.frame(secs, fx, do_boost, shuffle, now)
        if self.prev is not None and t < self.FADE and fx != "cut":
            p = t / self.FADE
            if fx == "slide":                  # the new picture pushes the old one off to the left
                out = Image.new("RGB", (self.W, self.H))
                off = round(self.W * (1 - (1 - p) ** 2))
                out.paste(self.prev, (-off, 0))
                out.paste(img, (self.W - off, 0))
                return out
            return Image.blend(self.prev, img, p)
        return img
