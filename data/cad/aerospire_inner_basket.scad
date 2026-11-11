// ====================================================================
// AeroSpire Self-Watering Planter - Inner Basket (Grow Pot)
// Parametric Engineering CAD - OpenSCAD CSG
// ====================================================================
$fn = 64;

pot_top_dia = 134;
pot_base_dia = 104;
pot_height = 95;
wall = 2.0;
lip_dia = 144;
lip_height = 4;
wick_cone_top_dia = 36;
wick_cone_base_dia = 26;
wick_cone_depth = 32;

module pot_shell() {
    union() {
        // Main truncated cone body
        translate([0, 0, wick_cone_depth])
            cylinder(h = pot_height, d1 = pot_base_dia, d2 = pot_top_dia);
        // Top resting lip
        translate([0, 0, pot_height + wick_cone_depth - lip_height])
            cylinder(h = lip_height, d1 = pot_top_dia, d2 = lip_dia);
        // Central wicking cone (plunges into water reservoir)
        cylinder(h = wick_cone_depth + 1, d1 = wick_cone_base_dia, d2 = wick_cone_top_dia);
    }
}

module pot_interior() {
    union() {
        // Main soil chamber
        translate([0, 0, wick_cone_depth + wall])
            cylinder(h = pot_height + 2, d1 = pot_base_dia - (wall * 2), d2 = pot_top_dia - (wall * 2));
        // Wicking cone hollow interior
        translate([0, 0, wall])
            cylinder(h = wick_cone_depth + 2, d1 = wick_cone_base_dia - (wall * 2), d2 = wick_cone_top_dia - (wall * 2));
    }
}

module aeration_slots() {
    // 16 radial aeration slots for root-pruning air-gap
    for (i = [0:15]) {
        rotate([0, 0, i * (360 / 16)])
            translate([pot_base_dia / 2 - 2, -1.2, wick_cone_depth + 10])
                cube([8, 2.4, 25]);
    }
}

module finger_grip_notches() {
    // 2 ergonomic finger lift cutouts on rim
    for (ang = [0, 180]) {
        rotate([0, 0, ang])
            translate([pot_top_dia / 2 - 1, -10, pot_height + wick_cone_depth - lip_height - 1])
                cube([10, 20, lip_height + 2]);
    }
}

module inner_basket() {
    difference() {
        pot_shell();
        pot_interior();
        aeration_slots();
        finger_grip_notches();
    }
}

color([0.85, 0.55, 0.25, 0.95])
    inner_basket();
