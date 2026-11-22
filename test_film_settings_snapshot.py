"""Read a synthetic pinned-layout UI snapshot; reject stale or inconsistent settings."""
import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent


class Settings(ctypes.Structure):
    _fields_=[(n,ctypes.c_int) for n in ('enabled','stock','grain','halation')]


class FilmSettingsSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def setUp(self):
        self.assertTrue((ROOT/'camera/film_settings_snapshot.c').exists(),
                        'background settings snapshot decoder is not implemented')
        lib=Path(self.directory.name)/'snapshot.dylib'
        if not lib.exists():
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                str(ROOT/'camera/film_settings_snapshot.c'),str(ROOT/'camera/film_menu.c'),
                '-o',str(lib)],check=True)
        self.api=ctypes.CDLL(str(lib)).film_settings_snapshot_get
        self.api.argtypes=[ctypes.POINTER(Settings),ctypes.c_void_p,ctypes.c_void_p,
                           ctypes.c_uint,ctypes.c_uint]
        self.api.restype=ctypes.c_int
        # Synthetic state: no firmware bytes or camera memory dump.
        data=bytearray(1120)
        for offset,value in [(0,0),(4,3),(8,2),(12,1),(0x68,0x956f0),
                             (0xc0,380),(0xc4,1000),(0xc8,1100),
                             (0x18c,1),(0x190,1)]:
            struct.pack_into('<I',data,offset,value)
        self.original=bytes(data)

    def call(self,a,b=None,now=None):
        b=a if b is None else b
        now=struct.unpack_from('<I',a,0xc8)[0]+100 if now is None else now
        out=Settings(9,9,9,9)
        first=ctypes.create_string_buffer(bytes(a));second=ctypes.create_string_buffer(bytes(b))
        result=self.api(ctypes.byref(out),first,second,len(a),now&0xffffffff)
        return result,(out.enabled,out.stock,out.grain,out.halation)

    def test_synthetic_off_kodak_gold_snapshot(self):
        self.assertEqual(self.call(self.original),(0,(0,3,2,1)))

    def test_all_preset_choices_and_on_off(self):
        for enabled in (0,1):
            for stock in range(12):
                data=bytearray(self.original);struct.pack_into('<4I',data,0,enabled,stock,2,1)
                self.assertEqual(self.call(data),(0,(enabled,stock,2,1)))

    def test_changing_settings_or_wrong_identity_never_publish(self):
        changed=bytearray(self.original);struct.pack_into('<I',changed,4,4)
        self.assertEqual(self.call(self.original,changed),(2,(9,9,9,9)))
        for offset,bad in [(0,2),(4,12),(8,4),(12,2),(0x68,0),(0xc0,381),(0x18c,0)]:
            data=bytearray(self.original);struct.pack_into('<I',data,offset,bad)
            self.assertEqual(self.call(data),(-1,(9,9,9,9)),offset)
        self.assertEqual(self.call(self.original[:100],now=1),(-1,(9,9,9,9)))

    def test_stopped_expired_or_missing_heartbeat_is_disabled(self):
        data=bytearray(self.original);struct.pack_into('<I',data,0x190,0)
        self.assertEqual(self.call(data),(1,(9,9,9,9)))
        last=struct.unpack_from('<I',self.original,0xc8)[0]
        self.assertEqual(self.call(self.original,now=last+2001),(1,(9,9,9,9)))
        data=bytearray(self.original);started=struct.unpack_from('<I',data,0xc4)[0]
        struct.pack_into('<I',data,0xc8,started+600000)
        self.assertEqual(self.call(data,now=started+600000),(1,(9,9,9,9)))

    def test_monotonic_wrap_does_not_invent_staleness(self):
        data=bytearray(self.original)
        struct.pack_into('<II',data,0xc4,0xffffff00,0xfffffff0)
        self.assertEqual(self.call(data,now=20),(0,(0,3,2,1)))


if __name__=='__main__':unittest.main(verbosity=2)
