import ctypes
import re

from . import paths

MAX_LINK = 16384
_LINK = re.compile(r"^happ://crypt(?:[2-5])?/[A-Za-z0-9+/=_-]+$")
_LIBRARY = None


def is_link(value):
    return isinstance(value, str) and value.lower().startswith("happ://")


def check_link(value):
    if not isinstance(value, str) or len(value) > MAX_LINK or not _LINK.fullmatch(value):
        raise ValueError("ожидается ссылка happ://crypt... из Happ")
    return value


def _library():
    global _LIBRARY
    if _LIBRARY is None:
        try:
            library = ctypes.CDLL(paths.HAPPWN)
        except OSError:
            raise ValueError("модуль расшифровки Happ не найден в сборке") from None
        library.happ_decrypt.argtypes = [ctypes.c_char_p]
        library.happ_decrypt.restype = ctypes.c_void_p
        library.happ_free.argtypes = [ctypes.c_void_p]
        library.happ_free.restype = None
        _LIBRARY = library
    return _LIBRARY


def decrypt(link):
    link = check_link(link)
    library = _library()
    result = library.happ_decrypt(link.encode("ascii"))
    if not result:
        raise ValueError("не удалось расшифровать Happ-ссылку")
    try:
        return ctypes.string_at(result).decode("utf-8")
    finally:
        library.happ_free(result)
