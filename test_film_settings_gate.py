"""Verify real menu choices produce the worker's argv, and OFF yields no job."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_film_settings_snapshot import Settings

ROOT=Path(__file__).resolve().parent

class Job(ctypes.Structure):
    _fields_=[('preset',ctypes.c_char_p),('grain',ctypes.c_char*2),
              ('halation',ctypes.c_char*2)]

class FilmSettingsGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()
    def setUp(self):
        self.assertTrue((ROOT/'camera/film_settings_gate.c').exists(),
                        'worker selection gate is not implemented')
        lib=Path(self.directory.name)/'gate.dylib'
        if not lib.exists():
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                str(ROOT/'camera/film_settings_gate.c'),str(ROOT/'camera/film_menu.c'),
                '-o',str(lib)],check=True)
        self.api=ctypes.CDLL(str(lib)).film_settings_job
        self.api.argtypes=[ctypes.POINTER(Job),ctypes.POINTER(Settings)]
        self.api.restype=ctypes.c_int
    def test_off_clears_any_previous_worker_plan(self):
        job=Job(b'previous',b'3',b'1');settings=Settings(0,3,2,1)
        self.assertEqual(self.api(ctypes.byref(job),ctypes.byref(settings)),0)
        self.assertIsNone(job.preset);self.assertEqual(job.grain,b'')
    def test_all_five_presets_and_effect_arguments_match_worker(self):
        names=[b'portra_400',b'cinestill_800t',b'fuji_provia',b'kodak_gold',b'tri_x_400',
               b'classic_chrome',b'agfa_ultra_100',b'polaroid_669',b'fuji_acros',
               b'polaroid_px680',b'kodak_ektar_100',b'ilford_hp5']
        for i,name in enumerate(names):
            for grain in range(4):
                for halation in range(2):
                    job=Job();settings=Settings(1,i,grain,halation)
                    self.assertEqual(self.api(ctypes.byref(job),ctypes.byref(settings)),1)
                    self.assertEqual((job.preset,job.grain,job.halation),
                                     (name,str(grain).encode(),str(halation).encode()))
    def test_invalid_choice_never_leaves_enabled_plan(self):
        for settings in (Settings(2,0,0,0),Settings(1,12,0,0),Settings(1,0,4,0),Settings(1,0,0,2)):
            job=Job(b'previous',b'3',b'1')
            self.assertEqual(self.api(ctypes.byref(job),ctypes.byref(settings)),-1)
            self.assertIsNone(job.preset)

if __name__=='__main__':unittest.main(verbosity=2)
