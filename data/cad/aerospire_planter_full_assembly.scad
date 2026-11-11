// ====================================================================
// AeroSpire Self-Watering Planter - Complete System Assembly
// Parametric Engineering CAD - OpenSCAD CSG
// ====================================================================
use <aerospire_reservoir_base.scad>
use <aerospire_inner_basket.scad>
use <aerospire_capillary_column.scad>
use <aerospire_water_level_gauge.scad>

$fn = 48;

// Display mode: "assembled", "exploded", or "cutaway"
mode = "cutaway";

module full_system() {
    if (mode == "assembled") {
        color([0.22, 0.48, 0.75, 0.85])
            base_assembly();
        translate([0, 0, 22])
            color([0.88, 0.58, 0.28, 0.95])
                inner_basket();
        translate([0, 0, 22])
            color([0.25, 0.78, 0.45, 0.9])
                capillary_cartridge();
        translate([48, 0, 15])
            color([0.9, 0.2, 0.2, 1.0])
                gauge_assembly();
    } else if (mode == "exploded") {
        color([0.22, 0.48, 0.75, 0.85])
            base_assembly();
        translate([0, 0, 160])
            color([0.88, 0.58, 0.28, 0.95])
                inner_basket();
        translate([0, 0, 90])
            color([0.25, 0.78, 0.45, 0.9])
                capillary_cartridge();
        translate([70, 0, 60])
            color([0.9, 0.2, 0.2, 1.0])
                gauge_assembly();
    } else if (mode == "cutaway") {
        difference() {
            union() {
                color([0.22, 0.48, 0.75, 0.85])
                    base_assembly();
                translate([0, 0, 22])
                    color([0.88, 0.58, 0.28, 0.95])
                        inner_basket();
                translate([0, 0, 22])
                    color([0.25, 0.78, 0.45, 0.9])
                        capillary_cartridge();
                translate([48, 0, 15])
                    color([0.9, 0.2, 0.2, 1.0])
                        gauge_assembly();
            }
            translate([0, 0, -5])
                cube([150, 150, 200]);
        }
    }
}

full_system();
