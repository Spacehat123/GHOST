// ====================================================================
// AeroSpire Self-Watering Planter - Reservoir Base
// Parametric Engineering CAD - OpenSCAD CSG
// ====================================================================
$fn = 64;

// Primary Parameters (mm)
top_dia = 140;
base_dia = 120;
total_height = 150;
wall = 2.5;
rim_height = 8;
shelf_height = 75;
gauge_dia = 16;
gauge_offset = 48;

module reservoir_outer() {
    cylinder(h = total_height, d1 = base_dia, d2 = top_dia);
}

module reservoir_inner() {
    translate([0, 0, wall])
        cylinder(h = total_height, d1 = base_dia - (wall * 2), d2 = top_dia - (wall * 2));
}

module water_level_channel() {
    // Integrated guide tube for floating level gauge
    translate([gauge_offset, 0, wall])
        difference() {
            cylinder(h = total_height - wall, d = gauge_dia + (wall * 1.5));
            translate([0, 0, -1])
                cylinder(h = total_height + 2, d = gauge_dia);
            // Inflow slot at bottom of gauge channel
            translate([-gauge_dia/2, -wall, 0])
                cube([gauge_dia, wall * 2, 25]);
        }
}

module refill_spout() {
    // Angled refill spout lip
    translate([-gauge_offset - 5, 0, total_height - 15])
        rotate([0, -25, 0])
            difference() {
                cylinder(h = 22, d = 26);
                translate([0, 0, -1])
                    cylinder(h = 24, d = 21);
            }
}

module internal_shelf_supports() {
    // 3 internal shelf support lugs for inner basket
    for (a = [60, 180, 300]) {
        rotate([0, 0, a])
            translate([(base_dia/2) - 8, -4, wall])
                cube([6, 8, shelf_height - wall]);
    }
}

module base_assembly() {
    union() {
        difference() {
            reservoir_outer();
            reservoir_inner();
            // Refill access notch on rim
            translate([-gauge_offset - 10, -12, total_height - 18])
                cube([20, 24, 25]);
        }
        water_level_channel();
        internal_shelf_supports();
        refill_spout();
    }
}

color([0.2, 0.45, 0.7, 0.9])
    base_assembly();
