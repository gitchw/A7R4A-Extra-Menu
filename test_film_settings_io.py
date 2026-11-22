"""Actual atomic RAM-config publication, including refusal of partial writers."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_film_settings_snapshot import Settings

ROOT=Path(__file__).resolve().parent


class FilmSettingsIoTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT/'camera/film_settings_io.c').exists(),
                        'background RAM settings publisher is not implemented')
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'film.ini'
        lib=Path(self.directory.name)/'io.dylib'
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC',
            str(ROOT/'camera/film_settings_io.c'),str(ROOT/'camera/film_menu.c'),
            '-o',str(lib)],check=True)
        self.publish=ctypes.CDLL(str(lib)).film_settings_publish
        self.publish.argtypes=[ctypes.c_char_p,ctypes.POINTER(Settings)]
        self.publish.restype=ctypes.c_int

    def write(self,s):return self.publish(bytes(self.path),ctypes.byref(s))

    def test_real_worker_configuration_is_complete_then_replaced(self):
        self.assertEqual(self.write(Settings(1,3,2,1)),0)
        self.assertEqual(self.path.read_text(),'ENABLED=1\nSTOCK=KODAK_GOLD\nGRAIN=MEDIUM\nHALATION=ON\n')
        self.assertEqual(self.write(Settings(0,4,0,0)),0)
        self.assertEqual(self.path.read_text(),'ENABLED=0\nSTOCK=TRI_X_400\nGRAIN=OFF\nHALATION=OFF\n')
        self.assertFalse(self.path.with_suffix('.ini.new').exists())

    def test_invalid_settings_leave_the_existing_config_untouched(self):
        self.path.write_text('prior complete config')
        self.assertNotEqual(self.write(Settings(1,12,2,1)),0)
        self.assertEqual(self.path.read_text(),'prior complete config')
        self.assertFalse(self.path.with_suffix('.ini.new').exists())

    def test_existing_temporary_writer_is_never_clobbered(self):
        self.path.write_text('prior complete config')
        temp=self.path.with_suffix('.ini.new');temp.write_text('other incomplete writer')
        self.assertNotEqual(self.write(Settings(1,3,2,1)),0)
        self.assertEqual(self.path.read_text(),'prior complete config')
        self.assertEqual(temp.read_text(),'other incomplete writer')


if __name__=='__main__':unittest.main(verbosity=2)
