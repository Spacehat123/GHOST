"""Generate engineering graphics, BOM breakdowns, and performance curves for the AeroSpire Planter."""

import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

OUT_DIR = Path("data/cad")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 1. Cost & Scaling Curve Chart
def generate_cost_scaling_chart():
    quantities = ["1,000 units\n(Pilot/Soft Launch)", "10,000 units\n(Batch 1 Production)", "100,000 units\n(Scaled Mass Market)"]
    tooling_amort = [18.50, 1.85, 0.19]
    raw_materials = [1.82, 1.45, 1.15]
    molding_labor = [2.20, 1.10, 0.65]
    hardware_sub = [1.20, 0.75, 0.52]  # float gauge + porous media
    packaging = [1.10, 0.65, 0.42]

    x = np.arange(len(quantities))
    width = 0.45

    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    
    p1 = ax.bar(x, raw_materials, width, label="Raw Materials (PP & EPS)", color="#2b5c8f")
    p2 = ax.bar(x, molding_labor, width, bottom=raw_materials, label="Injection Molding Cycle/Labor", color="#3a7ebf")
    p3 = ax.bar(x, hardware_sub, width, bottom=np.array(raw_materials)+np.array(molding_labor), label="Capillary & Float Hardware", color="#4ba3e3")
    p4 = ax.bar(x, packaging, width, bottom=np.array(raw_materials)+np.array(molding_labor)+np.array(hardware_sub), label="Kraft Packaging & Manual", color="#7ec4f5")
    p5 = ax.bar(x, tooling_amort, width, bottom=np.array(raw_materials)+np.array(molding_labor)+np.array(hardware_sub)+np.array(packaging), label="Tooling Amortization ($18.5k NRE)", color="#e07a5f")

    totals = [round(sum(vals), 2) for vals in zip(raw_materials, molding_labor, hardware_sub, packaging, tooling_amort)]
    for i, total in enumerate(totals):
        ax.text(i, total + 0.4, f"${total:.2f} COGS", ha="center", va="bottom", fontweight="bold", fontsize=11)

    ax.set_ylabel("Unit COGS ($ USD)", fontsize=12)
    ax.set_title("AeroSpire Planter — Manufacturing Cost Scaling Curve", fontsize=14, fontweight="bold", pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(quantities, fontsize=11)
    ax.legend(loc="upper right", frameon=True, fontsize=10)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    ax.set_ylim(0, 30)

    plt.tight_layout()
    chart_path = OUT_DIR / "aerospire_cost_scaling_curve.png"
    plt.savefig(chart_path)
    plt.close()
    print(f"Saved: {chart_path}")

# 2. Soil Moisture & Root Zone Dissolved Oxygen Comparison
def generate_hydrology_performance_chart():
    days = np.linspace(0, 21, 200)

    # Conventional Pot: Wet-dry rollercoaster
    moisture_conv = 80 * np.exp(-days / 5) * (1 + 0.2 * np.sin(days * 1.5))
    moisture_conv = np.clip(moisture_conv, 15, 95)

    # Standard Bottom-Reservoir Wick Pot: High moisture but stagnant root zone hypoxia
    moisture_std_wick = 72 - 0.8 * days
    oxygen_std_wick = 2.5 + 0.5 * np.sin(days / 3)  # hypoxic (< 3.0 mg/L)

    # AeroSpire Dual-Chamber: Balanced capillary equilibrium (48-55%) + continuous root aeration
    moisture_aerospire = 55 - 0.7 * days
    oxygen_aerospire = 7.2 - 0.05 * days  # normoxic, healthy root aeration (> 6.5 mg/L)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), dpi=150, sharex=True)

    # Subplot 1: Volumetric Soil Moisture Content (%)
    ax1.plot(days, moisture_conv, "--", color="#888888", label="Traditional Pot (Manual Watering)", linewidth=1.8)
    ax1.plot(days, moisture_std_wick, "-.", color="#d95f02", label="Standard Bottom-Reservoir (Stagnant Water)", linewidth=2.0)
    ax1.plot(days, moisture_aerospire, "-", color="#1b9e77", label="AeroSpire (Sub-Irrigation Capillary Balance)", linewidth=2.5)
    ax1.axhspan(40, 60, color="#1b9e77", alpha=0.15, label="Optimal Plant Moisture Band (40–60%)")
    ax1.set_ylabel("Soil Moisture Content (% VMC)", fontsize=11)
    ax1.set_title("AeroSpire Hydrology vs. Conventional Planters (21-Day Autonomous Run)", fontsize=13, fontweight="bold")
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, linestyle="--", alpha=0.4)
    ax1.set_ylim(0, 100)

    # Subplot 2: Root Zone Dissolved Oxygen (mg/L)
    ax2.plot(days, [8.0]*len(days), ":", color="#999999", alpha=0.5)
    ax2.plot(days, oxygen_std_wick, "-.", color="#d95f02", label="Standard Sub-Irrigation (Hypoxia Risk: < 4 mg/L)", linewidth=2.0)
    ax2.plot(days, oxygen_aerospire, "-", color="#1b9e77", label="AeroSpire Air-Gap Circulation (Root-Pruning Aeration)", linewidth=2.5)
    ax2.axhspan(0, 4.0, color="#d95f02", alpha=0.15, label="Hypoxia / Root Rot Danger Zone (< 4 mg/L)")
    ax2.set_xlabel("Days Elapsed After Full Refill (1.2L Reservoir)", fontsize=11)
    ax2.set_ylabel("Root Zone Dissolved O₂ (mg/L)", fontsize=11)
    ax2.legend(loc="center right", fontsize=9)
    ax2.grid(True, linestyle="--", alpha=0.4)
    ax2.set_ylim(0, 10)

    plt.tight_layout()
    chart_path = OUT_DIR / "aerospire_hydrology_performance.png"
    plt.savefig(chart_path)
    plt.close()
    print(f"Saved: {chart_path}")

if __name__ == "__main__":
    generate_cost_scaling_chart()
    generate_hydrology_performance_chart()
