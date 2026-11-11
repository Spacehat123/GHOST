// ====================================================================
// AeroSpire Self-Watering Planter - Capillary Wicking Column
// Parametric Engineering CAD - OpenSCAD CSG
// ====================================================================
$fn = 64;

column_height = 42;
top_dia = 25;
base_dia = 21;
wall = 1.8;
mesh_hole_dia = 2.2;

module column_body() {
    difference() {
        cylinder(h = column_height, d1 = base_dia, d2 = top_dia);
        translate([0, 0, wall])
            cylinder(h = column_height, d1 = base_dia - (wall * 2), d2 = top_dia - (wall * 2));
        
        // Lateral capillary intake slits
        for (z = [8, 18, 28]) {
            for (r = [0, 90, 180, 270]) {
                rotate([0, 0, r])
                    translate([base_dia / 2 - 2, -1.0, z])
                        cube([6, 2.0, 6]);
            }
        }
        
        // Base perforations
        for (x = [-5, 0, 5]) {
            for (y = [-5, 0, 5]) {
                if (sqrt(x*x + y*y) < (base_dia / 2 - 2)) {
                    translate([x, y, -1])
                        cylinder(h = wall + 2, d = mesh_hole_dia);
                }
            }
        }
    }
}

module top_retention_flange() {
    translate([0, 0, column_height - 2])
        difference() {
            cylinder(h = 2, d = top_dia + 3);
            translate([0, 0, -0.5])
                cylinder(h = 3, d = top_dia - (wall * 2));
        }
}

module capillary_cartridge() {
    union() {
        column_body();
        top_retention_flange();
    }
}

color([0.3, 0.75, 0.45, 0.9])
    capillary_cartridge();
