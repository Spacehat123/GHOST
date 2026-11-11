// GHOST Parametric Model: api_test_bracket
// Generated at 2026-11-11 12:33:18
width = 30;
length = 45;
height = 10;
wall = 1.5;

difference() {
    cube([length, width, height], center=true);
    translate([0, 0, wall])
        cube([length - (wall * 2), width - (wall * 2), height], center=true);
}
