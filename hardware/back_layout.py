# Bar ticker: back-view layout drawing (looking at the back, so Panel 1 is on the RIGHT).
# Dimensions in mm. Insert positions are estimates from photos; check one panel before drilling or cutting.
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, FancyBboxPatch

PW, PH, N = 320, 160, 5
WOOD = 19                          # 3/4" boards
ROWS = (14, 141)                   # top / bottom insert rows (from the top edge)
ROW_X = (50, 122, 198, 270)        # inserts along each row, from a panel's left edge (back view)
END_INS = (12, 118)                # insert near each short end: (in from the end, down from the top)
BAR_W = 25.4

fig, ax = plt.subplots(figsize=(18, 4.6))
ax.set_aspect("equal"); ax.set_axis_off()
W = PW * N
# wood frame
for r in (Rectangle((-WOOD, -WOOD), W + 2 * WOOD, WOOD), Rectangle((-WOOD, PH), W + 2 * WOOD, WOOD),
          Rectangle((-WOOD, 0), WOOD, PH), Rectangle((W, 0), WOOD, PH)):
    r.set(facecolor="#c8955a", edgecolor="#7a5230", lw=1); ax.add_patch(r)
# panels
for i in range(N):
    ax.add_patch(Rectangle((i * PW, 0), PW, PH, facecolor="#2b2b2b", edgecolor="#555", lw=1.2))
    ax.text(i * PW + PW / 2, 75, f"Panel {N - i}", color="#888", ha="center", va="center", fontsize=11)
# end inserts (2 at each short end of every panel) -> seam parts + end brackets
H = 108
def part(xc, w, color, label):
    ax.add_patch(Rectangle((xc - w / 2, PH / 2 - H / 2), w, H, facecolor=color, edgecolor="#1d3d5e", lw=1.2, zorder=4))
    if label:
        ax.text(xc, PH / 2 + H / 2 + 12, label, ha="center", fontsize=8.5, zorder=9)
for xc in (PW * 1, PW * 4):
    part(xc, 60, "#5aa0e0", "seam plate")
for xe in (15, W - 15):
    ax.add_patch(Rectangle((xe - 15, 0), 30, PH, facecolor="#5aa0e0", edgecolor="#1d3d5e", lw=1.2, zorder=7))
    ax.text(xe, PH + 12, "end bracket", ha="center", fontsize=8.5, zorder=9)
part(PW * 2, 120, "#5aa0e0", "")
part(PW * 3, 100, "#5aa0e0", "")
for k in range(0, N + 1):
    for sx in (-1, 1):
        x = k * PW + sx * 15
        if 0 < x < W:
            for sy in (-1, 1):
                ax.add_patch(Circle((x, PH / 2 + sy * 42), 3, facecolor="#f2f2f2", edgecolor="#333", lw=0.8, zorder=10))
# steel bars (full length, ends captured in the end brackets)
for ry in ROWS:
    ax.add_patch(Rectangle((5, ry - BAR_W / 2), W - 10, BAR_W, facecolor="#9aa3ad", edgecolor="#4b5560", lw=1, alpha=0.6, zorder=6))
# magnets in every long-edge insert
for i in range(N):
    for ry in ROWS:
        for rx in ROW_X:
            ax.add_patch(Circle((i * PW + rx, ry), 6.5, facecolor="#d94040", edgecolor="#701010", lw=0.8, zorder=7))
# power supply rides above the bars on the cradle; Pi on its seam mount
ax.add_patch(FancyBboxPatch((PW * 2 - 107, 22), 214, 116, boxstyle="round,pad=1", facecolor="#c9ccd1", edgecolor="#555", lw=1.2, alpha=0.85, zorder=8))
ax.text(PW * 2, 80, "5V 30A supply\n(on seam cradle)", ha="center", va="center", fontsize=10, zorder=9)
ax.add_patch(FancyBboxPatch((PW * 3 - 42.5, PH / 2 - 28), 85, 56, boxstyle="round,pad=1", facecolor="#3fa34d", edgecolor="#1e5a26", lw=1.2, zorder=8))
ax.text(PW * 3, PH / 2, "Pi 4 + Bonnet\n(seam Pi mount)", ha="center", va="center", fontsize=8.5, color="white", zorder=9)
for tx in (PW * 4 + 160, PW * 2 + 160, PW * 0 + 160):
    ax.plot([PW * 3, tx], [PH / 2 + 28, 128], color="#bbb", lw=2, ls="--", zorder=7)
# labels
ax.set_xlim(-60, W + 60); ax.set_ylim(PH + 45, -70)
ax.set_title("Bar ticker - BACK view (Panel 1 is on the right from behind)    ~63\" x 6.3\" panels, 3/4\" wood frame", fontsize=13)
legend = [("#5aa0e0", "printed seam parts (4) + end brackets (2), white = M3 screws"), ("#9aa3ad", "steel flat bar 1\" x 1/8\" x 62.5\" (2), held by magnets"),
          ("#d94040", "magnets (every long-edge insert)"), ("#c8955a", "wood frame (sides screw to the end-bracket flanges)")]
for i, (c, t) in enumerate(legend):
    ax.add_patch(Rectangle((i * 460 - 40, -58), 22, 16, facecolor=c, edgecolor="#333"))
    ax.text(i * 460 - 10, -50, t, va="center", fontsize=10)
plt.tight_layout(); plt.savefig("ticker-back-layout.png", dpi=110)
print("ok")
