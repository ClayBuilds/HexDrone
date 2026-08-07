# Drone Battery Flight Time Optimizer

Picks the LiPo battery that gives a multirotor the longest hover time without
violating its thrust or current limits.

The script fits a power-law curve to propeller dynamometer data (thrust vs.
current), uses that curve to predict the hover current draw for every candidate
battery, then ranks the batteries by estimated flight time and flags any that
fail a safety check.

## How it works

1. **Load inputs** — battery spec table (`.xlsx`) and a propeller dynamometer
   sweep (`.txt`).
2. **Reject outliers** — dead-band rows (zero thrust or zero current) are
   dropped, a provisional power law is fit, and points more than 2.5σ from the
   fit are removed.
3. **Fit the trendline** — `I = a · T^b`, where `T` is thrust per motor (lbs)
   and `I` is current per motor (amps). R² is reported.
4. **Estimate flight time** — for each battery:

   ```
   total_weight        = empty_weight + battery_weight
   thrust_per_motor    = total_weight / num_motors
   current_per_motor   = a · thrust_per_motor^b
   total_current       = current_per_motor · num_motors
   flight_time (min)   = (capacity_mAh / 1000) / total_current · 60
   ```

5. **Safety checks** — a battery is marked `FAIL` if either check trips:
   - **Thrust:weight** — max measured thrust × motor count must be at least
     2.0× the loaded weight.
   - **Max continuous discharge** — max measured current × motor count must not
     exceed the battery's rated continuous discharge.
6. **Report** — console summary, a text report, and a 3D scatter plot.

The recommended battery is the one with the longest flight time among those
that pass both checks.

## Requirements

Python 3 with:

```bash
pip install pandas numpy scipy matplotlib openpyxl
```

Plotting uses the `TkAgg` backend and calls `plt.show()`, so a display (and
Tk support, e.g. `python3-tk`) is needed for the interactive window. The PNG is
written either way.

## Usage

```bash
python3 drone_optimizer.py
```

Paths are relative to the working directory, so run it from the project root.

## Configuration

Edit the constants at the top of `drone_optimizer.py`:

| Constant | Default | Meaning |
| --- | --- | --- |
| `NUM_MOTORS` | `6` | Number of motors on the airframe |
| `DRONE_EMPTY_WEIGHT_LBS` | `5` | All-up weight excluding the battery |
| `THRUST_WEIGHT_RATIO` | `2.0` | Minimum required thrust:weight ratio |
| `INPUT_DATA_DIR` | `"Input Data"` | Where inputs are read from |
| `OUTPUT_DATA_DIR` | `"Output Data"` | Where results are written |

## Input files

### `Input Data/battery list.xlsx`

Sheet name must be **`MaxAmps 3S LiPo Batteries`**, with these columns:

- `Battery Name`
- `Capacity (mAh)`
- `Weight (lbs)`
- `Price ($)`
- `Max Continuous Discharge (Amps)`

### `Input Data/dynamometer_*.txt`

CSV with `#` comment lines, from a single-motor propeller dynamometer sweep.
The first matching file is used. Required columns: `THRUST_LBS`,
`CURRENT_AMPS` (`PWM_US` is present but unused).

```
# Propeller Dynamometer Test
# Recorded: 2026-03-07T20:30:48.797796
# Points:   101
# Columns:  PWM_US, THRUST_LBS, CURRENT_AMPS
#
PWM_US,THRUST_LBS,CURRENT_AMPS
1000,0.0000,0.0000
...
```

## Output files

- `Output Data/flight_time_results.txt` — parameters used, per-battery table
  with pass/fail reasons, and the recommended battery.
- `Output Data/flight_time_optimizer.png` — 3D scatter of capacity vs. battery
  weight vs. flight time. Blue circles pass, red squares fail thrust:weight,
  red triangles fail the current rating, and the green marker is the pick.

Example console/report output:

```
  Trendline Model          : I = 5.105 * T^1.541  (R² = 0.9963)
  Min Thrust:Weight Ratio  : 2.0:1  (max total thrust = 11.942 lbs)
  Max Burst Current        : 88.13 A total (14.69 A/motor x 6 motors)

OPTIMAL BATTERY:
  Name     : MAXAMPS LiPo 5600mAh 3S2P 11.1v Battery Pack
  Capacity : 5600 mAh
  Weight   : 0.869 lbs
  Flight   : 11.4 minutes
  Hover    : 29.6 A total
```

## Assumptions and limitations

- Hover only — no allowance for climb, wind, or maneuvering.
- Constant current draw for the whole discharge; voltage sag and the usable
  capacity reserve (typically ~80% depth of discharge) are not modeled.
- All motors are assumed identical to the one on the dynamometer, and thrust is
  assumed evenly split between them.
- Treat the results as a comparison between batteries, not an absolute
  endurance prediction.
