## !/usr/bin/env python3
"""Drone Battery Flight Time Optimizer"""

import glob
import os

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

# ── Configuration ─────────────────────────────────────────────────────────────
NUM_MOTORS = 6
DRONE_EMPTY_WEIGHT_LBS = 5
INPUT_DATA_DIR = "Input Data"
OUTPUT_DATA_DIR = "Output Data"
THRUST_WEIGHT_RATIO = 2.0   # minimum required thrust:weight ratio


# ── Data Loading ──────────────────────────────────────────────────────────────

def load_battery_data(input_dir):
    path = os.path.join(input_dir, "battery list.xlsx")
    df = pd.read_excel(path, sheet_name="MaxAmps 3S LiPo Batteries", engine="openpyxl")
    return df[["Battery Name", "Capacity (mAh)", "Weight (lbs)",
               "Price ($)", "Max Continuous Discharge (Amps)"]].copy()


def load_dynamometer_data(input_dir):
    pattern = os.path.join(input_dir, "dynamometer_*.txt")
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No dynamometer file found matching: {pattern}")
    filepath = matches[0]
    filename = os.path.basename(filepath)
    df = pd.read_csv(filepath, comment="#")
    return df[["THRUST_LBS", "CURRENT_AMPS"]].copy(), filename


# ── Outlier Rejection ─────────────────────────────────────────────────────────

def _power_law(x, a, b):
    return a * np.power(x, b)


def remove_outliers(df):
    # Drop dead-band rows
    mask = (df["THRUST_LBS"] > 0) & (df["CURRENT_AMPS"] > 0)
    df_pos = df[mask].copy()

    # Fit temporary power law
    popt, _ = curve_fit(_power_law, df_pos["THRUST_LBS"], df_pos["CURRENT_AMPS"],
                        p0=[1.0, 1.5], maxfev=10000)
    predicted = _power_law(df_pos["THRUST_LBS"], *popt)
    residuals = df_pos["CURRENT_AMPS"] - predicted
    std = residuals.std()

    keep = np.abs(residuals) <= 2.5 * std
    df_clean = df_pos[keep].copy()
    n_removed = len(df_pos) - keep.sum()
    return df_clean, int(n_removed)


# ── Trendline Fitting ─────────────────────────────────────────────────────────

def fit_trendline(df_clean):
    popt, _ = curve_fit(_power_law, df_clean["THRUST_LBS"], df_clean["CURRENT_AMPS"],
                        p0=[1.0, 1.5], maxfev=10000)
    a, b = popt

    predicted = _power_law(df_clean["THRUST_LBS"], a, b)
    ss_res = np.sum((df_clean["CURRENT_AMPS"] - predicted) ** 2)
    ss_tot = np.sum((df_clean["CURRENT_AMPS"] - df_clean["CURRENT_AMPS"].mean()) ** 2)
    r2 = 1.0 - ss_res / ss_tot

    return a, b, r2


def predict_current(thrust, a, b):
    return a * thrust ** b


# ── Flight Time Calculation ───────────────────────────────────────────────────

def calculate_flight_times(batteries_df, a, b, drone_empty_weight, num_motors):
    results = batteries_df.copy()
    total_weights = drone_empty_weight + results["Weight (lbs)"]
    thrust_per_motor = total_weights / num_motors
    current_per_motor = predict_current(thrust_per_motor, a, b)
    total_current = current_per_motor * num_motors
    flight_time = (results["Capacity (mAh)"] / 1000.0) / total_current * 60.0

    results["Total_Weight_Lbs"] = total_weights
    results["Total_Current_A"] = total_current
    results["Flight_Time_Min"] = flight_time
    return results


# ── Safety Checks ─────────────────────────────────────────────────────────────

def apply_safety_checks(results_df, df_clean, num_motors, empty_weight):
    """
    Adds a 'Fail_Reasons' column (list of strings) and 'Safe' boolean column.

    Check 1 — Thrust:weight ratio
      Max thrust per motor from clean dyno data * num_motors >= 2 * total_weight

    Check 2 — Max continuous discharge
      num_motors * max clean current per motor <= battery max continuous discharge
    """
    max_thrust_per_motor = df_clean["THRUST_LBS"].max()
    max_current_per_motor = df_clean["CURRENT_AMPS"].max()
    max_total_thrust = max_thrust_per_motor * num_motors
    max_total_current = max_current_per_motor * num_motors

    fail_reasons = []
    for _, row in results_df.iterrows():
        reasons = []
        total_weight = row["Total_Weight_Lbs"]

        # Check 1: thrust:weight ratio
        ratio = max_total_thrust / total_weight
        if ratio < THRUST_WEIGHT_RATIO:
            reasons.append(
                f"Thrust:weight ratio {ratio:.2f}:1 < {THRUST_WEIGHT_RATIO:.1f}:1 required"
            )

        # Check 2: max current vs battery rating
        if max_total_current > row["Max Continuous Discharge (Amps)"]:
            reasons.append(
                f"Max burst current {max_total_current:.1f}A exceeds battery rating "
                f"{row['Max Continuous Discharge (Amps)']:.1f}A"
            )

        fail_reasons.append(reasons)

    results_df = results_df.copy()
    results_df["Fail_Reasons"] = fail_reasons
    results_df["Safe"] = results_df["Fail_Reasons"].apply(lambda r: len(r) == 0)
    results_df["Fail_TW"] = results_df["Fail_Reasons"].apply(
        lambda r: any("ratio" in s for s in r))
    results_df["Fail_Current"] = results_df["Fail_Reasons"].apply(
        lambda r: any("current" in s for s in r))
    return results_df, max_total_thrust, max_total_current


# ── 3D Scatter Plot ───────────────────────────────────────────────────────────

def plot_results(batteries_df, best_battery, output_dir):
    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(111, projection="3d")

    safe = batteries_df[batteries_df["Safe"]]
    fail_tw = batteries_df[batteries_df["Fail_TW"]]
    fail_cur = batteries_df[batteries_df["Fail_Current"] & ~batteries_df["Fail_TW"]]

    # Safe batteries (blue circles)
    if not safe.empty:
        ax.scatter(safe["Capacity (mAh)"], safe["Weight (lbs)"], safe["Flight_Time_Min"],
                   c="steelblue", s=60, alpha=0.8, label="Valid Battery")

    # Thrust:weight failures (red squares)
    if not fail_tw.empty:
        ax.scatter(fail_tw["Capacity (mAh)"], fail_tw["Weight (lbs)"], fail_tw["Flight_Time_Min"],
                   c="red", s=70, marker="s", alpha=0.8, label="Fails Thrust:Weight Ratio")

    # Current failures (red triangles)
    if not fail_cur.empty:
        ax.scatter(fail_cur["Capacity (mAh)"], fail_cur["Weight (lbs)"], fail_cur["Flight_Time_Min"],
                   c="red", s=70, marker="^", alpha=0.8, label="Fails Max Current Rating")

    # Best battery (green)
    if best_battery is not None:
        bx = best_battery["Capacity (mAh)"]
        by = best_battery["Weight (lbs)"]
        bz = best_battery["Flight_Time_Min"]
        ax.scatter([bx], [by], [bz], c="limegreen", s=180, edgecolors="green",
                   linewidths=1.5, zorder=5, label="Best Battery")
        ax.text(bx, by, bz + 0.3, best_battery["Battery Name"].split()[2],
                fontsize=8, color="green", ha="center")

    ax.set_xlabel("Capacity (mAh)")
    ax.set_ylabel("Battery Weight (lbs)")
    ax.set_zlabel("Flight Time (min)")
    ax.set_title("Drone Battery Flight Time Optimizer")
    ax.legend()

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "flight_time_optimizer.png")
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.show()
    return out_path


# ── Text Results File ─────────────────────────────────────────────────────────

def save_results_txt(batteries_df, best_battery, a, b, r2, dyno_filename,
                     num_motors, empty_weight, n_outliers,
                     max_total_thrust, max_total_current, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "flight_time_results.txt")

    lines = []
    sep = "=" * 60
    lines += [
        sep,
        "        DRONE BATTERY FLIGHT TIME OPTIMIZER",
        sep,
        "",
        "CALCULATION PARAMETERS:",
        f"  Number of Motors         : {num_motors}",
        f"  Empty Drone Weight       : {empty_weight:.2f} lbs",
        f"  Dynamometer File         : {dyno_filename}",
        f"  Trendline Model          : I = {a:.3f} * T^{b:.3f}  (R\u00b2 = {r2:.4f})",
        f"  Outliers Removed         : {n_outliers} point{'s' if n_outliers != 1 else ''}",
        f"  Min Thrust:Weight Ratio  : {THRUST_WEIGHT_RATIO:.1f}:1  "
        f"(max single-motor thrust = {max_total_thrust/num_motors:.3f} lbs, "
        f"max total thrust = {max_total_thrust:.3f} lbs)",
        f"  Max Burst Current        : {max_total_current:.2f} A total "
        f"({max_total_current/num_motors:.2f} A/motor x {num_motors} motors)",
        "",
        "RESULTS:",
    ]

    header = (f"  {'#':>2}  {'Battery Name':<48}  {'Cap':>6}  {'Wt':>6}  "
              f"{'Hover A':>8}  {'Time':>7}  Status")
    lines.append(header)
    lines.append("  " + "-" * (len(header) + 5))

    for i, row in batteries_df.iterrows():
        is_best = best_battery is not None and row["Battery Name"] == best_battery["Battery Name"]
        marker = " *" if is_best else "  "
        if row["Safe"]:
            status = "OK"
        else:
            status = "FAIL: " + "; ".join(row["Fail_Reasons"])
        lines.append(
            f"{marker}{i+1:>2}  {row['Battery Name']:<48}  "
            f"{int(row['Capacity (mAh)']):>6}  "
            f"{row['Weight (lbs)']:>6.3f}  "
            f"{row['Total_Current_A']:>7.2f}A  "
            f"{row['Flight_Time_Min']:>6.1f}m  "
            f"{status}"
        )

    lines += [""]
    if best_battery is not None:
        lines += [
            "OPTIMAL BATTERY:",
            f"  Name     : {best_battery['Battery Name']}",
            f"  Capacity : {int(best_battery['Capacity (mAh)'])} mAh",
            f"  Weight   : {best_battery['Weight (lbs)']:.3f} lbs",
            f"  Price    : ${best_battery['Price ($)']:.0f}",
            f"  Flight   : {best_battery['Flight_Time_Min']:.1f} minutes",
            f"  Hover    : {best_battery['Total_Current_A']:.1f} A total",
        ]
    else:
        lines.append("OPTIMAL BATTERY: None — all batteries failed safety checks.")
    lines.append(sep)

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


# ── Console Output ────────────────────────────────────────────────────────────

def print_summary(batteries_df, best_battery, a, b, r2, dyno_filename,
                  num_motors, empty_weight, n_outliers,
                  max_total_thrust, max_total_current):
    sep = "=" * 60
    print(sep)
    print("        DRONE BATTERY FLIGHT TIME OPTIMIZER")
    print(sep)
    print()
    print("ASSUMPTIONS:")
    print(f"  Number of Motors         : {num_motors}")
    print(f"  Empty Drone Weight       : {empty_weight:.2f} lbs")
    print(f"  Dynamometer File         : {dyno_filename}")
    print(f"  Trendline Model          : I = {a:.3f} * T^{b:.3f}  (R\u00b2 = {r2:.4f})")
    print(f"  Outliers Removed         : {n_outliers} point{'s' if n_outliers != 1 else ''}")
    print(f"  Min Thrust:Weight Ratio  : {THRUST_WEIGHT_RATIO:.1f}:1 required  "
          f"(max total thrust = {max_total_thrust:.3f} lbs)")
    print(f"  Max Burst Current        : {max_total_current:.2f} A total — "
          f"must not exceed battery's max continuous discharge rating")
    print()
    print("RESULTS:")
    header = (f"  {'#':>2}  {'Battery Name':<48}  {'Cap':>6}  {'Wt':>6}  "
              f"{'Hover A':>8}  {'Time':>7}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for i, row in batteries_df.iterrows():
        is_best = best_battery is not None and row["Battery Name"] == best_battery["Battery Name"]
        marker = " *" if is_best else "  "
        flag = "  [FAIL]" if not row["Safe"] else ""
        print(f"{marker}{i+1:>2}  {row['Battery Name']:<48}  "
              f"{int(row['Capacity (mAh)']):>6}  "
              f"{row['Weight (lbs)']:>6.3f}  "
              f"{row['Total_Current_A']:>7.2f}A  "
              f"{row['Flight_Time_Min']:>6.1f}m{flag}")
    print()
    if best_battery is not None:
        print("OPTIMAL BATTERY:")
        print(f"  Name     : {best_battery['Battery Name']}")
        print(f"  Capacity : {int(best_battery['Capacity (mAh)'])} mAh")
        print(f"  Weight   : {best_battery['Weight (lbs)']:.3f} lbs")
        print(f"  Price    : ${best_battery['Price ($)']:.0f}")
        print(f"  Flight   : {best_battery['Flight_Time_Min']:.1f} minutes")
        print(f"  Hover    : {best_battery['Total_Current_A']:.1f} A total")
    else:
        print("OPTIMAL BATTERY: None — all batteries failed safety checks.")
    print(sep)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    batteries_df = load_battery_data(INPUT_DATA_DIR)
    dyno_df, dyno_filename = load_dynamometer_data(INPUT_DATA_DIR)

    df_clean, n_outliers = remove_outliers(dyno_df)
    a, b, r2 = fit_trendline(df_clean)

    results_df = calculate_flight_times(batteries_df, a, b,
                                        DRONE_EMPTY_WEIGHT_LBS, NUM_MOTORS)

    results_df, max_total_thrust, max_total_current = apply_safety_checks(
        results_df, df_clean, NUM_MOTORS, DRONE_EMPTY_WEIGHT_LBS
    )

    safe_df = results_df[results_df["Safe"]]
    best_battery = safe_df.loc[safe_df["Flight_Time_Min"].idxmax()] if not safe_df.empty else None

    print_summary(results_df, best_battery, a, b, r2, dyno_filename,
                  NUM_MOTORS, DRONE_EMPTY_WEIGHT_LBS, n_outliers,
                  max_total_thrust, max_total_current)

    txt_path = save_results_txt(results_df, best_battery, a, b, r2, dyno_filename,
                                NUM_MOTORS, DRONE_EMPTY_WEIGHT_LBS, n_outliers,
                                max_total_thrust, max_total_current, OUTPUT_DATA_DIR)
    print(f"\nResults saved to: {txt_path}")

    png_path = plot_results(results_df, best_battery, OUTPUT_DATA_DIR)
    print(f"Plot saved to:    {png_path}")


if __name__ == "__main__":
    main()
