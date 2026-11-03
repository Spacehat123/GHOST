"""Unit tests for reconnaissance photo filters and review parsing."""
import unittest
from ghost.workers.recon_scout import is_venue_photo, format_author_name


class TestReconFilters(unittest.TestCase):

    def test_reject_profile_avatars_path(self):
        """Rejects paths containing /a/ and /a-/ avatar patterns."""
        self.assertFalse(is_venue_photo("https://lh3.googleusercontent.com/a-/ALV-UjV_9w8e7d6c5=s64-c"))
        self.assertFalse(is_venue_photo("https://lh3.googleusercontent.com/a/ACg8ocLQWERTY12345=s96-c"))
        self.assertFalse(is_venue_photo("https://lh3.googleusercontent.com/a-/AOh14Ghp..."))

    def test_reject_avatar_dimensions_and_keywords(self):
        """Rejects small square avatar dimension parameters and avatar keywords."""
        self.assertFalse(is_venue_photo("https://lh3.googleusercontent.com/some_path=s32-c"))
        self.assertFalse(is_venue_photo("https://lh3.googleusercontent.com/some_path=s64-c"))
        self.assertFalse(is_venue_photo("https://lh3.googleusercontent.com/some_path=s128-c"))
        self.assertFalse(is_venue_photo("https://lh5.googleusercontent.com/user_avatar_123.jpg"))
        self.assertFalse(is_venue_photo("https://lh5.googleusercontent.com/user_profile.png"))
        self.assertFalse(is_venue_photo("https://lh5.googleusercontent.com/p/AF1QipM4-mo"))

    def test_accept_venue_place_photos(self):
        """Accepts legitimate place and venue photos."""
        self.assertTrue(is_venue_photo("https://lh5.googleusercontent.com/p/AF1QipNWX4R019283=w400-h300-k-no"))
        self.assertTrue(is_venue_photo("https://lh3.googleusercontent.com/gps-cs-s/AB598_random_id=w800-h600"))
        self.assertTrue(is_venue_photo("https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?auto=format&fit=crop&w=1600&q=85"))

    def test_format_author_name(self):
        """Formats author names as 'FirstName L.' and drops synthetic authors."""
        self.assertEqual(format_author_name("Marcus Holloway"), "Marcus H.")
        self.assertEqual(format_author_name("Sarah"), "Sarah")
        self.assertEqual(format_author_name("Jean-Luc Picard"), "Jean-Luc P.")
        self.assertIsNone(format_author_name("Verified Guest"))
        self.assertIsNone(format_author_name("Google User"))
        self.assertIsNone(format_author_name(None))
        self.assertIsNone(format_author_name(""))


if __name__ == "__main__":
    unittest.main()
