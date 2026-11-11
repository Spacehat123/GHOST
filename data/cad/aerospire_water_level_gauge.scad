// ====================================================================
// AeroSpire Self-Watering Planter - Floating Water Level Gauge
// Parametric Engineering CAD - OpenSCAD CSG
// ====================================================================
$fn = 64;

float_dia = 12.5;
float_height = 28;
stem_dia = 3.2;
stem_height = 135;
cap_top_dia = 19;
cap_height = 20;

module float_bobber() {
    // Sealed buoyant cylinder
    cylinder(h = float_height, d = float_dia);
}

module indicator_stem() {
    // Vertical stem attached to float
    translate([0, 0, float_height - 2])
        union() {
            cylinder(h = stem_height, d = stem_dia);
            // Calibration rings (FULL, OPTIMAL, REFILL)
            translate([0, 0, stem_height - 15])
                cylinder(h = 3, d = stem_dia + 1.2);
            translate([0, 0, stem_height - 45])
                cylinder(h = 3, d = stem_dia + 1.2);
            translate([0, 0, stem_height - 75])
                cylinder(h = 3, d = stem_dia + 1.2);
        }
}

module protective_sight_cap() {
    // Transparent retaining top cap
    translate([35, 0, 0])
        difference() {
            cylinder(h = cap_height, d = cap_top_dia);
            translate([0, 0, 2])
                cylinder(h = cap_height + 1, d = cap_top_dia - 3);
            // Stem clearance hole
            translate([0, 0, -1])
                cylinder(h = 4, d = stem_dia + 1.5);
            // Air breather notch
            translate([-1, cap_top_dia/2 - 3, cap_height - 5])
                cube([2, 4, 6]);
        }
}

module gauge_assembly() {
    union() {
        color([0.9, 0.2, 0.2, 1.0]) {
            float_bobber();
            indicator_stem();
        }
        color([0.9, 0.9, 0.9, 0.7])
            protective_sight_cap();
    }
}

gauge_assembly();
