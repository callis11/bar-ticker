# Bar ticker: printed parts that hold the 5 LED panels together, using the brass inserts near each panel's
# SHORT ends. Every panel has two there: ~15 mm in from the end edge, ~37-39 mm in from each long edge
# (measured from the 11/23 tape photo). At a seam that's 4 inserts (2 per panel), all between the steel bars,
# so the long-edge insert rows stay free for the magnets that hold the bars.
#
#   ticker-seam-plate.stl      plain joiner for a seam                      print 2
#   ticker-seam-pi-mount.stl   joiner with the Pi 4 mount built in         print 1
#   ticker-seam-psu-cradle.stl joiner with a zip-tie cradle for the 5V supply  print 1
#   ticker-end-bracket.stl     outer ends: 90-degree flange for the wood side boards  print 2
#   ticker-end-washer.stl      slider washer under each M3 screw (windows are oversized)  print 20
#
# Screws: M3 x 10 mm (+ printed washer) into every insert. Windows allow +/-6 mm along and +/-4 mm up/down,
# so small measuring errors don't matter. x = along the ticker (0 = seam or end edge), y = up/down
# (0 = panel center), z = away from the panel back.
import manifold3d as m, numpy as np, trimesh
C, Cy = m.Manifold.cube, m.Manifold.cylinder
def box(x0, y0, z0, x1, y1, z1): return C([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])
def cyl(x, y, z0, h, d, n=48): return Cy(h, d / 2, d / 2, n).translate([x, y, z0])
def rbox(x0, y0, x1, y1, z0, h, r):
    return m.Manifold.batch_hull([cyl(x, y, z0, h, 2 * r, 24) for x in (x0 + r, x1 - r) for y in (y0 + r, y1 - r)])
def save(part, name):
    mesh = part.to_mesh()
    tm = trimesh.Trimesh(vertices=np.array(mesh.vert_properties)[:, :3], faces=np.array(mesh.tri_verts))
    print(f"{name:28s} watertight={tm.is_watertight} size={np.round(tm.extents, 1)} cm3={round(tm.volume / 1000, 1)}")
    tm.export(name); return tm

INS_X, INS_Y = 15.0, 42.0          # insert offset from the end edge / from the panel center line
WIN_X, WIN_Y = 6.0, 4.0            # window half-size around each insert
T = 4.0                            # plate thickness
H = 108.0                          # plate height (fits between the steel bars)

def windows(part, xs):
    for x in xs:
        for s in (-1, 1):
            y = s * INS_Y
            part = part - rbox(x - WIN_X - 1.7, y - WIN_Y - 1.7, x + WIN_X + 1.7, y + WIN_Y + 1.7, -1, T + 2, 2.0)
    return part

def ribs(part, x0, x1, ys=(-H / 2 + 1.5, H / 2 - 1.5), h=5.0):
    for y in ys:
        part = part + box(x0, y - 1.5, T, x1, y + 1.5, T + h)
    return part

def seam_marker(part, h):
    return part - box(-0.6, -H / 2 - 1, T - 0.8, 0.6, H / 2 + 1, T + h + 1)

# ---- plain seam plate
plate = box(-30, -H / 2, 0, 30, H / 2, T)
plate = ribs(plate, -30, 30)
plate = seam_marker(windows(plate, (-INS_X, INS_X)), 5)
save(plate, "ticker-seam-plate.stl")

# ---- seam plate with the Pi 4 mount (Pi centered on the seam, USB/Ethernet end facing +x)
PI_HOLES = [(-29, -24.5), (29, -24.5), (-29, 24.5), (29, 24.5)]   # 58 x 49 mm pattern
SO_H = 8.0                                                         # standoffs: Pi board clears the screw heads
pim = box(-50, -H / 2, 0, 50, H / 2, T)
pim = ribs(pim, -50, 50)
for (hx, hy) in PI_HOLES:
    pim = pim + cyl(hx, hy, T, SO_H, 6.5)
for x in np.arange(-36, 37, 8):                                    # vents under the Pi
    if abs(x) > 4:
        pim = pim - box(x - 1.5, -18, -1, x + 1.5, 18, T + 1)
pim = windows(pim, (-INS_X, INS_X))
for (hx, hy) in PI_HOLES:
    pim = pim - cyl(hx, hy, -1, T + SO_H + 2, 2.4, 32)               # M2.5 screws thread into the plastic
pim = seam_marker(pim, 5)
save(pim, "ticker-seam-pi-mount.stl")

# ---- seam plate with a cradle for the power supply. The supply is taller (~110-115 mm) than the gap between
# the steel bars, so it rides on two raised rails ABOVE the bars and is held with 2 zip ties / velcro straps.
RAIL_Z = 22.0                                                      # top of the rails: above the bars (~16 mm)
psu = box(-60, -H / 2, 0, 60, H / 2, T)
RAILS = (-34.0, 28.0)                                              # rail y starts: inside the screw windows, clear of the bars
for y in RAILS:                                                    # two rails along x
    psu = psu + box(-60, y, T, 60, y + 6, RAIL_Z)
for x in (-35, 35):                                                # zip-tie slots through each rail (2 straps)
    for y in RAILS:
        psu = psu - box(x - 3, y - 1, RAIL_Z - 9, x + 3, y + 7, RAIL_Z - 5)
psu = psu + box(-60, -1.5, T, 60, 1.5, 10)                         # center rib
psu = windows(psu, (-INS_X, INS_X))
psu = seam_marker(psu, 0)
save(psu, "ticker-seam-psu-cradle.stl")

# ---- end bracket (outer ends): base on the panel back + 90-degree flange flush with the end edge
L, FL_T, FL_H = 30.0, 5.0, 45.0
eb = box(0, -H / 2, 0, L, H / 2, T) + box(0, -H / 2, 0, FL_T, H / 2, FL_H)
for yc in (-H / 2 + 1.5, 0.0, H / 2 - 1.5):
    eb = eb + m.Manifold.batch_hull([box(0, yc - 1.5, 0, FL_T, yc + 1.5, FL_H - 4), box(0, yc - 1.5, 0, L, yc + 1.5, T)])
eb = windows(eb, (INS_X,))
for yc in (-30.0, 30.0):                                           # #6 wood screws, countersunk on the inside
    eb = eb - Cy(FL_T + 2, 2.0, 2.0, 32).rotate([0, 90, 0]).translate([-1, yc, 30])
    eb = eb - Cy(2.4, 2.0, 4.2, 32).rotate([0, 90, 0]).translate([FL_T - 2.39, yc, 30])
save(eb, "ticker-end-bracket.stl")

# ---- slider washer: bridges a window under the M3 screw head
save(rbox(-9, -7, 9, 7, 0, 2.0, 2.5) - cyl(0, 0, -1, 4.0, 3.4, 32), "ticker-end-washer.stl")

# ---- preview sheet
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
names = ["ticker-seam-plate.stl", "ticker-seam-pi-mount.stl", "ticker-seam-psu-cradle.stl", "ticker-end-bracket.stl"]
fig = plt.figure(figsize=(16, 5))
for i, n in enumerate(names):
    tm = trimesh.load(n); ax = fig.add_subplot(1, 4, i + 1, projection="3d")
    ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#5aa0e0", edgecolor="#1d3d5e", linewidths=0.1))
    ax.set_xlim(-65, 65); ax.set_ylim(-65, 65); ax.set_zlim(0, 60); ax.view_init(35, -60); ax.set_axis_off()
    ax.set_title(n.replace("ticker-", "").replace(".stl", ""))
plt.tight_layout(); plt.savefig("ticker-seam-parts.png", dpi=100)
