# Bar ticker: 3D-printable Pi 4 mount (flat base with standoffs, vents, screw ears, 40 mm fan wall)
import manifold3d as m, numpy as np, trimesh
C, Cy = m.Manifold.cube, m.Manifold.cylinder
def box(x0, y0, z0, x1, y1, z1): return C([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])
def cyl(x, y, z0, h, d, n=48): return Cy(h, d / 2, d / 2, n).translate([x, y, z0])

T = 3.0                    # plate thickness
SO_H, SO_D = 6.0, 6.5      # standoff height / outside diameter
HOLE = 2.4                 # M2.5 screws thread straight into the plastic (or use 2.7 + nuts)
PI_W, PI_H = 85.0, 56.0
HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]
X0, X1, Y0, Y1 = -8.0, 92.0, -5.0, 61.0        # plate outline (room for the fan wall and the USB/Ethernet end)

plate = box(X0, Y0, 0, X1, Y1, T)
# 4 screw ears for mounting to the back board (#6 / 3.5-4 mm wood screws)
for ex in (X0 + 12, X1 - 12):
    for ey, dy in ((Y0, -12), (Y1, 12)):
        y_a, y_b = (ey + dy, ey) if dy < 0 else (ey, ey + dy)
        ear = box(ex - 9, y_a, 0, ex + 9, y_b, T) + cyl(ex, ey + dy, 0, T, 18)
        plate = plate + ear
        plate = plate - cyl(ex, ey + dy, -1, T + 2, 4.5)
# vent slots under the processor
for x in np.arange(14, 74, 6):
    plate = plate - (box(x, 9, -1, x + 3, 47, T + 1))
# standoffs with screw holes
for (hx, hy) in HOLES:
    plate = plate + cyl(hx, hy, T, SO_H, SO_D)
for (hx, hy) in HOLES:
    plate = plate - cyl(hx, hy, -1, T + SO_H + 2, HOLE, 32)

# 40 mm fan wall at the SD-card end, blowing along the Pi toward the USB/Ethernet end
WT, FW_H = 3.0, 46.0
wall = box(X0, Y0, 0, X0 + WT, Y1, FW_H)
fx, fy, fz = X0, (Y0 + Y1) / 2, T + 3 + 20               # fan center
fan_hole = Cy(WT + 2, 19, 19, 64).rotate([0, 90, 0]).translate([fx - 1, fy, fz])
wall = wall - fan_hole
for dy in (-16, 16):
    for dz in (-16, 16):
        wall = wall - Cy(WT + 2, 1.7, 1.7, 24).rotate([0, 90, 0]).translate([fx - 1, fy + dy, fz + dz])
# notch at the bottom of the wall so the microSD card can still go in and out
wall = wall - box(X0 - 1, 28 - 9, T - 0.01, X0 + WT + 1, 28 + 9, T + 8)
# two small gussets so the wall stays stiff
for gy in (Y0 + 2, Y1 - 5):
    g = box(X0 + WT, gy, T, X0 + WT + 3, gy + 3, T + 15)
    wall = wall + g

part = plate + wall
mesh = part.to_mesh()
tm = trimesh.Trimesh(vertices=np.array(mesh.vert_properties)[:, :3], faces=np.array(mesh.tri_verts))
print("watertight:", tm.is_watertight, "size mm:", np.round(tm.extents, 1), "volume cm3:", round(tm.volume / 1000, 1))
tm.export("hardware/ticker-pi4-mount.stl")
# no-fan version: just the base
mesh2 = plate.to_mesh()
tm2 = trimesh.Trimesh(vertices=np.array(mesh2.vert_properties)[:, :3], faces=np.array(mesh2.tri_verts))
tm2.export("hardware/ticker-pi4-mount-nofan.stl")
# quick picture
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
fig = plt.figure(figsize=(8, 6)); ax = fig.add_subplot(111, projection="3d")
ax.add_collection3d(Poly3DCollection(tm.vertices[tm.faces], facecolor="#4a90d9", edgecolor="#1f3b5c", linewidths=0.05, alpha=1))
ax.set_xlim(-30, 115); ax.set_ylim(-35, 90); ax.set_zlim(0, 80); ax.view_init(28, -55); ax.set_axis_off()
plt.tight_layout(); plt.savefig("hardware/pi4_mount.png", dpi=110)
