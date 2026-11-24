# Bar ticker: 3D-printable joiner strap that holds two LED panels together across the seam.
# Held by 2 recessed M3 button-head screws (M3 x 6) into the panels' brass inserts. No magnets at these
# inserts: the magnets go in the other inserts, straight into the panel, so they all sit at the same height.
# The strap stays low (7 mm) so the steel bar passes over it on the magnets (~12 mm).
# The holes are long slots, so the exact insert spacing doesn't matter (SLOT_NEAR..SLOT_FAR from the seam).
# Use 2 per seam (top row of inserts + bottom row): 4 seams x 2 = 8 straps for 5 panels.
import manifold3d as m, numpy as np, trimesh
C, Cy = m.Manifold.cube, m.Manifold.cylinder
def box(x0, y0, z0, x1, y1, z1): return C([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])
def cyl(x, y, z0, h, d, n=48): return Cy(h, d / 2, d / 2, n).translate([x, y, z0])
def slot(x0, x1, y, z0, h, d): return box(x0, y - d / 2, z0, x1, y + d / 2, z0 + h) + cyl(x0, y, z0, h, d) + cyl(x1, y, z0, h, d)

L, W = 140.0, 18.0         # strap length (centered on the seam) and width
T = 4.0                    # strap thickness
FLOOR = 1.5                # plastic left under the screw head (so an M3 x 6 still gets ~4.5 mm of thread)
RIB_H, RIB_W = 3.0, 2.0    # stiffening ribs along both edges (top ends at 7 mm, under the steel bar)
SLOT_NEAR, SLOT_FAR = 20.0, 62.0
SLOT_W, HEAD_W = 3.4, 6.2  # M3 clearance / button-head recess

strap = box(-L / 2, -W / 2, 0, L / 2, W / 2, T)
for y0 in (-W / 2, W / 2 - RIB_W):
    strap = strap + box(-L / 2, y0, T, L / 2, y0 + RIB_W, T + RIB_H)
strap = strap - box(-0.6, -W / 2 - 1, T - 0.8, 0.6, W / 2 + 1, T + RIB_H + 1)      # seam marker
for s in (-1, 1):
    a, b = sorted((s * SLOT_NEAR, s * SLOT_FAR))
    strap = strap - slot(a, b, 0, -1, T + 2, SLOT_W)
    strap = strap - slot(a, b, 0, FLOOR, T, HEAD_W)                                 # recess for the heads

mesh = strap.to_mesh()
tm = trimesh.Trimesh(vertices=np.array(mesh.vert_properties)[:, :3], faces=np.array(mesh.tri_verts))
print("watertight:", tm.is_watertight, "size mm:", np.round(tm.extents, 1), "volume cm3:", round(tm.volume / 1000, 1))
tm.export("ticker-panel-joiner.stl")

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
fig = plt.figure(figsize=(9, 4)); ax = fig.add_subplot(111, projection="3d")
ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#5aa0e0", edgecolor="#1d3d5e", linewidths=0.15))
ax.set_xlim(-75, 75); ax.set_ylim(-75, 75); ax.set_zlim(-10, 40); ax.view_init(35, -60); ax.set_axis_off()
plt.tight_layout(); plt.savefig("ticker-panel-joiner.png", dpi=130)
