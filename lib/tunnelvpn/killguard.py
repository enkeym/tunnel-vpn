import contextlib
import ctypes as ct
import ipaddress
import os
import sys
import threading
import uuid

from . import buildconfig, paths, tunnels


class GUID(ct.Structure):
    _fields_ = [("a", ct.c_uint32), ("b", ct.c_uint16),
                ("c", ct.c_uint16), ("d", ct.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class Blob(ct.Structure):
    _fields_ = [("size", ct.c_uint32), ("data", ct.c_void_p)]


class Display(ct.Structure):
    _fields_ = [("name", ct.c_wchar_p), ("description", ct.c_wchar_p)]


class Data(ct.Union):
    _fields_ = [("pointer", ct.c_void_p), ("u64", ct.c_uint64),
                ("u32", ct.c_uint32), ("u16", ct.c_uint16), ("u8", ct.c_uint8)]


class Value(ct.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ct.c_uint32), ("data", Data)]


class Condition(ct.Structure):
    _fields_ = [("key", GUID), ("match", ct.c_uint32), ("value", Value)]


class Action(ct.Structure):
    _fields_ = [("type", ct.c_uint32), ("key", GUID)]


class Context(ct.Union):
    _fields_ = [("raw", ct.c_uint64), ("key", GUID)]


class Filter(ct.Structure):
    _fields_ = [("key", GUID), ("display", Display), ("flags", ct.c_uint32),
                ("provider", ct.c_void_p), ("provider_data", Blob),
                ("layer", GUID), ("sublayer", GUID), ("weight", Value),
                ("count", ct.c_uint32), ("conditions", ct.POINTER(Condition)),
                ("action", Action), ("context", Context),
                ("reserved", ct.c_void_p), ("id", ct.c_uint64),
                ("effective_weight", Value)]


class Sublayer(ct.Structure):
    _fields_ = [("key", GUID), ("display", Display), ("flags", ct.c_uint32),
                ("provider", ct.c_void_p), ("provider_data", Blob),
                ("weight", ct.c_uint16)]


class V4Mask(ct.Structure):
    _fields_ = [("address", ct.c_uint32), ("mask", ct.c_uint32)]


SUBLAYER = GUID.parse("86d19615-41b5-4af0-b246-b62765076542")
LAYERS = ("c38d57d1-05a7-4c33-904f-7fbceee60e82",
          "4a72393b-319f-44bc-84c3-ba54dcb3b6b4")
APP = "d78e1e87-8644-4ea5-9437-d809ecefc971"
INTERFACE = "4cd62a49-59c3-4969-b7f3-bda5d32890a4"
FLAGS = "632ce23b-5167-435c-86d7-e903684aa80c"
REMOTE = "b235ae9a-1d64-49b8-a44c-5ff3d9095045"
PORT = "c35a604d-d22b-4e1a-91b4-68f674ee674b"
PROTOCOL = "3971ef2b-623e-4f9a-8cb1-6e79b806b9a7"
PERMIT = 0x1002
BLOCK = 0x1001
PERSISTENT = 1
LOCK = threading.RLock()


def _condition(key, kind, value, match=0):
    data = Data()
    if kind in (1, 2, 3):
        setattr(data, {1: "u8", 2: "u16", 3: "u32"}[kind], value)
    else:
        data.pointer = ct.cast(ct.pointer(value), ct.c_void_p).value
    result = Condition(GUID.parse(key), match, Value(kind, data))
    result._keep = value
    return result


class Engine:
    def __enter__(self):
        self.api = ct.WinDLL("fwpuclnt.dll")
        specs = {
            "FwpmEngineOpen0": [ct.c_wchar_p, ct.c_uint32, ct.c_void_p,
                                ct.c_void_p, ct.POINTER(ct.c_void_p)],
            "FwpmEngineClose0": [ct.c_void_p],
            "FwpmTransactionBegin0": [ct.c_void_p, ct.c_uint32],
            "FwpmTransactionCommit0": [ct.c_void_p],
            "FwpmTransactionAbort0": [ct.c_void_p],
            "FwpmSubLayerAdd0": [ct.c_void_p, ct.POINTER(Sublayer), ct.c_void_p],
            "FwpmSubLayerDeleteByKey0": [ct.c_void_p, ct.POINTER(GUID)],
            "FwpmFilterAdd0": [ct.c_void_p, ct.POINTER(Filter), ct.c_void_p,
                               ct.POINTER(ct.c_uint64)],
            "FwpmFilterDeleteByKey0": [ct.c_void_p, ct.POINTER(GUID)],
            "FwpmFilterCreateEnumHandle0": [ct.c_void_p, ct.c_void_p,
                                           ct.POINTER(ct.c_void_p)],
            "FwpmFilterEnum0": [ct.c_void_p, ct.c_void_p, ct.c_uint32,
                                ct.POINTER(ct.POINTER(ct.POINTER(Filter))),
                                ct.POINTER(ct.c_uint32)],
            "FwpmFilterDestroyEnumHandle0": [ct.c_void_p, ct.c_void_p],
            "FwpmGetAppIdFromFileName0": [ct.c_wchar_p, ct.POINTER(ct.POINTER(Blob))],
            "FwpmFreeMemory0": [ct.c_void_p],
        }
        for name, args in specs.items():
            func = getattr(self.api, name)
            func.argtypes, func.restype = args, ct.c_uint32
        self.api.FwpmFreeMemory0.restype = None
        self.handle = ct.c_void_p()
        self.call("FwpmEngineOpen0", None, 10, None, None, ct.byref(self.handle))
        return self

    def __exit__(self, *exc):
        self.call("FwpmEngineClose0", self.handle)

    def call(self, name, *args, allowed=()):
        result = getattr(self.api, name)(*args)
        if result and result not in allowed:
            raise OSError(result, f"{name}: 0x{result:08x}")

    @contextlib.contextmanager
    def transaction(self):
        self.call("FwpmTransactionBegin0", self.handle, 0)
        try:
            yield
            self.call("FwpmTransactionCommit0", self.handle)
        except BaseException:
            self.call("FwpmTransactionAbort0", self.handle)
            raise

    def clear(self):
        enum = ct.c_void_p()
        self.call("FwpmFilterCreateEnumHandle0", self.handle, None, ct.byref(enum))
        keys = []
        try:
            while True:
                entries = ct.POINTER(ct.POINTER(Filter))()
                count = ct.c_uint32()
                self.call("FwpmFilterEnum0", self.handle, enum, 256,
                          ct.byref(entries), ct.byref(count))
                try:
                    for i in range(count.value):
                        rule = entries[i].contents
                        if bytes(rule.sublayer) == bytes(SUBLAYER):
                            keys.append(GUID.from_buffer_copy(bytes(rule.key)))
                finally:
                    self.api.FwpmFreeMemory0(ct.byref(entries))
                if count.value < 256:
                    break
        finally:
            self.call("FwpmFilterDestroyEnumHandle0", self.handle, enum)
        for key in keys:
            self.call("FwpmFilterDeleteByKey0", self.handle, ct.byref(key))
        self.call("FwpmSubLayerDeleteByKey0", self.handle, ct.byref(SUBLAYER),
                  allowed=(0x80320007,))

    def sublayer(self):
        rule = Sublayer()
        rule.key, rule.display = SUBLAYER, Display("TunnelVPN kill switch", None)
        rule.flags, rule.weight = PERSISTENT, 0x100
        self.call("FwpmSubLayerAdd0", self.handle, ct.byref(rule), None)

    @contextlib.contextmanager
    def app(self, filename):
        blob = ct.POINTER(Blob)()
        self.call("FwpmGetAppIdFromFileName0", filename, ct.byref(blob))
        try:
            yield _condition(APP, 12, blob.contents)
        finally:
            self.api.FwpmFreeMemory0(ct.byref(blob))

    def luid(self, index):
        api = ct.WinDLL("iphlpapi.dll").ConvertInterfaceIndexToLuid
        api.argtypes = [ct.c_uint32, ct.POINTER(ct.c_uint64)]
        api.restype = ct.c_uint32
        value = ct.c_uint64()
        result = api(index, ct.byref(value))
        if result:
            raise OSError(result, "ConvertInterfaceIndexToLuid")
        return value

    def add(self, layer, action, *conditions):
        rule = Filter()
        rule.key = GUID.from_buffer_copy(uuid.uuid4().bytes_le)
        rule.display = Display("TunnelVPN kill switch", None)
        rule.flags, rule.layer, rule.sublayer = PERSISTENT, GUID.parse(layer), SUBLAYER
        rule.weight = Value(1, Data(u8=15 if action == PERMIT else 0))
        rule.count = len(conditions)
        rule.conditions = (Condition * len(conditions))(*conditions)
        rule.action.type = action
        filter_id = ct.c_uint64()
        self.call("FwpmFilterAdd0", self.handle, ct.byref(rule), None, ct.byref(filter_id))


def apply(data, tun_index=None):
    main = tunnels.main_tunnel(data)
    enabled = bool(data["kill_switch"] and main and main["enabled"])
    with LOCK, Engine() as engine, engine.transaction():
        engine.clear()
        if not enabled:
            return
        engine.sublayer()
        luid = engine.luid(tun_index) if tun_index is not None else None
        for layer in LAYERS:
            if os.path.isfile(paths.SINGBOX):
                with engine.app(os.path.abspath(paths.SINGBOX)) as app:
                    engine.add(layer, PERMIT, app)
            for filename in main["exclude_apps"]:
                if os.path.isfile(filename):
                    with engine.app(os.path.abspath(filename)) as app:
                        engine.add(layer, PERMIT, app)
            with engine.app(sys.executable) as app:
                engine.add(layer, PERMIT, app, _condition(PORT, 2, 53),
                           _condition(PROTOCOL, 1, 17))
            engine.add(layer, PERMIT, _condition(FLAGS, 3, 1, match=6))
            if luid is not None:
                engine.add(layer, PERMIT, _condition(INTERFACE, 4, luid))
            if layer == LAYERS[0]:
                for prefix in buildconfig.LOCAL_NETS:
                    net = ipaddress.IPv4Network(prefix)
                    mask = V4Mask(int(net.network_address), int(net.netmask))
                    engine.add(layer, PERMIT, _condition(REMOTE, 0x100, mask),
                               _condition(PORT, 2, 53, match=9))
            engine.add(layer, BLOCK)


def release():
    with LOCK, Engine() as engine, engine.transaction():
        engine.clear()
