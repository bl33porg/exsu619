# Makes a load cell calibration curve from calibration_data.csv
# Run with:  python3 make_curve.py

import os
import numpy as np
import matplotlib.pyplot as plt

# read and save files in the same folder as this script
FOLDER = os.path.dirname(os.path.abspath(__file__))
os.chdir(FOLDER)

CSV_FILE = "calibration_data.csv"   # file with your CSV lines
MAX_NOISE = 500    # readings noisier than this are thrown out (hand moving)
MAX_ERROR = 1.5    # readings more than this many grams off are thrown out

# ---- read the file ----
reps, dirs, masses, raws, noises = [], [], [], [], []
for line in open(CSV_FILE):
    line = line.strip()
    if line.startswith("CSV,"):
        line = line[4:]                  # remove "CSV," at the start
    parts = line.split(",")
    if len(parts) < 4 or not parts[0].isdigit():
        continue                         # skip header and other lines
    reps.append(int(parts[0]))
    dirs.append(parts[1])
    masses.append(float(parts[2]))
    raws.append(float(parts[3]))
    noises.append(float(parts[4]) if len(parts) > 4 else 0)

mass = np.array(masses)
raw = np.array(raws)
direction = np.array(dirs)
noise = np.array(noises)

# ---- fit a straight line, throwing out bad readings ----
good = noise < MAX_NOISE
factor, offset = np.polyfit(mass[good], raw[good], 1)
error = (raw - offset) / factor - mass
good = good & (abs(error) < MAX_ERROR)
factor, offset = np.polyfit(mass[good], raw[good], 1)
error = (raw - offset) / factor - mass

predicted = factor * mass + offset
r2 = 1 - np.sum((raw[good] - predicted[good]) ** 2) / np.sum((raw[good] - raw[good].mean()) ** 2)
rms = np.sqrt(np.mean(error[good] ** 2))

print("CALIBRATION FACTOR =", round(factor, 1), "counts per gram")
print("OFFSET =", round(offset, 1))
print("R2 =", round(r2, 6))
print("RMS error =", round(rms, 2), "g")
print("Thrown out:", int((~good).sum()), "readings")

# ---- draw the graph ----
fig, (top, bottom) = plt.subplots(2, 1, figsize=(8, 8), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.4]})

for d, marker, color, name in [("up", "o", "#2563eb", "Loading (weights added)"),
                               ("down", "s", "#ea580c", "Unloading (weights removed)")]:
    pick = good & (direction == d)
    top.scatter(mass[pick], raw[pick] / 1000, marker=marker, facecolors="none",
                edgecolors=color, s=45, label=name)
    bottom.scatter(mass[pick], error[pick], marker=marker, facecolors="none",
                   edgecolors=color, s=45)

top.scatter(mass[~good], raw[~good] / 1000, marker="x", color="red", s=60, label="Thrown out")
bottom.scatter(mass[~good], error[~good], marker="x", color="red", s=60)

x = np.linspace(0, mass.max() * 1.05, 2)
top.plot(x, (factor * x + offset) / 1000, color="black", lw=1.2,
         label=f"Fit: raw = {factor:.1f}·m {offset:+.0f}\nR² = {r2:.6f}")
top.set_ylabel("HX711 raw reading (×1000 counts)")
top.set_title("Load cell calibration curve")
top.legend(loc="upper left", fontsize=9)
top.grid(alpha=0.3)

bottom.axhline(0, color="black", lw=1)
bottom.set_ylabel("Error (g)")
bottom.set_xlabel("Applied mass (g)")
bottom.grid(alpha=0.3)
bottom.text(0.99, 0.06, f"RMS error {rms:.2f} g", transform=bottom.transAxes,
            ha="right", fontsize=9)

plt.tight_layout()
plt.savefig("calibration_curve.png", dpi=200)
print("Saved", os.path.join(FOLDER, "calibration_curve.png"))
plt.show()
