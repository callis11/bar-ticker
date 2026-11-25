# Bar ticker: printed parts that hold the 5 LED panels together, using the brass inserts near each panel's
# SHORT ends (brass M4 inserts). Every panel has two there: ~15 mm in from the end edge, ~37-39 mm in from each long edge
# (measured from the 11/23 tape photo). At a seam that's 4 inserts (2 per panel), all in the middle of the panel,
# so the long-edge insert rows stay free for the edge clips that hang the panels from the 1x4 frame.
#
#   ticker-seam-strap.stl      flat joiner, top or bottom of a plain seam   print 4
#   ticker-seam-pi-mount.stl   joiner with the Pi 4 mount + 6-way fuse block mount (sits on 6 legs)  print 1
#   ticker-leg.stl             14 mm leg block under the Pi mount              print 6
#   ticker-seam-psu-base-A/B.stl  joiner with 4 posts for the power-supply deck, 2 halves (butt cut, K1C bed)  print 1 each
#   ticker-psu-deck-A/B.stl       deck the 5V supply bolts to (2 x M4), 2 halves, each with 1 supply bolt  print 1 each
#   ticker-edge-clip.stl       long edges: insert -> 90-degree flange screwed to the top/bottom 1x4  print 10
#   ticker-end-bracket.stl     outer ends: flange for the wood side board  print 1
#   ticker-end-bracket-switch.stl  same, with a notch in the flange for the power switch  print 1
#   ticker-end-washer.stl      slider washer under each M3 screw (windows are oversized)  print 30
#
# Panel inserts are M4 (confirmed 11/24). Screws: M4 x 10 mm (+ printed washer) for flat parts; M4 x 25 for the raised parts (Pi mount, PSU base). Windows allow +/-6 mm along and +/-4 mm up/down,
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
H = 108.0                          # plate height (stays clear of the edge clips at |y| >= 55)

def windows(part, xs):
    for x in xs:
        for s in (-1, 1):
            y = s * INS_Y
            part = part - rbox(x - WIN_X - 1.7, y - WIN_Y - 1.7, x + WIN_X + 1.7, y + WIN_Y + 1.7, -1, T + 2, 2.0)
    return part

LEG = 14.0                         # raised parts: plate underside this high off the panel frame, so the ribbon
                                   # connectors (IN/OUT, 1-2" in from each end, center of the panel) and the power
                                   # plug fit underneath. Screws for raised parts: M4 x 25.
def legs(part, xs, ys=(-INS_Y, INS_Y)):
    """Hollow legs under a plate that sits at z=LEG..LEG+T: one around each insert, window straight through."""
    for x in xs:
        for y in ys:
            part = part + box(x - WIN_X - 4.7, y - WIN_Y - 4.7, 0, x + WIN_X + 4.7, y + WIN_Y + 4.7, LEG)
    return part

def raised_windows(part, xs, ys=(-INS_Y, INS_Y)):
    for x in xs:
        for y in ys:
            part = part - rbox(x - WIN_X - 1.7, y - WIN_Y - 1.7, x + WIN_X + 1.7, y + WIN_Y + 1.7, -1, LEG + T + 2, 2.0)
    return part

def feet(part, pts, size=10.0):
    """Plain support feet (no screw) down to the panel frame."""
    for (x, y) in pts:
        part = part + box(x - size / 2, y - size / 2, 0, x + size / 2, y + size / 2, LEG)
    return part

def ribs(part, x0, x1, ys=(-H / 2 + 1.5, H / 2 - 1.5), h=5.0):
    for y in ys:
        part = part + box(x0, y - 1.5, T, x1, y + 1.5, T + h)
    return part

def split_x0(part, big=400.0):
    """Cut a long part in two at x=0 (butt cut). Each half prints flat with no supports."""
    return part ^ box(-big, -big, -big, 0, big, big), part ^ box(0, -big, -big, big, big, big)

def seam_marker(part, h):
    return part - box(-0.6, -H / 2 - 1, T - 0.8, 0.6, H / 2 + 1, T + h + 1)

# ---- seam strap: flat, joins two panels at a seam using ONE end insert on each panel. Two per seam (top and
# bottom), leaving the middle of the panel open for the ribbon connectors. Print 4 (2 plain seams).
STRAP_Y0, STRAP_Y1 = 31.0, 54.0
strap = box(-30, STRAP_Y0, 0, 30, STRAP_Y1, T) + box(-30, STRAP_Y1 - 3, T, 30, STRAP_Y1, T + 4)
strap = strap - box(-0.6, STRAP_Y0 - 1, T - 0.8, 0.6, STRAP_Y1 + 1, T + 5)          # seam marker
for x in (-INS_X, INS_X):
    strap = strap - rbox(x - WIN_X - 1.7, INS_Y - WIN_Y - 1.7, x + WIN_X + 1.7, INS_Y + WIN_Y + 1.7, -1, T + 6, 2.0)
save(strap, "ticker-seam-strap.stl")

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
pim = box(PX0, -H / 2, LEG, 50, H / 2, LEG + T)
pim = legs(pim, (-INS_X, INS_X))
pim = feet(pim, [(PX0 + 6, -40), (PX0 + 6, 40)])
for y in (-H / 2 + 1.5, H / 2 - 1.5):
    pim = pim + box(PX0, y - 1.5, LEG + T, 50, y + 1.5, LEG + T + 4)
for (hx, hy) in PI_HOLES:
    pim = pim + cyl(hx, hy, LEG + T, SO_H, 6.5)
for sx in (-1, 1):
    for sy in (-1, 1):
        pim = pim + cyl(FB_CX + sx * FB_DX / 2, sy * FB_DY / 2, LEG + T, FB_BOSS_H, 9.0)
for x in np.arange(-36, 37, 8):                                    # vents under the Pi
    if abs(x) > 4:
        pim = pim - box(x - 1.5, -18, LEG - 1, x + 1.5, 18, LEG + T + 1)
pim = raised_windows(pim, (-INS_X, INS_X))
for (hx, hy) in PI_HOLES:
    pim = pim - cyl(hx, hy, LEG - 1, T + SO_H + 2, 2.4, 32)               # M2.5 screws thread into the plastic
for sx in (-1, 1):
    for sy in (-1, 1):                                             # slightly slotted pilots: +/-1.5 mm for listing tolerance
        x, y = FB_CX + sx * FB_DX / 2, sy * FB_DY / 2
        pim = pim - rbox(x - 1.65 - 1.5, y - 1.65 - 1.5, x + 1.65 + 1.5, y + 1.65 + 1.5, LEG + T + 1, FB_BOSS_H, 1.65) \
                  - cyl(x, y, LEG - 1, T + FB_BOSS_H + 2, 3.3, 24)
pim = pim - box(-0.6, -H / 2 - 1, LEG + T - 0.8, 0.6, H / 2 + 1, LEG + T + 6)      # seam marker
# print-friendly: the plate prints flat (legs cut off) and stands on 6 separate leg blocks (ticker-leg.stl)
pim_flat = (pim ^ box(-400, -400, LEG, 400, 400, 400)).translate([0, 0, -LEG])
save(pim_flat, "ticker-seam-pi-mount.stl")
leg = box(-WIN_X - 4.7, -WIN_Y - 4.7, 0, WIN_X + 4.7, WIN_Y + 4.7, LEG) \
      - rbox(-WIN_X - 1.7, -WIN_Y - 1.7, WIN_X + 1.7, WIN_Y + 1.7, -1, LEG + 2, 2.0)
save(leg, "ticker-leg.stl")        # 4 under the Pi mount's screw windows + 2 under its far end (glue those 2)

# ---- power supply mount, 2 pieces (bolt the supply to the deck FIRST, then screw the deck onto the base):
#   base: seam joiner (4 end inserts) with 4 posts out past the supply's ends
#   deck: plate that rides on the posts above the base; the supply bolts to it with 2 x M4 + fender washers
# Supply: 7 3/4" (197 mm) long; 2 threaded M4 holes in the bottom along one long edge, 4.7" (119 mm) apart,
# closer to one end (~18 mm and ~139 mm from that end, from the photo). The deck has slotted windows so the
# across-the-width position and a few mm along don't matter.
PSU_L = 197.0
PSU_HOLE_DX = 119.4                       # 4.7" center to center
PSU_HOLE_FROM_END = 18.0                  # first hole from the near end (photo estimate)
PSU_HOLE_CX = -PSU_L / 2 + PSU_HOLE_FROM_END + PSU_HOLE_DX / 2   # midpoint of the 2 holes, supply centered at x=0
DECK_T = 4.0
BL = 120.0                                # base/deck half-length (2 x 120 mm halves for the K1C)
DECK_X, DECK_Y = 112.0, 30.0              # deck -> base screws (M3 x 12 + nut, outside the supply's ends)
BASE_H = 104.0
DECK_Z = LEG + T                          # deck sits right on the raised base (= 18 mm)
PSU_BOLT_X = (PSU_HOLE_CX - PSU_HOLE_DX / 2, PSU_HOLE_CX + PSU_HOLE_DX / 2)
psub = box(-BL, -BASE_H / 2, LEG, BL, BASE_H / 2, LEG + T)
psub = legs(psub, (-INS_X, INS_X))
psub = feet(psub, [(-DECK_X, -42), (-DECK_X, 42), (DECK_X, -42), (DECK_X, 42)])
for x in PSU_BOLT_X:                                               # room for the M4 bolt heads under the deck
    for s_ in (-1, 1):
        a, b = sorted((s_ * 28.0, s_ * 53.0))
        psub = psub - rbox(x - 9, a, x + 9, b, LEG - 1, T + 2, 3.0)
for x in (-DECK_X, DECK_X):                                        # M3 clearance + nut trap underneath
    for y in (-DECK_Y, DECK_Y):
        psub = psub - cyl(x, y, LEG - 1, T + 2, 3.4, 24)
        psub = psub - Cy(2.6, 3.2, 3.2, 6).translate([x, y, LEG - 0.01])
psub = raised_windows(psub, (-INS_X, INS_X))
psub = psub - box(-0.6, -BASE_H / 2 - 1, LEG + T - 0.8, 0.6, BASE_H / 2 + 1, LEG + T + 1)   # seam marker
a_, b_ = split_x0(psub)        # 2 halves: each screws to its own panel's 2 inserts; the deck + supply tie them together
# print these two UPSIDE DOWN (flat top on the bed, legs up): no supports needed
flip = lambda p: p.mirror([0, 0, 1]).translate([0, 0, LEG + T])
save(flip(a_), "ticker-seam-psu-base-A.stl"); save(flip(b_), "ticker-seam-psu-base-B.stl")

deck = box(-BL, -60, 0, BL, 60, DECK_T)
for y in (-58.5, 58.5):                                           # stiffening lips along both long edges
    deck = deck + box(-BL, y - 1.5, DECK_T, BL, y + 1.5, DECK_T + 5)
for px in (-DECK_X, DECK_X):                                      # countersunk holes onto the base (nut underneath)
    for py in (-DECK_Y, DECK_Y):
        deck = deck - cyl(px, py, -1, DECK_T + 2, 3.4, 24)
        deck = deck - Cy(2.0, 1.7, 3.6, 24).translate([px, py, DECK_T - 1.99])
for hx in PSU_BOLT_X:                                                 # M4 windows for the supply
    for s_ in (-1, 1):
        a, b = sorted((s_ * 32.0, s_ * 55.0))
        deck = deck - rbox(hx - 4.5, a, hx + 4.5, b, -1, DECK_T + 2, 2.2)
for x in np.arange(-90, 91, 12):                                   # vents under the supply
    if abs(x) > 8:
        deck = deck - box(x - 2, -22, -1, x + 2, 22, DECK_T + 1)
# outline of the supply's footprint scored lightly on top, so it's obvious which way it sits
deck = deck - box(-PSU_L / 2, -0.4 - 25, DECK_T - 0.5, -PSU_L / 2 + 0.8, 25.4, DECK_T + 1)
a_, b_ = split_x0(deck)        # 2 halves: each has 2 posts + 1 supply bolt, so the supply itself joins them
save(a_, "ticker-psu-deck-A.stl"); save(b_, "ticker-psu-deck-B.stl")

# ---- NO STEEL: the 1x4 frame is the backbone. The top and bottom boards run the full length; the panels hang
# from them with small edge clips, and the side boards screw to the end brackets.

# ---- edge clip: screws into a long-edge insert (the rows ~14 mm in from each long edge) and has a 90-degree
# flange flush with the panel's long edge for screwing into the top or bottom 1x4. 1 per panel per edge = 10.
ROW_IN = 14.0                      # long-edge insert row, measured in from the panel edge (photo estimate)
PANEL_H = 160.0
EDGE = PANEL_H / 2                 # panel edge, from the center line
CL_W = 30.0                        # clip width along the ticker
CL_Y0 = 55.0                       # base starts here: clears the seam parts / PSU base (|y| <= 54)
FL_T, FL_H = 5.0, 45.0
clip = box(-CL_W / 2, CL_Y0, 0, CL_W / 2, EDGE, T) + box(-CL_W / 2, EDGE - FL_T, 0, CL_W / 2, EDGE, FL_H)
for xc in (-CL_W / 2 + 1.5, CL_W / 2 - 1.5):                       # gussets
    clip = clip + m.Manifold.batch_hull([box(xc - 1.5, EDGE - FL_T, 0, xc + 1.5, EDGE, FL_H - 4),
                                         box(xc - 1.5, CL_Y0, 0, xc + 1.5, EDGE, T)])
yi = EDGE - ROW_IN
clip = clip - rbox(-WIN_X - 1.7, yi - 5 - 1.7, WIN_X + 1.7, yi + 5 + 1.7, -1, T + 2, 2.0)   # insert window
for zc in (20.0, 36.0):                                            # #6 wood screws, countersunk on the inside face
    clip = clip - Cy(FL_T + 2, 2.0, 2.0, 32).rotate([-90, 0, 0]).translate([0, EDGE - FL_T - 1, zc])
    clip = clip - Cy(2.4, 4.2, 2.0, 32).rotate([-90, 0, 0]).translate([0, EDGE - FL_T - 0.01, zc])
save(clip, "ticker-edge-clip.stl")

# ---- end bracket (outer ends): full panel height, screws into the 2 short-end inserts, 90-degree flange flush with
# the end edge for the wood side board.
L = 30.0
eb = box(0, -PANEL_H / 2, 0, L, PANEL_H / 2, T) + box(0, -PANEL_H / 2, 0, FL_T, PANEL_H / 2, FL_H)
eb = eb - box(FL_T, -30, -1, L + 1, 30, T + 1)                   # open middle: ribbon connector clearance
for yc in (-PANEL_H / 2 + 1.5, -31.5, 31.5, PANEL_H / 2 - 1.5):
    eb = eb + m.Manifold.batch_hull([box(0, yc - 1.5, 0, FL_T, yc + 1.5, FL_H - 4), box(0, yc - 1.5, 0, L, yc + 1.5, T)])
eb = windows(eb, (INS_X,))
FL_HOLES = (-66.0, -36.0, -12.0, 12.0, 36.0, 66.0)
for yc in FL_HOLES:                                                # #6 wood screws along the whole height
    eb = eb - Cy(FL_T + 2, 2.0, 2.0, 32).rotate([0, 90, 0]).translate([-1, yc, 32])
    eb = eb - Cy(2.4, 2.0, 4.2, 32).rotate([0, 90, 0]).translate([FL_T - 2.39, yc, 32])
save(eb, "ticker-end-bracket.stl")
# switch-end version: notch in the top of the flange so a 20 mm round rocker switch (and the ~35 mm pocket routed
# behind it in the side board) has room. Switch center: panel center height, 55 mm out from the panel back
# (= 70 mm / 2 3/4" back from the front edge of a 1x4 side board).
SW_Z, SW_NOTCH = 55.0, 20.0
eb_sw = eb - box(-1, -SW_NOTCH, SW_Z - 25, L + 1, SW_NOTCH, FL_H + 1)
save(eb_sw, "ticker-end-bracket-switch.stl")

# ---- slider washer: bridges a window under the M4 screw head (the panel inserts are M4)
save(rbox(-9, -7, 9, 7, 0, 2.0, 2.5) - cyl(0, 0, -1, 4.0, 4.5, 32), "ticker-end-washer.stl")   # M4 clearance

# ---- preview sheet
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
names = ["ticker-seam-strap.stl", "ticker-seam-pi-mount.stl", "ticker-seam-psu-base-A.stl", "ticker-seam-psu-base-B.stl", "ticker-psu-deck-A.stl", "ticker-psu-deck-B.stl", "ticker-end-bracket.stl", "ticker-edge-clip.stl", "ticker-leg.stl"]
fig = plt.figure(figsize=(34, 5))
for i, n in enumerate(names):
    tm = trimesh.load(n); ax = fig.add_subplot(1, 9, i + 1, projection="3d")
    ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#5aa0e0", edgecolor="#1d3d5e", linewidths=0.1))
    ax.set_xlim(-140, 140); ax.set_ylim(-140, 140); ax.set_zlim(0, 70); ax.view_init(35, -60); ax.set_axis_off()
    ax.set_title(n.replace("ticker-", "").replace(".stl", ""))
plt.tight_layout(); plt.savefig("ticker-seam-parts.png", dpi=100)
