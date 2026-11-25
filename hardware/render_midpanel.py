# 3D renders of the 11/25 mid-panel layout (straps at every seam, Pi plate on Panel 3, supply plate on Panel 2).
import numpy as np, trimesh, pyvista as pv
pv.OFF_SCREEN = True
LEG, T = 14.0, 4.0
def stl(n): return trimesh.load(n)
def bx(x0, y0, z0, x1, y1, z1):
    return trimesh.creation.box(extents=[x1-x0, y1-y0, z1-z0]).apply_translation([(x0+x1)/2, (y0+y1)/2, (z0+z1)/2])
parts = []   # (mesh, color)
def add(m, c): parts.append((m, c))
def mirror_y(m): m = m.copy(); m.apply_transform(np.diag([1, -1, 1, 1])); m.invert(); return m
def mirror_x(m): m = m.copy(); m.apply_transform(np.diag([-1, 1, 1, 1])); m.invert(); return m
PANEL = "#222428"; PRINT = "#3d8fd6"; WOOD = "#d9b37c"; PLUG = "#f2c14e"; PI = "#2e8b57"; PSU = "#b8bcc2"; FUSE = "#c0392b"
centers = [-640, -320, 0, 320, 640]
for cx in centers:
    add(bx(cx-159.5, -80, -15, cx+159.5, 80, 0), PANEL)
    for s in (-1, 1):                                   # 16-pin plugs near each end (+ ribbon stub)
        add(bx(cx + s*160 - s*55, -17, 0, cx + s*160 - s*18, 17, 11) if s < 0 else bx(cx + 160 - 55, -17, 0, cx + 160 - 18, 17, 11), PLUG)
    add(bx(cx - 8, -12, 0, cx + 8, 12, 10), "#ffffff")  # 4-pin power plug
for sx in (-480, -160, 160, 480):                       # straps, top + bottom of every seam
    s = stl("ticker-seam-strap.stl"); s.apply_translation([sx, 0, 0]); add(s, PRINT); add(mirror_y(s), PRINT)
def plate(name, cx):
    p = stl(name); p.apply_translation([cx, 0, LEG]); add(p, PRINT)
    for x in (-31, 31):
        for y in (-66, 66):
            l = stl("ticker-leg.stl"); l.apply_translation([cx + x, y, 0]); add(l, "#5aa9e6")
plate("ticker-pi-mount.stl", 0)
add(bx(55.0-42.5, -28, 26, 55.0+42.5, 28, 27.6), PI)              # Pi 4
add(bx(55.0-32.5, -28, 38, 55.0+32.5, 28, 39.6), "#1f5f3a")       # bonnet
add(bx(-52.5-44, -44, 24, -52.5+44, 44, 61), FUSE)                # fuse block
plate("ticker-psu-plate.stl", -320)
add(bx(-320-98.5, -49, 18, -320+98.5, 49, 63), PSU)               # 5V 30A supply
for i, cx in enumerate(centers):                                  # edge clips, top + bottom
    x = cx - (117 if cx in (0, -320) else 31)
    c = stl("ticker-edge-clip.stl"); c.apply_translation([x, 0, 0]); add(c, PRINT); add(mirror_y(c), PRINT)
eb = stl("ticker-end-bracket.stl"); eb.apply_translation([-800, 0, 0]); add(eb, PRINT)
es = mirror_x(stl("ticker-end-bracket-switch.stl")); es.apply_translation([800, 0, 0]); add(es, PRINT)
for s in (-1, 1):                                                  # 1x4 frame (19 x 89)
    add(bx(-819, 80, -15, 819, 99, 74) if s > 0 else bx(-819, -99, -15, 819, -80, 74), WOOD)
    add(bx(800, -99, -15, 819, 99, 74) if s > 0 else bx(-819, -99, -15, -800, 99, 74), WOOD)
sw = pv.Cylinder(center=(819, 0, 55 - 15), direction=(1, 0, 0), radius=10, height=4)

def render(fname, cam, focal, zoom, size=(1800, 900), skip_wood_near=False):
    pl = pv.Plotter(off_screen=True, window_size=size); pl.set_background("white")
    for m, c in parts:
        if skip_wood_near and c == WOOD and m.bounds[0][1] < -79: continue
        pl.add_mesh(pv.wrap(m), color=c, smooth_shading=False, specular=0.2)
    pl.add_mesh(sw, color="red")
    pl.camera_position = [cam, focal, (0, 0, 1)]; pl.camera.zoom(zoom)
    pl.enable_eye_dome_lighting() if False else None
    pl.screenshot(fname); pl.close()
render("render-mid-full.png", (300, -1500, 1300), (0, 0, 0), 1.25)
render("render-mid-pi-psu.png", (-150, -520, 520), (-160, 0, 20), 1.0, (1600, 1000), skip_wood_near=True)
render("render-mid-seam.png", (230, -260, 260), (160, 0, 0), 1.0, (1400, 1000), skip_wood_near=True)
print("ok")
