# Bar ticker: 3D-printable joiner strap that holds two LED panels together across the seam.
# Screws into each panel's brass M3 inserts. The holes are long slots, so the exact insert spacing
# doesn't matter: anywhere from SLOT_NEAR to SLOT_FAR mm from the seam on each side will line up.
# Use 2 per seam (top row of inserts + bottom row): 4 seams x 2 = 8 straps for 5 panels.
import manifold3d as m, numpy as np, trimesh
C, Cy = m.Manifold.cube, m.Manifold.cylinder
def box(x0, y0, z0, x1, y1, z1): return C([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])
def cyl(x, y, z0, h, d, n=48): return Cy(h, d / 2, d / 2, n).translate([x, y, z0])
def slot(x0, x1, y, z0, h, d):                     # rounded slot along x
    return box(x0, y - d / 2, z0, x1, y + d / 2, z0 + h) + cyl(x0, y, z0, h, d) + cyl(x1, y, z0, h, d)

L, W = 140.0, 20.0         # strap length (centered on the seam) and width
T = 4.0                    # strap thickness
PAD = 2.0                  # feet under the screw areas so the strap sits on the insert bosses, not the ribs
RIB_H, RIB_W = 4.0, 2.4    # stiffening ribs along both edges
SLOT_NEAR, SLOT_FAR = 20.0, 62.0   # slot range, measured from the seam
SLOT_W = 3.4               # M3 clearance
HEAD_W = 7.0               # room for the screw head / washer between the ribs

strap = box(-L / 2, -W / 2, PAD, L / 2, W / 2, PAD + T)
for s in (-1, 1):          # feet under each slot area
    a, b = sorted((s * (SLOT_NEAR - 6), s * (SLOT_FAR + 6)))
    strap = strap + box(a, -W / 2, 0, b, W / 2, PAD)
# edge ribs on top
for y0 in (-W / 2, W / 2 - RIB_W):
    strap = strap + box(-L / 2, y0, PAD + T, L / 2, y0 + RIB_W, PAD + T + RIB_H)
# seam marker notch so it's easy to center over the joint
strap = strap - box(-0.6, -W / 2 - 1, PAD + T - 0.8, 0.6, W / 2 + 1, PAD + T + RIB_H + 1)
# screw slots
for s in (-1, 1):
    a, b = sorted((s * SLOT_NEAR, s * SLOT_FAR))
    strap = strap - slot(a, b, 0, -1, PAD + T + 2, SLOT_W)

mesh = strap.to_mesh()
tm = trimesh.Trimesh(vertices=np.array(mesh.vert_properties)[:, :3], faces=np.array(mesh.tri_verts))
print("watertight:", tm.is_watertight, "size mm:", np.round(tm.extents, 1), "volume cm3:", round(tm.volume / 1000, 1))
# print it upside down (ribs on the bed? no: flat top of the feet on the bed) - feet down is fine, no supports
tm.export("ticker-panel-joiner.stl")

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
fig = plt.figure(figsize=(9, 4)); ax = fig.add_subplot(111, projection="3d")
ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#5aa0e0", edgecolor="#1d3d5e", linewidths=0.15))
ax.set_xlim(-75, 75); ax.set_ylim(-75, 75); ax.set_zlim(-10, 40); ax.view_init(35, -60); ax.set_axis_off()
plt.tight_layout(); plt.savefig("ticker-panel-joiner.png", dpi=130)
