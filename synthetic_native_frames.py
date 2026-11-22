"""Hand-authored protocol frames for offline tests; no camera recordings or dumps."""

SYNTHETIC_ROOT_VERSION = (
    b"MprFileIf : FFirst [pathname]\r\n"
    b"[MSL][0x1A2BDCD0]: MSLF_WD_Version = 0104\r\n"
    b"10061115\r\nkind : DIR\r\nfile length = 0\r\n"
    b"modification time = 10 : 00 : 00\r\nFindFirst OK.\r\n\r\nMON>"
)

SYNTHETIC_FNEXT_UNMOUNTED = (
    b"MprFileIf : FNext ['c' to cancel]\r\n"
    b"[MMGR][AVI] ## ERROR(MMGR/FileIf):0x0EF10001 ## "
    b"avimmgr/AviMMgrFileIf.cpp(696) FindNext()\r\n"
    b"[AVIMMGR][MS1]GetDeviceInfo : ER_SEQUENCE  DEVICE Unmounted\r\n"
    b"[MMGR][AVI] ## ERROR(MMGR):0x31000007 ## "
    b"avimmgr/AviMMgrCmd.cpp(16755) GetDeviceInfo()\r\n"
    b"ec = 0x0000100B\r\n\r\nMON>"
)

SYNTHETIC_INTERLEAVED_FNEXT_METADATA = (
    b"MprFileIf : FNext ['c' to cancel]\r\n"
    b"A6400002.JPG\r\nkind : FILE\r\nfile length = 14\r\n"
    b"FindNext OK.\r\n\r\nMON>"
)
