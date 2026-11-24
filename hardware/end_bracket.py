# Bar ticker: end bracket.
# The end bracket screws into the single brass insert near each SHORT end of the end panels (about 12 mm in
# from the edge, off-center), which is clear of the top/bottom insert rows where the steel bars and magnets go.
# It gives a 90-degree flange flush with the panel's end edge for screwing on the wooden side boards.
# Print 2 (one per end; the part is symmetric so it works on both ends). Screw: M3 x 8 mm.
import manifold3d as m, numpy as np, trimesh
C, Cy = m.Manifold.cube, m.Manifold.cylinder
def box(x0, y0, z0, x1, y1, z1): return C([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])
def cyl(x, y, z0, h, d, n=48): return Cy(h, d / 2, d / 2, n).translate([x, y, z0])
def save(part, name):
    mesh = part.to_mesh()
    tm = trimesh.Trimesh(vertices=np.array(mesh.vert_properties)[:, :3], faces=np.array(mesh.tri_verts))
    print(name, "watertight:", tm.is_watertight, "size mm:", np.round(tm.extents, 1), "cm3:", round(tm.volume / 1000, 1))
    tm.export(name); return tm

# x = along the ticker (0 = the panel's end edge, + toward the middle), y = up/down, z = away from the panel back
INS_X = 12.0               # end insert: distance in from the panel's end edge (measured ~11-14 mm from the photo)
SLOT_Y = 7.0               # slot lets the bracket slide +/- 7 mm up/down to find the insert
BASE_T = 4.0
W = 28.0                   # narrow, so it stays between the steel bars
L = 30.0
FL_T, FL_H = 5.0, 45.0     # flange thickness / how far it sticks out from the panel back

part = box(0, -W / 2, 0, L, W / 2, BASE_T)
part = part + box(0, -W / 2, 0, FL_T, W / 2, FL_H)
for s in (-1, 1):                                    # side gussets
    a, b = sorted((s * W / 2, s * (W / 2 - 3)))
    part = part + m.Manifold.batch_hull([box(0, a, 0, FL_T, b, FL_H - 4), box(0, a, 0, L, b, BASE_T)])
# vertical slot for the M3 screw + a counterbore so the head sits flush
part = part - (box(INS_X - 1.7, -SLOT_Y, -1, INS_X + 1.7, SLOT_Y, BASE_T + 2)
               + cyl(INS_X, -SLOT_Y, -1, BASE_T + 2, 3.4) + cyl(INS_X, SLOT_Y, -1, BASE_T + 2, 3.4))
part = part - (box(INS_X - 3.2, -SLOT_Y, BASE_T - 1.5, INS_X + 3.2, SLOT_Y, BASE_T + 0.01)
               + cyl(INS_X, -SLOT_Y, BASE_T - 1.5, 1.6, 6.4) + cyl(INS_X, SLOT_Y, BASE_T - 1.5, 1.6, 6.4))
# two wood-screw holes through the flange (#6 / 3.5 mm), countersunk on the inside face
for zc in (20.0, 36.0):
    part = part - Cy(FL_T + 2, 2.0, 2.0, 32).rotate([0, 90, 0]).translate([-1, 0, zc])
    part = part - Cy(2.4, 2.0, 4.2, 32).rotate([0, 90, 0]).translate([FL_T - 2.39, 0, zc])
tm = save(part, "ticker-end-bracket.stl")

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
fig = plt.figure(figsize=(6, 6)); ax = fig.add_subplot(111, projection="3d")
ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#5aa0e0", edgecolor="#1d3d5e", linewidths=0.2))
ax.set_xlim(-10, 40); ax.set_ylim(-25, 25); ax.set_zlim(0, 50); ax.view_init(25, -130); ax.set_axis_off()
plt.tight_layout(); plt.savefig("ticker-end-bracket.png", dpi=120)
