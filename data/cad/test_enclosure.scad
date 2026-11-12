// GHOST Parametric Model: test_enclosure
// Generated at 2026-11-12 14:07:17
width = 40;
length = 60;
height = 15;
wall = 2.0;

difference() {
    cube([length, width, height], center=true);
    translate([0, 0, wall])
        cube([length - (wall * 2), width - (wall * 2), height], center=true);
}
