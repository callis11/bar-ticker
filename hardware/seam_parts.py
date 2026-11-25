# Bar ticker: printed parts that hold the 5 LED panels together, using the brass inserts near each panel's
# SHORT ends (brass M4 inserts). Every panel has two there: ~15 mm in from the end edge, ~37-39 mm in from each long edge
# (measured from the 11/23 tape photo). At a seam that's 4 inserts (2 per panel), all in the middle of the panel,
# so the long-edge insert rows stay free for the edge clips that hang the panels from the 1x4 frame.
#
#   ticker-seam-strap.stl      flat joiner, top or bottom of EVERY seam (middle stays open for the 16-pin plugs)  print 8
#   ticker-pi-mount.stl        Pi 4 + 6-way fuse block plate, middle of Panel 3, on 4 legs  print 1
#   ticker-psu-plate.stl       5V supply plate, middle of Panel 2 or 4, on 4 legs  print 1
#   ticker-leg.stl             14 mm leg block under the two plates  print 8
#   ticker-edge-clip.stl       long edges: insert -> 90-degree flange screwed to the top/bottom 1x4  print 10
#   ticker-end-bracket.stl     outer ends: flange for the wood side board  print 1
#   ticker-end-bracket-switch.stl  same, with a notch in the flange for the power switch  print 1
#   ticker-end-washer.stl      slider washer under each M4 screw (windows are oversized)  print 30
#
# Panel inserts are M4 (confirmed 11/24). Screws: M4 x 10 mm (+ printed washer) for flat parts; M4 x 30 + extra washers as needed for the raised plates (Pi, PSU). Windows allow +/-6 mm along and +/-4 mm up/down,
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

# ---- MID-PANEL MOUNTS (11/25): the 16-pin plugs sit right at every seam (both panels, middle height), so nothing
# crosses a seam through the middle any more. Every seam gets 2 straps (top + bottom). The Pi + fuse block plate
# and the power-supply plate each sit in the MIDDLE of a panel, between that panel's two 16-pin plugs, raised on
# 4 printed legs (ticker-leg.stl) that screw into the middle pair of long-edge inserts.
# Coordinates for these two parts: x = 0 at the panel center, along the ticker; y = 0 at the panel center line.
MID_X, MID_Y = 31.0, 66.0          # middle pair of long-edge inserts: +/-31 mm from panel center, 14 mm in from each edge
TAB = WIN_X + 4.7                  # half-width of the tab that reaches out to each insert

def mid_plate(hl, hy):
    """Flat plate, +/-hl long, +/-hy tall, with 4 tabs out to the middle long-edge inserts (windows through)."""
    p = box(-hl, -hy, 0, hl, hy, T)
    for x in (-MID_X, MID_X):
        for s in (-1, 1):
            y0, y1 = sorted((0.0, s * (MID_Y + WIN_Y + 4.7)))
            p = p + rbox(x - TAB, y0, x + TAB, y1, 0, T, 3.0)
    return p

def mid_windows(p):
    for x in (-MID_X, MID_X):
        for s in (-1, 1):
            y = s * MID_Y
            p = p - rbox(x - WIN_X - 1.7, y - WIN_Y - 1.7, x + WIN_X + 1.7, y + WIN_Y + 1.7, -1, T + 8, 2.0)
    return p

# Pi 4 + 6-way fuse block plate (Panel 3). Plugs are ~105+ mm from the panel center, so the plate stays within +/-102.5.
# Fuse block: GaRhomxy 6-way, 88 x 88 x 37 mm, corner holes 70.1 x 72.6 mm (listing). Pi USB/Ethernet end faces +x.
PI_HOLES = [(-29, -24.5), (29, -24.5), (-29, 24.5), (29, 24.5)]   # 58 x 49 mm pattern
SO_H = 8.0
PLATE_HL = 102.5
PI_CX, FB_CX = 52.5, -52.5
FB_DX, FB_DY = 70.1, 72.6
FB_BOSS_H = 6.0
pim = mid_plate(PLATE_HL, H / 2)
for y in (-H / 2 + 1.5, H / 2 - 1.5):
    pim = pim + box(-PLATE_HL, y - 1.5, T, PLATE_HL, y + 1.5, T + 4)
for (hx, hy) in PI_HOLES:
    pim = pim + cyl(PI_CX + hx, hy, T, SO_H, 6.5)
for sx in (-1, 1):
    for sy in (-1, 1):
        pim = pim + cyl(FB_CX + sx * FB_DX / 2, sy * FB_DY / 2, T, FB_BOSS_H, 9.0)
for x in np.arange(-36, 37, 8):                                    # vents under the Pi
    if abs(x) > 4:
        pim = pim - box(PI_CX + x - 1.5, -18, -1, PI_CX + x + 1.5, 18, T + 1)
for (hx, hy) in PI_HOLES:
    pim = pim - cyl(PI_CX + hx, hy, -1, T + SO_H + 2, 2.4, 32)    # M2.5 screws thread into the plastic
for sx in (-1, 1):
    for sy in (-1, 1):                                             # slotted pilots: +/-1.5 mm for listing tolerance
        x, y = FB_CX + sx * FB_DX / 2, sy * FB_DY / 2
        pim = pim - rbox(x - 3.15, y - 3.15, x + 3.15, y + 3.15, T + 1, FB_BOSS_H, 1.65) - cyl(x, y, -1, T + FB_BOSS_H + 2, 3.3, 24)
pim = mid_windows(pim)
save(pim, "ticker-pi-mount.stl")
leg = box(-WIN_X - 4.7, -WIN_Y - 4.7, 0, WIN_X + 4.7, WIN_Y + 4.7, LEG) \
      - rbox(-WIN_X - 1.7, -WIN_Y - 1.7, WIN_X + 1.7, WIN_Y + 1.7, -1, LEG + 2, 2.0)
save(leg, "ticker-leg.stl")        # 4 under the Pi plate + 4 under the supply plate = 8

# Power-supply plate (Panel 2 or 4): one piece (fits the K1C), the supply bolts to it from underneath with
# 2 x M4 x 8 + regular M4 washers (do this on the bench first), then the plate screws down beside the supply.
# Supply: 197 mm long, ~98-100 wide; 2 threaded M4 holes in the bottom along one long edge, 4.7" (119.4 mm) apart,
# ~18 mm from the non-terminal end. Slots let the across-the-width position be anywhere from 28 to 50 mm off center.
PSU_L = 197.0
PSU_HOLE_DX = 119.4
PSU_HOLE_FROM_END = 18.0
PSU_HOLE_CX = -PSU_L / 2 + PSU_HOLE_FROM_END + PSU_HOLE_DX / 2
PSU_BOLT_X = (PSU_HOLE_CX - PSU_HOLE_DX / 2, PSU_HOLE_CX + PSU_HOLE_DX / 2)
PSU_HL, PSU_HY = 104.0, 56.0
psu = mid_plate(PSU_HL, PSU_HY)
for y in (-PSU_HY + 1.5, PSU_HY - 1.5):                            # stiffening lips along both long edges
    psu = psu + box(-PSU_HL, y - 1.5, T, PSU_HL, y + 1.5, T + 5)
for hx in PSU_BOLT_X:                                              # M4 slots for the supply bolts
    for s_ in (-1, 1):
        a, b = sorted((s_ * 28.0, s_ * 50.0))
        psu = psu - rbox(hx - 2.25, a, hx + 2.25, b, -1, T + 2, 2.2)
for x in np.arange(-90, 91, 12):                                   # vents under the supply
    if abs(x) > 8:
        psu = psu - box(x - 2, -22, -1, x + 2, 22, T + 1)
psu = psu - box(-PSU_L / 2, -25.4, T - 0.5, -PSU_L / 2 + 0.8, 25.4, T + 1)   # scored line: non-terminal end of the supply
psu = mid_windows(psu)
save(psu, "ticker-psu-plate.stl")

# ---- NO STEEL: the 1x4 frame is the backbone. The top and bottom boards run the full length; the panels hang
# from them with small edge clips, and the side boards screw to the end brackets.

# ---- edge clip: screws into a long-edge insert (the rows ~14 mm in from each long edge) and has a 90-degree
# flange flush with the panel's long edge for screwing into the top or bottom 1x4. 1 per panel per edge = 10.
# On the 2 panels with a mid-panel plate, the middle inserts are taken: use an outer long-edge insert there.
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
save(rbox(-9, -7, 9, 7, 0, 2.0, 2.5) - cyl(0, 0, -1, 4.0, 4.8, 32), "ticker-end-washer.stl")   # M4 clearance (+ allowance: printed holes come out small)

# ---- preview sheet
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
names = ["ticker-seam-strap.stl", "ticker-pi-mount.stl", "ticker-psu-plate.stl", "ticker-end-bracket.stl", "ticker-edge-clip.stl", "ticker-leg.stl", "ticker-end-washer.stl"]
fig = plt.figure(figsize=(28, 5))
for i, n in enumerate(names):
    tm = trimesh.load(n); ax = fig.add_subplot(1, 7, i + 1, projection="3d")
    ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#5aa0e0", edgecolor="#1d3d5e", linewidths=0.1))
    ax.set_xlim(-140, 140); ax.set_ylim(-140, 140); ax.set_zlim(0, 70); ax.view_init(35, -60); ax.set_axis_off()
    ax.set_title(n.replace("ticker-", "").replace(".stl", ""))
plt.tight_layout(); plt.savefig("ticker-seam-parts.png", dpi=100)
