"""File and directory operations with mandatory tactical authorization."""
# Actions in desktop_control:
# - move_file: target="source/path", args="dest/path"
# - copy_file: target="source/path", args="dest/path"
# - delete_file: target="path/to/delete"
#
# RULE: Any file moving, renaming, organizing, or deletion requires
# user consent via the Tactical Authorization modal in the GUI!
