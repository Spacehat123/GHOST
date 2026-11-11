// GHOST Parametric Model: sensor_enclosure
// Generated at 2026-11-10 21:27:17
width = 50;
length = 80;
height = 25;
wall = 2;

difference() {
    cube([length, width, height], center=true);
    translate([0, 0, wall])
        cube([length - (wall * 2), width - (wall * 2), height], center=true);
}
