# Bar ticker: printed parts that hold the 5 LED panels together, using the brass inserts near each panel's
# SHORT ends. Every panel has two there: ~15 mm in from the end edge, ~37-39 mm in from each long edge
# (measured from the 11/23 tape photo). At a seam that's 4 inserts (2 per panel), all between the steel bars,
# so the long-edge insert rows stay free for the magnets that hold the bars.
#
#   ticker-seam-plate.stl      plain joiner for a seam                      print 2
#   ticker-seam-pi-mount.stl   joiner with the Pi 4 mount + 6-way fuse block mount  print 1
#   ticker-seam-psu-base.stl   joiner with 4 posts for the power-supply deck       print 1
#   ticker-psu-deck.stl        deck the 5V supply bolts to (2 x M4), sits on the posts  print 1
#   ticker-end-bracket.stl     outer ends: holds both steel bar ends + flange for the wood sides  print 2
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

# ---- seam plate with the Pi 4 mount AND the 6-way fuse block (Pi centered on the seam, USB/Ethernet end
# facing +x; fuse block on the extension toward the power supply, -x).
# Fuse block: GaRhomxy 6-way with negative bus, 3.46" x 3.45" x 1.44" (88 x 88 x 37 mm); 4 corner mounting
# holes 2.76" x 2.86" (70.1 x 72.6 mm) apart per the listing. Bosses take #8 / M4 self-tapping screws.
PI_HOLES = [(-29, -24.5), (29, -24.5), (-29, 24.5), (29, 24.5)]   # 58 x 49 mm pattern
SO_H = 8.0                                                         # standoffs: Pi board clears the screw heads
FB_CX = -105.0                                                     # fuse block center (toward the supply)
FB_DX, FB_DY = 70.1, 72.6                                          # fuse block hole spacing
FB_BOSS_H = 6.0
PX0 = FB_CX - 50.0                                                 # plate starts just past the fuse block
pim = box(PX0, -H / 2, 0, 50, H / 2, T)
pim = ribs(pim, PX0, 50)
for (hx, hy) in PI_HOLES:
    pim = pim + cyl(hx, hy, T, SO_H, 6.5)
for sx in (-1, 1):
    for sy in (-1, 1):
        pim = pim + cyl(FB_CX + sx * FB_DX / 2, sy * FB_DY / 2, T, FB_BOSS_H, 9.0)
for x in np.arange(-36, 37, 8):                                    # vents under the Pi
    if abs(x) > 4:
        pim = pim - box(x - 1.5, -18, -1, x + 1.5, 18, T + 1)
pim = windows(pim, (-INS_X, INS_X))
for (hx, hy) in PI_HOLES:
    pim = pim - cyl(hx, hy, -1, T + SO_H + 2, 2.4, 32)               # M2.5 screws thread into the plastic
for sx in (-1, 1):
    for sy in (-1, 1):                                             # slightly slotted pilots: +/-1.5 mm for listing tolerance
        x, y = FB_CX + sx * FB_DX / 2, sy * FB_DY / 2
        pim = pim - rbox(x - 1.65 - 1.5, y - 1.65 - 1.5, x + 1.65 + 1.5, y + 1.65 + 1.5, T + 1, FB_BOSS_H, 1.65) \
                  - cyl(x, y, -1, T + FB_BOSS_H + 2, 3.3, 24)
pim = seam_marker(pim, 5)
save(pim, "ticker-seam-pi-mount.stl")

# ---- power supply mount, 2 pieces (bolt the supply to the deck FIRST, then screw the deck onto the base):
#   base: seam joiner (4 end inserts) with 4 posts out past the supply's ends
#   deck: plate that rides on the posts ABOVE the steel bars; the supply bolts to it with 2 x M4 + fender washers
# Supply: 7 3/4" (197 mm) long; 2 threaded M4 holes in the bottom along one long edge, 4.7" (119 mm) apart,
# closer to one end (~18 mm and ~139 mm from that end, from the photo). The deck has slotted windows so the
# across-the-width position and a few mm along don't matter.
PSU_L = 197.0
PSU_HOLE_DX = 119.4                       # 4.7" center to center
PSU_HOLE_FROM_END = 18.0                  # first hole from the near end (photo estimate)
PSU_HOLE_CX = -PSU_L / 2 + PSU_HOLE_FROM_END + PSU_HOLE_DX / 2   # midpoint of the 2 holes, supply centered at x=0
DECK_Z = 18.0                             # underside of the deck: above the bars (~16 mm)
DECK_T = 4.0
BL = 120.0                                # base/deck half-length (240 mm long: fits a 256 mm bed)
POST_X, POST_Y = 112.0, 30.0
BASE_H = 104.0                            # base stays between the bars where the posts are
psub = box(-BL, -BASE_H / 2, 0, BL, BASE_H / 2, T)
psub = ribs(psub, -BL, BL, ys=(-BASE_H / 2 + 1.5, BASE_H / 2 - 1.5))
for px in (-POST_X, POST_X):
    for py in (-POST_Y, POST_Y):
        psub = psub + box(px - 5, py - 5, T, px + 5, py + 5, DECK_Z)
        psub = psub - cyl(px, py, T, DECK_Z, 2.8, 24)          # M3 self-tapping / thread-forming screw
psub = windows(psub, (-INS_X, INS_X))
psub = seam_marker(psub, 0)
save(psub, "ticker-seam-psu-base.stl")

deck = box(-BL, -60, 0, BL, 60, DECK_T)
for y in (-58.5, 58.5):                                           # stiffening lips along both long edges
    deck = deck + box(-BL, y - 1.5, DECK_T, BL, y + 1.5, DECK_T + 5)
for px in (-POST_X, POST_X):                                      # countersunk holes onto the base posts
    for py in (-POST_Y, POST_Y):
        deck = deck - cyl(px, py, -1, DECK_T + 2, 3.4, 24)
        deck = deck - Cy(2.0, 1.7, 3.6, 24).translate([px, py, DECK_T - 1.99])
for hx in (PSU_HOLE_CX - PSU_HOLE_DX / 2, PSU_HOLE_CX + PSU_HOLE_DX / 2):   # M4 windows for the supply
    for s_ in (-1, 1):
        a, b = sorted((s_ * 32.0, s_ * 55.0))
        deck = deck - rbox(hx - 4.5, a, hx + 4.5, b, -1, DECK_T + 2, 2.2)
for x in np.arange(-90, 91, 12):                                   # vents under the supply
    deck = deck - box(x - 2, -22, -1, x + 2, 22, DECK_T + 1)
# outline of the supply's footprint scored lightly on top, so it's obvious which way it sits
deck = deck - box(-PSU_L / 2, -0.4 - 25, DECK_T - 0.5, -PSU_L / 2 + 0.8, 25.4, DECK_T + 1)
save(deck, "ticker-psu-deck.stl")

# ---- end bracket (outer ends): full panel height, 90-degree flange flush with the end edge for the wood side
# board (screw holes along the whole height), and pockets that capture the ends of both 1" steel bars, so the
# bars, the bracket and the side boards all tie together. Bars run the full length and butt against the flange.
PANEL_H = 160.0
ROW_Y = 66.0                       # steel bar centerline (long-edge insert rows, ~14 mm from the panel edge)
BAR_W, BAR_T = 25.4, 3.2
MAG_H = 12.0                       # magnet body height: the bar sits on the magnet faces at this height
L, FL_T, FL_H = 30.0, 5.0, 45.0
eb = box(0, -PANEL_H / 2, 0, L, PANEL_H / 2, T) + box(0, -PANEL_H / 2, 0, FL_T, PANEL_H / 2, FL_H)
for s_ in (-1, 1):
    y_in = ROW_Y - BAR_W / 2 - 0.3          # inner edge of the bar (+clearance)
    a, b = sorted((s_ * (y_in - 4.5), s_ * PANEL_H / 2))
    blk = box(0, a, 0, L, b, MAG_H + BAR_T + 0.4 + 3.0)                       # block around the bar end
    ba, bb = sorted((s_ * y_in, s_ * (PANEL_H / 2 + 1)))
    blk = blk - box(FL_T, ba, MAG_H, L + 1, bb, MAG_H + BAR_T + 0.4)          # bar slot (open on the inside and outer edge)
    la, lb = sorted((s_ * (y_in + 4.0), s_ * (PANEL_H / 2 + 1)))
    blk = blk - box(FL_T, la, MAG_H + BAR_T + 0.4 - 0.01, L + 1, lb, MAG_H + 20)  # lip only overlaps the bar's inner 4 mm
    eb = eb + blk
for yc in (0.0,):
    eb = eb + m.Manifold.batch_hull([box(0, yc - 1.5, 0, FL_T, yc + 1.5, FL_H - 4), box(0, yc - 1.5, 0, L, yc + 1.5, T)])
for s_ in (-1, 1):                                                 # gussets from the flange down onto the bar blocks
    yc = s_ * (ROW_Y - BAR_W / 2 - 3.0)
    eb = eb + m.Manifold.batch_hull([box(0, yc - 1.5, 0, FL_T, yc + 1.5, FL_H - 4), box(0, yc - 1.5, 0, L, yc + 1.5, 19)])
eb = windows(eb, (INS_X,))
for yc in (-66.0, -36.0, -12.0, 12.0, 36.0, 66.0):                 # #6 wood screws along the whole height
    eb = eb - Cy(FL_T + 2, 2.0, 2.0, 32).rotate([0, 90, 0]).translate([-1, yc, 32])
    eb = eb - Cy(2.4, 2.0, 4.2, 32).rotate([0, 90, 0]).translate([FL_T - 2.39, yc, 32])
save(eb, "ticker-end-bracket.stl")

# ---- slider washer: bridges a window under the M3 screw head
save(rbox(-9, -7, 9, 7, 0, 2.0, 2.5) - cyl(0, 0, -1, 4.0, 3.4, 32), "ticker-end-washer.stl")

# ---- preview sheet
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
names = ["ticker-seam-plate.stl", "ticker-seam-pi-mount.stl", "ticker-seam-psu-base.stl", "ticker-psu-deck.stl", "ticker-end-bracket.stl"]
fig = plt.figure(figsize=(20, 5))
for i, n in enumerate(names):
    tm = trimesh.load(n); ax = fig.add_subplot(1, 5, i + 1, projection="3d")
    ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#5aa0e0", edgecolor="#1d3d5e", linewidths=0.1))
    ax.set_xlim(-140, 140); ax.set_ylim(-140, 140); ax.set_zlim(0, 70); ax.view_init(35, -60); ax.set_axis_off()
    ax.set_title(n.replace("ticker-", "").replace(".stl", ""))
plt.tight_layout(); plt.savefig("ticker-seam-parts.png", dpi=100)
