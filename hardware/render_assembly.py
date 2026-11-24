# Bar ticker: 3D rendering of the back of the assembled ticker (panels, printed parts, steel bars, magnets,
# power supply, Pi, wood frame). World: x along the ticker (0-1600 mm), y up the panel (0-160), z out the back.
import numpy as np, trimesh, manifold3d as m
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

def tbox(x0, y0, z0, x1, y1, z1):
    return trimesh.creation.box(extents=[x1 - x0, y1 - y0, z1 - z0]).apply_translation([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
def tcyl(x, y, z0, h, d, n=20):
    return trimesh.creation.cylinder(radius=d / 2, height=h, sections=n).apply_translation([x, y, z0 + h / 2])
def part(name, x, flip=False):
    tm = trimesh.load(name)
    if flip:
        tm.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [0, 0, 1]))
    return tm.apply_translation([x, 80, 0])

PW, PH, N = 320, 160, 5
W = PW * N
groups = {}
def add(key, mesh): groups.setdefault(key, []).append(mesh)

for i in range(N):                                                    # panels (front faces -z)
    add("panel", tbox(i * PW + 0.6, 0, -15, (i + 1) * PW - 0.6, PH, 0))
add("print", part("ticker-seam-plate.stl", PW * 1))
add("print", part("ticker-seam-psu-cradle.stl", PW * 2))
add("print", part("ticker-seam-pi-mount.stl", PW * 3))
add("print", part("ticker-seam-plate.stl", PW * 4))
add("print", part("ticker-end-bracket.stl", 0))
add("print", part("ticker-end-bracket.stl", W, flip=True))
for ry in (14, 146):                                                  # steel bars + magnets
    add("steel", tbox(5, ry - 12.7, 12, W - 5, ry + 12.7, 15.2))
    for i in range(N):
        for rx in (50, 122, 198, 270):
            add("magnet", tcyl(i * PW + rx, ry, 0, 12, 12.5))
add("psu", tbox(PW * 2 - 107, 80 - 57, 22, PW * 2 + 107, 80 + 57, 72))  # 5V 30A supply on the cradle
add("pi", tbox(PW * 3 - 42.5, 80 - 28, 12, PW * 3 + 42.5, 80 + 28, 13.5))   # Pi 4
add("bonnet", tbox(PW * 3 - 32.5, 80 - 28, 30, PW * 3 + 32.5, 80 + 28, 31.6))  # Triple Bonnet on the tall header
WOOD, DEPTH = 19, 75                                                  # 3/4" boards, 75 mm deep (covers panel + parts)
add("wood", tbox(-WOOD, -WOOD, -15, 0, PH + WOOD, DEPTH - 15))
add("wood", tbox(W, -WOOD, -15, W + WOOD, PH + WOOD, DEPTH - 15))
add("wood", tbox(0, -WOOD, -15, W, 0, DEPTH - 15))
add("wood", tbox(0, PH, -15, W, PH + WOOD, DEPTH - 15))

colors = {"panel": "#2a2a2a", "print": "#4f97da", "steel": "#a7b0ba", "magnet": "#d23c3c",
          "psu": "#cfd3d8", "pi": "#2f9a45", "bonnet": "#1b1b1b", "wood": "#c8955a"}
import pyvista as pv
pv.OFF_SCREEN = True
def scene(wood_opacity=1.0, size=(2400, 1100)):
    p = pv.Plotter(off_screen=True, window_size=size)
    p.set_background("white")
    for k, meshes in groups.items():
        tm = trimesh.util.concatenate(meshes)
        faces = np.hstack([np.full((len(tm.faces), 1), 3), tm.faces]).ravel()
        pm = pv.PolyData(tm.vertices, faces)
        p.add_mesh(pm, color=colors[k], smooth_shading=False, specular=0.3 if k in ("steel", "magnet") else 0.1,
                   opacity=wood_opacity if k == "wood" else 1.0)
    p.enable_anti_aliasing("ssaa")
    return p

# 1) whole ticker from behind
p = scene()
p.camera_position = [(W / 2, -1300, 1500), (W / 2, 80, 0), (0, 0, 1)]
p.camera.zoom(1.0)
p.add_text("Bar ticker - back, fully assembled", font_size=14, color="black")
p.screenshot("ticker-render-full.png")

# 2) close-up: Pi seam, supply seam and the right end bracket (wood see-through)
p = scene(wood_opacity=0.25)
p.camera_position = [(W - 250, -420, 520), (W - 330, 80, 10), (0, 0, 1)]
p.add_text("Close-up: Pi seam mount, seam plate, end bracket holding both bars (wood shown see-through)", font_size=12, color="black")
p.screenshot("ticker-render-closeup.png")

p = scene(wood_opacity=0.25)
p.camera_position = [(PW * 2.5, -480, 560), (PW * 2.5, 80, 10), (0, 0, 1)]
p.add_text("Close-up: power supply on its cradle (left) and the Pi + Bonnet (right)", font_size=12, color="black")
p.screenshot("ticker-render-middle.png")
print("ok")

# ---- front views with a real ticker frame on the LEDs
def front_scene(size=(2400, 900)):
    p = scene(size=size)
    tex = pv.read_texture("/tmp/ticker_face.png") if os.path.exists("/tmp/ticker_face.png") else None
    face = pv.Plane(center=(W / 2, PH / 2, -15.2), direction=(0, 0, -1), i_size=W, j_size=PH)
    face.texture_map_to_plane(inplace=True)
    if tex is not None:
        face.active_texture_coordinates[:, 0] = 1 - face.active_texture_coordinates[:, 0]   # viewed from the front
        p.add_mesh(face, texture=tex, lighting=False)
    return p
import os
p = front_scene()
p.camera_position = [(W / 2, 80, -2600), (W / 2, 80, 0), (0, 1, 0)]
p.camera.zoom(1.55)
p.add_text("Bar ticker - FRONT", font_size=14, color="black")
p.screenshot("ticker-render-front.png")
p = front_scene()
p.camera_position = [(W * 0.15, -700, -1500), (W / 2, 80, 0), (0, 1, 0)]
p.add_text("Bar ticker - front, from below (how it looks above the bar)", font_size=14, color="black")
p.screenshot("ticker-render-front-angle.png")
print("front ok")
