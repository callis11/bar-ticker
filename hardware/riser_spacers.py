# Bar ticker: optional riser spacers, only if the cords need more room once it's assembled.
# One file each, 6 mm tall. Stack two for 12 mm. Use screws that are longer by the added height.
import manifold3d as m, numpy as np, trimesh
Cy = m.Manifold.cylinder
def tube(od, idia, h): return Cy(h, od / 2, od / 2, 48) - Cy(h + 2, idia / 2, idia / 2, 32).translate([0, 0, -1])
def save(p, name):
    mm = p.to_mesh()
    trimesh.Trimesh(np.array(mm.vert_properties)[:, :3], np.array(mm.tri_verts)).export(name); print(name)
H = 6.0
save(tube(6.5, 2.8, H), "ticker-riser-pi.stl")              # under each Pi corner (M2.5 screw passes through)  x4
save(tube(9.0, 4.4, H), "ticker-riser-fuse.stl")            # under each fuse-block corner (#8 screw)          x4
post = m.Manifold.cube([10, 10, H]).translate([-5, -5, 0]) - Cy(H + 2, 1.7, 1.7, 32).translate([0, 0, -1])
save(post, "ticker-riser-deck.stl")                         # on each power-supply post (M3 screw)              x4
