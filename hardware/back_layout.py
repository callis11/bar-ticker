# Bar ticker: back-view layout drawing (looking at the back, so Panel 1 is on the RIGHT).
# Dimensions in mm. Insert positions are estimates from photos; check one panel before drilling or cutting.
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, FancyBboxPatch

PW, PH, N = 320, 160, 5
WOOD = 19                          # 3/4" boards
ROWS = (14, 141)                   # top / bottom insert rows (from the top edge)
ROW_X = (38, 122, 198, 282)        # inserts along each row, from a panel's left edge (back view)
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
# end brackets (90-degree flange against the side boards)
for xe, flip in ((0, 1), (W, -1)):
    x0 = xe if flip == 1 else xe - 30
    ax.add_patch(Rectangle((x0, END_INS[1] - 14), 30, 28, facecolor="#5aa0e0", edgecolor="#1d3d5e", lw=1, zorder=4))
    ax.add_patch(Circle((xe + flip * END_INS[0], END_INS[1]), 2.5, color="#ddd", zorder=5))
# joiner straps across each seam
for k in range(1, N):
    for ry in ROWS:
        ax.add_patch(Rectangle((k * PW - 70, ry - 9), 140, 18, facecolor="#5aa0e0", edgecolor="#1d3d5e", lw=1, zorder=3))
# steel bars
for ry in ROWS:
    ax.add_patch(Rectangle((32, ry - BAR_W / 2), W - 64, BAR_W, facecolor="#9aa3ad", edgecolor="#4b5560", lw=1, alpha=0.55, zorder=6))
# magnets
for i in range(N):
    for ry in ROWS:
        for rx in ROW_X:
            ax.add_patch(Circle((i * PW + rx, ry), 6.5, facecolor="#d94040", edgecolor="#701010", lw=0.8, zorder=7))
# power supply + Pi (stand off the back on the steel bars / a back board)
ps = FancyBboxPatch((PW * 1 + 50, 25), 215, 110, boxstyle="round,pad=1", facecolor="#c9ccd1", edgecolor="#555", lw=1.2, zorder=8)
ax.add_patch(ps); ax.text(PW * 1 + 157, 80, "5V 30A\npower supply", ha="center", va="center", fontsize=10, zorder=9)
pi = FancyBboxPatch((PW * 2 + 110, 37), 100, 66, boxstyle="round,pad=1", facecolor="#3fa34d", edgecolor="#1e5a26", lw=1.2, zorder=8)
ax.add_patch(pi); ax.text(PW * 2 + 160, 70, "Pi 4 + Bonnet\n(on printed mount)", ha="center", va="center", fontsize=9, color="white", zorder=9)
# ribbons from the Bonnet to each chain's first panel (back view: Panel 1 far right, 3 middle-right, 5 far left)
for tx, lab in ((PW * 4 + 160, "Port 1 -> Panel 1 IN"), (PW * 2 + 160, ""), (PW * 0 + 160, "Port 3 -> Panel 5 IN")):
    ax.plot([PW * 2 + 160, tx], [103, 120], color="#bbb", lw=2, ls="--", zorder=7)
ax.text(PW * 2 + 160, 128, "Port 2 -> Panel 3 IN", ha="center", fontsize=8, color="#ddd", zorder=9)
# labels
ax.set_xlim(-60, W + 60); ax.set_ylim(PH + 45, -70)
ax.set_title("Bar ticker - BACK view (Panel 1 is on the right from behind)    ~63\" x 6.3\" panels, 3/4\" wood frame", fontsize=13)
legend = [("#5aa0e0", "printed joiner straps (8) + end brackets (2)"), ("#9aa3ad", "steel flat bar 1\" x 1/8\" x 60.5\" (2), held by magnets"),
          ("#d94040", "M3 magnets in the brass inserts"), ("#c8955a", "wood frame (sides screw to the end-bracket flanges)")]
for i, (c, t) in enumerate(legend):
    ax.add_patch(Rectangle((i * 420, -58), 22, 16, facecolor=c, edgecolor="#333"))
    ax.text(i * 420 + 30, -50, t, va="center", fontsize=10)
plt.tight_layout(); plt.savefig("ticker-back-layout.png", dpi=110)
print("ok")
