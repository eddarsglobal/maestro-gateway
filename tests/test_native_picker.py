import unittest

from maestro_gateway.server import native_picker_script


class NativePickerTests(unittest.TestCase):
    def test_folder_script(self):
        script = native_picker_script("folder")
        self.assertIn("choose folder", script)
        self.assertIn("POSIX path", script)

    def test_file_script(self):
        script = native_picker_script("file")
        self.assertIn("choose file", script)
        self.assertIn("POSIX path", script)

    def test_invalid_kind(self):
        with self.assertRaises(ValueError):
            native_picker_script("disk")


if __name__ == "__main__":
    unittest.main()
